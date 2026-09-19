"""Agent framework: interfaces, context and result types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.agents.llm import LLMClient
from app.agents.state import AnalysisState
from app.config.settings import Settings
from app.rag.retriever import HybridRetriever
from app.security.access import Principal
from app.security.tool_policy import Capability, ToolPolicy


class AgentStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AgentResult:
    status: AgentStatus = AgentStatus.OK
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class AgentContext:
    principal: Principal
    settings: Settings
    llm: LLMClient
    data: Any  # AgentData (read-only facade); typed loosely to avoid an import cycle
    policy: ToolPolicy
    retriever: HybridRetriever | None = None
    state: AnalysisState = field(default_factory=AnalysisState)
    contract_id: UUID | None = None
    version_id: UUID | None = None
    params: dict[str, Any] = field(default_factory=dict)
    cancel: Callable[[], bool] = lambda: False
    emit: Callable[[str, dict[str, Any]], None] = lambda kind, payload: None


class Agent(ABC):
    name: str = "agent"
    title: str = "Agent"
    description: str = ""

    def need(self, ctx: AgentContext, capability: Capability) -> None:
        """Every privileged step is authorised against the agent's capability grant."""
        ctx.policy.authorize(self.name, capability)

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult: ...
