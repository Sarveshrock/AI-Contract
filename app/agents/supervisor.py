"""Supervisor Agent: classifies the task, plans the workflow, coordinates specialised agents.

The supervisor never touches the database. Agents read through a read-only facade and return results
in shared state; persistence is a separate, narrow service that runs *after* the workflow.
Task types are an enum, so document text can never introduce a new workflow or tool.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.agents.amendment_agent import AmendmentIntelligenceAgent
from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.document_agent import DocumentIntelligenceAgent
from app.agents.extraction_agent import ContractExtractionAgent
from app.agents.obligation_agent import ObligationIntelligenceAgent
from app.agents.qa_agent import EvidenceQAAgent
from app.agents.risk_agent import RiskTriageAgent
from app.agents.state import AnalysisState
from app.agents.temporal_agent import TemporalReasoningAgent
from app.core.errors import AIResponseError, AIUnavailableError, ContractLensError, TransientError, WorkflowError
from app.core.logging import get_logger
from app.models.enums import PARTIAL_ROLES, DocumentRole, RunStatus, RunType
from app.security.tool_policy import Capability, ToolPolicy

log = get_logger(__name__)


@dataclass
class Task:
    run_type: RunType
    contract_id: UUID | None = None
    version_id: UUID | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanStep:
    agent: str
    depends_on: list[str]
    required: bool
    description: str


@dataclass
class WorkflowPlan:
    run_type: RunType
    steps: list[PlanStep]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {"run_type": self.run_type.value, "rationale": self.rationale,
                "steps": [{"agent": s.agent, "depends_on": s.depends_on, "required": s.required, "description": s.description} for s in self.steps]}


@dataclass
class StepRecord:
    agent: str
    status: str
    started_at: str
    seconds: float
    attempts: int
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "status": self.status, "started_at": self.started_at, "seconds": round(self.seconds, 3), "attempts": self.attempts,
                "summary": self.summary, "warnings": self.warnings, "error": self.error}


@dataclass
class SupervisorResult:
    status: RunStatus
    plan: WorkflowPlan
    steps: list[StepRecord]
    state: AnalysisState
    error: str | None = None

    @property
    def quality(self) -> float | None:
        return self.state.qa.score if self.state.qa else None

    @property
    def needs_review(self) -> bool:
        return self.status is RunStatus.NEEDS_REVIEW


def default_agents() -> dict[str, Agent]:
    agents: list[Agent] = [DocumentIntelligenceAgent(), ContractExtractionAgent(), ObligationIntelligenceAgent(), TemporalReasoningAgent(),
                           AmendmentIntelligenceAgent(), RiskTriageAgent(), EvidenceQAAgent()]
    return {a.name: a for a in agents}


class Supervisor:
    name = "supervisor"

    def __init__(self, agents: dict[str, Agent] | None = None, policy: ToolPolicy | None = None, *, max_attempts: int = 3) -> None:
        self.agents = agents or default_agents()
        self.policy = policy or ToolPolicy()
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------ classify & plan
    def classify(self, task: Task) -> RunType:
        self.policy.authorize(self.name, Capability.COMPUTE)
        if not isinstance(task.run_type, RunType):
            raise WorkflowError(f"unknown task type {task.run_type!r}", user_message="Unsupported analysis task.")
        if task.run_type not in (RunType.INGEST_ANALYSIS, RunType.COMPARE_VERSIONS):
            raise WorkflowError(f"{task.run_type} is not an agent workflow", user_message="This task is handled by a dedicated service, not the agent workflow.")
        return task.run_type

    def plan(self, task: Task) -> WorkflowPlan:
        run_type = self.classify(task)
        role: DocumentRole = task.params.get("document_role", DocumentRole.ORIGINAL)
        has_base = bool(task.params.get("base_version_id"))
        S = PlanStep
        if run_type is RunType.COMPARE_VERSIONS:
            return WorkflowPlan(run_type, [S("amendment_intelligence", [], True, "Compare the two versions clause by clause and by extracted terms")],
                                "Explicit comparison requested between two versions.")
        if role in PARTIAL_ROLES:
            steps = [
                S("document_intelligence", [], True, "Profile the amendment document"),
                S("amendment_intelligence", ["document_intelligence"], True, "Interpret the amendment's instructions with evidence"),
                S("obligation_intelligence", ["document_intelligence"], False, "Extract obligations introduced or changed by the amendment"),
                S("temporal_reasoning", ["obligation_intelligence"], False, "Compute deadlines using the base agreement's dates"),
                S("risk_triage", ["temporal_reasoning"], False, "Flag review signals arising from the amendment"),
                S("evidence_qa", ["risk_triage"], True, "Validate evidence, dates and consistency"),
            ]
            return WorkflowPlan(run_type, steps, f"Document role '{role.value}' modifies a base agreement; contract-level extraction is skipped.")
        steps = [
            S("document_intelligence", [], True, "Profile structure, OCR quality and related documents"),
            S("contract_extraction", ["document_intelligence"], True, "Extract parties, dates and key terms with evidence"),
            S("obligation_intelligence", ["document_intelligence"], True, "Extract obligations, triggers and time requirements"),
            S("temporal_reasoning", ["contract_extraction", "obligation_intelligence"], True, "Compute deadlines deterministically with traces"),
        ]
        if has_base:
            steps.append(S("amendment_intelligence", ["temporal_reasoning"], False, "Compare against the previous version"))
        steps += [S("risk_triage", ["temporal_reasoning"], False, "Flag review signals"),
                  S("evidence_qa", ["risk_triage"], True, "Validate evidence, dates and consistency; route low quality to review")]
        return WorkflowPlan(run_type, steps, "Full contract analysis for a new document version.")

    # ------------------------------------------------------------------ execute
    def execute(self, plan: WorkflowPlan, ctx: AgentContext, *, on_step=None) -> SupervisorResult:
        records: list[StepRecord] = []
        failed_required: str | None = None
        degraded = False
        done: dict[str, str] = {}
        for step in plan.steps:
            if ctx.cancel():
                return SupervisorResult(RunStatus.CANCELLED, plan, records, ctx.state, "Cancelled by user.")
            blocked = [d for d in step.depends_on if done.get(d) in ("failed", "skipped-dependency")]
            agent = self.agents.get(step.agent)
            started = datetime.now(timezone.utc)
            if agent is None:
                raise WorkflowError(f"agent {step.agent} not registered")
            if blocked and step.required:
                rec = StepRecord(step.agent, "skipped", started.isoformat(), 0.0, 0, error=f"Skipped because {', '.join(blocked)} failed.")
                done[step.agent] = "skipped-dependency"
            elif blocked:
                rec = StepRecord(step.agent, "skipped", started.isoformat(), 0.0, 0, error=f"Skipped because {', '.join(blocked)} failed.")
                done[step.agent] = "skipped-dependency"
                degraded = True
            else:
                rec = self._run_step(agent, ctx, step, started)
                done[step.agent] = "failed" if rec.status == "failed" else "ok"
                if rec.status == "failed":
                    if step.required:
                        failed_required = step.agent
                    else:
                        degraded = True
                elif rec.status == "warning":
                    degraded = degraded or bool(rec.warnings and step.agent in ("obligation_intelligence", "contract_extraction"))
            records.append(rec)
            if on_step:
                on_step(rec)
            ctx.emit("agent.step", rec.to_dict())
            if failed_required:
                error = next((r.error for r in records if r.agent == failed_required), None)
                return SupervisorResult(RunStatus.FAILED, plan, records, ctx.state, error or f"{failed_required} failed.")
        needs_review = degraded or (ctx.state.qa.needs_review if ctx.state.qa else False)
        return SupervisorResult(RunStatus.NEEDS_REVIEW if needs_review else RunStatus.SUCCEEDED, plan, records, ctx.state)

    def _run_step(self, agent: Agent, ctx: AgentContext, step: PlanStep, started: datetime) -> StepRecord:
        t0 = time.monotonic()
        attempts = 0
        last_error: str | None = None
        while attempts < self.max_attempts:
            attempts += 1
            ctx.emit("agent.start", {"agent": agent.name, "attempt": attempts})
            try:
                result: AgentResult = agent.run(ctx)
                return StepRecord(agent.name, result.status.value, started.isoformat(), time.monotonic() - t0, attempts, result.summary, result.warnings, result.error)
            except (TransientError, AIResponseError) as exc:
                last_error = exc.user_message
                log.warning("agent %s attempt %d failed: %s", agent.name, attempts, type(exc).__name__)
                if isinstance(exc, AIResponseError) and attempts >= 2:
                    break
            except AIUnavailableError as exc:
                return StepRecord(agent.name, "failed", started.isoformat(), time.monotonic() - t0, attempts, error=exc.user_message)
            except ContractLensError as exc:
                return StepRecord(agent.name, "failed", started.isoformat(), time.monotonic() - t0, attempts, error=exc.user_message)
            except Exception as exc:  # noqa: BLE001 - never let one agent crash the run silently
                log.exception("agent %s crashed", agent.name)
                return StepRecord(agent.name, "failed", started.isoformat(), time.monotonic() - t0, attempts, error=f"Unexpected error in {agent.title}: {type(exc).__name__}")
        return StepRecord(agent.name, "failed", started.isoformat(), time.monotonic() - t0, attempts, error=last_error or "The step failed after retries.")


_ = AgentStatus
