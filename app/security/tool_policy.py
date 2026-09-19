"""Tool / capability authorisation for agents and external actions.

Agents never receive database handles. They receive a narrow read-only data facade and may use only
the capabilities granted to them here. External side effects (calendar export, webhooks) go through
:class:`ToolExecutor`, which enforces: allowlisted tool → permission → enabled integration →
verified source data → explicit confirmation → audit entry.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.errors import ConfirmationRequired, ToolAuthorizationError
from app.core.logging import get_logger
from app.security.access import Permission, Principal

log = get_logger(__name__)


class Capability(StrEnum):
    LLM_STRUCTURED = "llm.structured"
    RETRIEVAL_READ = "retrieval.read"
    DATA_READ = "data.read"
    COMPUTE = "compute"
    # never granted to agents:
    DATA_WRITE = "data.write"
    EXTERNAL_ACTION = "external.action"


AGENT_CAPABILITIES: dict[str, frozenset[Capability]] = {
    "supervisor": frozenset({Capability.COMPUTE}),
    "document_intelligence": frozenset({Capability.DATA_READ, Capability.COMPUTE}),
    "contract_extraction": frozenset({Capability.DATA_READ, Capability.LLM_STRUCTURED, Capability.RETRIEVAL_READ}),
    "obligation_intelligence": frozenset({Capability.DATA_READ, Capability.LLM_STRUCTURED}),
    "temporal_reasoning": frozenset({Capability.DATA_READ, Capability.COMPUTE}),
    "amendment_intelligence": frozenset({Capability.DATA_READ, Capability.LLM_STRUCTURED, Capability.COMPUTE}),
    "risk_triage": frozenset({Capability.DATA_READ, Capability.LLM_STRUCTURED, Capability.COMPUTE}),
    "evidence_qa": frozenset({Capability.DATA_READ, Capability.COMPUTE}),
    "grounded_copilot": frozenset({Capability.RETRIEVAL_READ, Capability.LLM_STRUCTURED, Capability.DATA_READ}),
}
FORBIDDEN_FOR_AGENTS = frozenset({Capability.DATA_WRITE, Capability.EXTERNAL_ACTION})


class ToolPolicy:
    def authorize(self, agent: str, capability: Capability) -> None:
        if capability in FORBIDDEN_FOR_AGENTS:
            raise ToolAuthorizationError(f"{agent} requested forbidden capability {capability}", user_message="An AI agent attempted an action it is not permitted to take. It was blocked.")
        allowed = AGENT_CAPABILITIES.get(agent)
        if allowed is None or capability not in allowed:
            raise ToolAuthorizationError(f"{agent} is not allowed {capability}", user_message="An AI agent attempted an action it is not permitted to take. It was blocked.")


# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permission: Permission
    requires_confirmation: bool = True
    external: bool = True
    handler: Callable[..., dict[str, Any]] | None = None


@dataclass
class ToolExecutor:
    """Only tools registered here can run. Contract text can never add or select tools."""

    principal: Principal
    audit: Any  # AuditService
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def invoke(self, name: str, *, confirmed: bool, verified_source: bool, args: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = self.tools.get(name)
        if spec is None or spec.handler is None:
            self.audit.record("tool.denied", "tool", None, tool=name, reason="not allowlisted")
            raise ToolAuthorizationError(f"tool {name!r} is not allowlisted", user_message="That action is not an allowed tool.")
        self.principal.require(spec.permission)
        if spec.external and not verified_source:
            self.audit.record("tool.denied", "tool", None, tool=name, reason="unverified source")
            raise ToolAuthorizationError("unverified source data", user_message="External actions can only use data a human has confirmed. Review and confirm the item first.")
        if spec.requires_confirmation and not confirmed:
            raise ConfirmationRequired(f"{name} requires confirmation", user_message="This action needs your explicit confirmation.")
        self.audit.record("tool.invoke", "tool", None, tool=name, args=_summarise(args or {}))
        try:
            result = spec.handler(**(args or {}))
        except Exception as exc:
            self.audit.record("tool.failed", "tool", None, tool=name, error=type(exc).__name__)
            raise
        self.audit.record("tool.completed", "tool", None, tool=name)
        return result


def _summarise(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:120]) for k, v in args.items()}
