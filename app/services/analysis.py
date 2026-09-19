"""Runs agent workflows, logs them, persists results, scores risk and raises alerts."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.agents.base import AgentContext
from app.agents.data import AgentData
from app.agents.llm import LLMClient, MeteredLLM
from app.agents.state import AnalysisState
from app.agents.supervisor import StepRecord, Supervisor, SupervisorResult, Task
from app.config.risk import load_risk_config
from app.config.settings import Settings
from app.core.errors import ContractLensError
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import AnalysisRun
from app.models.enums import AnalysisStatus, RunStatus, RunType
from app.rag.retriever import HybridRetriever
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.security.redaction import redact_text
from app.services.alerts import AlertService
from app.services.audit import AuditService
from app.services.persistence import AnalysisPersistence, PersistSummary
from app.services.risk import RiskService
from app.security.tool_policy import ToolPolicy

log = get_logger(__name__)


@dataclass
class AnalysisOutcome:
    run_id: UUID
    status: RunStatus
    quality: float | None
    needs_review: bool
    error: str | None = None
    summary: PersistSummary | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)


class RunRecorder:
    """Keeps the ``analysis_runs`` row current so the Agent Runs screen can show live progress."""

    def __init__(self, repos: Repositories, run: AnalysisRun) -> None:
        self._r = repos
        self.run = run
        self.steps: list[dict[str, Any]] = []

    def step(self, rec: StepRecord) -> None:
        self.steps.append(rec.to_dict())
        try:
            self._r.runs.update(self.run.id, steps=self.steps)
        except ContractLensError:
            log.warning("could not update run steps")

    def finish(self, status: RunStatus, *, quality: float | None, error: str | None, result: dict[str, Any], llm: MeteredLLM | None) -> None:
        self._r.runs.update(
            self.run.id, status=status, quality_score=quality, error=redact_text(error) if error else None, result_summary=result, steps=self.steps,
            finished_at=datetime.now(timezone.utc), model=llm.model if llm else None, prompt_tokens=llm.prompt_tokens if llm else 0,
            completion_tokens=llm.completion_tokens if llm else 0,
        )


class AnalysisService:
    def __init__(self, principal: Principal, repos: Repositories, settings: Settings, llm: LLMClient, retriever: HybridRetriever | None,
                 audit: AuditService, risk: RiskService, alerts: AlertService, supervisor: Supervisor | None = None) -> None:
        self._p, self._r, self._settings, self._llm = principal, repos, settings, llm
        self._retriever, self._audit, self._risk, self._alerts = retriever, audit, risk, alerts
        self._supervisor = supervisor or Supervisor(max_attempts=settings.agent_max_attempts)
        self._persist = AnalysisPersistence(repos)

    # ------------------------------------------------------------------
    def _context(self, llm: MeteredLLM, contract_id: UUID, version_id: UUID, params: dict[str, Any], cancel: Callable[[], bool],
                 emit: Callable[[str, dict], None]) -> AgentContext:
        org = self._r.organizations.get(self._p.org_id)
        params = {**params, "risk_config": load_risk_config(org.settings if org else {}), "holidays": (org.settings if org else {}).get("holidays", []),
                  "today": params.get("today") or date.today()}
        return AgentContext(principal=self._p, settings=self._settings, llm=llm, data=AgentData(self._r), policy=ToolPolicy(), retriever=self._retriever,
                            state=AnalysisState(), contract_id=contract_id, version_id=version_id, params=params, cancel=cancel, emit=emit)

    def analyze_version(self, contract_id: UUID | str, version_id: UUID | str, *, is_new_contract: bool = False, today: date | None = None,
                        cancel: Callable[[], bool] = lambda: False, on_event: Callable[[str, dict], None] | None = None) -> AnalysisOutcome:
        self._p.require(Permission.AI_RUN)
        contract = self._r.contracts.require(contract_id)
        version = self._r.versions.require(version_id)
        base = self._r.versions.get(version.supersedes_version_id) if version.supersedes_version_id else None
        params: dict[str, Any] = {"document_role": version.document_role, "base_version_id": str(base.id) if base else None,
                                  "base_snapshot": (base.extraction if base else {}) or {}, "today": today}
        task = Task(RunType.INGEST_ANALYSIS, contract.id, version.id, params)
        plan = self._supervisor.plan(task)
        chunks = self._r.chunks.count([F.eq("contract_version_id", str(version.id))])
        run = self._r.runs.add(AnalysisRun(
            org_id=self._p.org_id, contract_id=contract.id, contract_version_id=version.id, run_type=RunType.INGEST_ANALYSIS, status=RunStatus.RUNNING,
            workflow_plan=plan.to_dict(), input_summary=f"version {version.version_number} ({version.document_role.value}), {chunks} passage(s)",
            started_at=datetime.now(timezone.utc), requested_by=self._p.user_id, model=self._llm.model,
        ))
        recorder = RunRecorder(self._r, run)
        llm = MeteredLLM(self._llm)
        self._r.contracts.update(contract.id, analysis_status=AnalysisStatus.RUNNING)
        emit = on_event or (lambda kind, payload: None)
        ctx = self._context(llm, contract.id, version.id, params, cancel, emit)
        try:
            result: SupervisorResult = self._supervisor.execute(plan, ctx, on_step=recorder.step)
        except Exception as exc:  # noqa: BLE001
            log.exception("supervisor crashed")
            recorder.finish(RunStatus.FAILED, quality=None, error=f"{type(exc).__name__}", result={}, llm=llm)
            self._r.contracts.update(contract.id, analysis_status=AnalysisStatus.FAILED)
            raise
        if result.status in (RunStatus.FAILED, RunStatus.CANCELLED):
            recorder.finish(result.status, quality=None, error=result.error, result={"steps": len(result.steps)}, llm=llm)
            self._r.contracts.update(contract.id, analysis_status=AnalysisStatus.FAILED if result.status is RunStatus.FAILED else AnalysisStatus.PENDING)
            self._audit.record("analysis.failed", "contract", contract.id, run_id=str(run.id), error=result.error)
            return AnalysisOutcome(run.id, result.status, None, False, result.error, None, recorder.steps)
        try:
            summary = self._persist.persist(run_id=run.id, contract=contract, version=version, state=result.state, role=version.document_role,
                                            needs_review=result.needs_review, is_new_contract=is_new_contract)
        except Exception as exc:  # noqa: BLE001
            log.exception("persistence failed")
            recorder.finish(RunStatus.FAILED, quality=result.quality, error="Results could not be saved: " + type(exc).__name__, result={}, llm=llm)
            self._r.contracts.update(contract.id, analysis_status=AnalysisStatus.FAILED)
            raise
        score = self._risk.score_contract(contract.id)
        try:
            self._alerts.scan(today)
        except ContractLensError:
            log.warning("alert scan failed after analysis")
        qa = result.state.qa
        recorder.finish(result.status, quality=result.quality, error=None, llm=llm, result={
            "persisted": summary.__dict__, "business_risk": score.business, "extraction_uncertainty": score.extraction,
            "qa_issues": [{"kind": i.kind, "severity": i.severity.value, "subject": i.subject, "message": i.message} for i in (qa.issues[:15] if qa else [])],
            "amendment": result.state.amendment.summary if result.state.amendment else None,
            "warnings": [w for s in recorder.steps for w in s.get("warnings", [])][:30],
        })
        self._audit.record("analysis.completed", "contract", contract.id, run_id=str(run.id), status=result.status.value, quality=result.quality)
        return AnalysisOutcome(run.id, result.status, result.quality, result.needs_review, None, summary, recorder.steps)

    def compare_versions(self, contract_id: UUID | str, base_version_id: UUID | str, new_version_id: UUID | str,
                         on_event: Callable[[str, dict], None] | None = None) -> AnalysisOutcome:
        """On-demand comparison of two stored versions (no re-extraction)."""
        self._p.require(Permission.AI_RUN)
        contract = self._r.contracts.require(contract_id)
        base = self._r.versions.require(base_version_id)
        new = self._r.versions.require(new_version_id)
        params = {"document_role": new.document_role, "base_version_id": str(base.id), "base_snapshot": base.extraction or {}}
        task = Task(RunType.COMPARE_VERSIONS, contract.id, new.id, params)
        plan = self._supervisor.plan(task)
        run = self._r.runs.add(AnalysisRun(org_id=self._p.org_id, contract_id=contract.id, contract_version_id=new.id, run_type=RunType.COMPARE_VERSIONS,
                                           status=RunStatus.RUNNING, workflow_plan=plan.to_dict(), input_summary=f"v{base.version_number} → v{new.version_number}",
                                           started_at=datetime.now(timezone.utc), requested_by=self._p.user_id, model=self._llm.model))
        recorder = RunRecorder(self._r, run)
        llm = MeteredLLM(self._llm)
        ctx = self._context(llm, contract.id, new.id, params, lambda: False, on_event or (lambda k, p: None))
        result = self._supervisor.execute(plan, ctx, on_step=recorder.step)
        am = result.state.amendment
        recorder.finish(result.status, quality=None, error=result.error, llm=llm,
                        result={"summary": am.summary if am else None, "clause_changes": len(am.clause_diffs) if am else 0})
        if am and result.status is not RunStatus.FAILED:
            self._persist.persist_amendment(run_id=run.id, contract=contract, state=result.state)
        self._audit.record("analysis.compare", "contract", contract.id, base=str(base.id), new=str(new.id))
        return AnalysisOutcome(run.id, result.status, None, False, result.error, None, recorder.steps)
