"""Aggregates for the Command Center and Risk Observatory. Only real data; nothing is synthesised."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config.risk import RiskConfig
from app.database.store import F
from app.models.entities import AnalysisRun, Contract
from app.models.enums import (
    ContractStatus,
    DeadlineStatus,
    FindingStatus,
    IndexingStatus,
    ObligationStatus,
    ReviewCaseStatus,
    ReviewStatus,
    Severity,
    ValidationStatus,
)
from app.repositories import Repositories
from app.services.renewals import RenewalService
from app.services.risk import score_findings


@dataclass
class DashboardData:
    active_contracts: int = 0
    total_contracts: int = 0
    renewals_90d: int = 0
    pending_obligations: int = 0
    overdue_items: int = 0
    unresolved_findings: int = 0
    review_queue: int = 0
    processing: dict[str, int] = field(default_factory=dict)
    lifecycle: dict[str, int] = field(default_factory=dict)
    obligations_by_category: dict[str, int] = field(default_factory=dict)
    deadlines_by_month: dict[str, int] = field(default_factory=dict)
    review_status: dict[str, int] = field(default_factory=dict)
    activity: dict[str, int] = field(default_factory=dict)
    recent_runs: list[AnalysisRun] = field(default_factory=list)
    recent_changes: list[dict[str, Any]] = field(default_factory=list)
    top_risk: list[dict[str, Any]] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    horizon: dict[str, int] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.total_contracts == 0


class AnalyticsService:
    def __init__(self, repos: Repositories, risk_config: RiskConfig | None = None) -> None:
        self._r = repos
        self._cfg = risk_config or RiskConfig()

    def dashboard(self, today: date | None = None) -> DashboardData:
        today = today or date.today()
        d = DashboardData()
        contracts = self._r.contracts.list([F.is_null("deleted_at")])
        d.total_contracts = len(contracts)
        d.active_contracts = sum(1 for c in contracts if c.status in (ContractStatus.ACTIVE, ContractStatus.EXPIRING))
        d.lifecycle = dict(Counter(c.status.value for c in contracts))
        obligations = [o for o in self._r.live_obligations() if o.review_status is not ReviewStatus.REJECTED]
        open_ob = [o for o in obligations if o.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS)]
        d.pending_obligations = len(open_ob)
        d.obligations_by_category = dict(Counter(o.category.value for o in obligations))
        d.review_status = dict(Counter(o.review_status.value for o in obligations))
        deadlines = [x for x in self._r.live_deadlines([F.eq("status", DeadlineStatus.OPEN)]) if x.validation_status is not ValidationStatus.REJECTED]
        ob_open_ids = {o.id for o in open_ob}
        auto = {c.id for c in contracts if c.auto_renews and c.renewal_term_months}
        d.overdue_items = sum(1 for x in deadlines if x.due_date and x.due_date < today and (x.obligation_id is None or x.obligation_id in ob_open_ids)
                              and not (x.category.value == "expiration" and x.contract_id in auto))
        months: dict[str, int] = {}
        for i in range(12):
            y, m = divmod(today.year * 12 + today.month - 1 + i, 12)
            months[f"{y}-{m + 1:02d}"] = 0
        for x in deadlines:
            if x.due_date and (k := x.due_date.strftime("%Y-%m")) in months:
                months[k] += 1
        d.deadlines_by_month = months
        findings = self._r.findings.list()
        d.unresolved_findings = sum(1 for f in findings if f.status is FindingStatus.OPEN)
        d.review_queue = self._r.reviews.count([F.in_("status", [ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW])])
        docs = self._r.documents.list()
        d.processing = dict(Counter(x.indexing_status.value for x in docs))
        d.processing.update({f"analysis:{k}": v for k, v in Counter(c.analysis_status.value for c in contracts).items()})
        radar = RenewalService(self._r)
        rows = radar.radar(today)
        d.renewals_90d = sum(1 for r in rows if (r.days_to_notice is not None and -10_000 < r.days_to_notice <= 90) or (r.days_to_notice is None and r.days_to_end is not None and 0 <= r.days_to_end <= 90))
        d.horizon = radar.horizon(today)
        runs = self._r.runs.list(order_by=[("created_at", True)], limit=8)
        d.recent_runs = runs
        # activity: audit + runs over the last 30 days
        start = today - timedelta(days=29)
        activity = {(start + timedelta(days=i)).isoformat(): 0 for i in range(30)}
        for a in self._r.audit.list([F.gte("created_at", datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc))], limit=5000):
            k = a.created_at.date().isoformat()
            if k in activity:
                activity[k] += 1
        d.activity = activity
        d.recent_changes = [{"summary": a.summary or "Version comparison", "contract_id": str(a.contract_id), "when": a.created_at, "changes": len(a.changes)}
                            for a in self._r.amendments.list(order_by=[("created_at", True)], limit=5)]
        by_contract: dict[Any, list] = defaultdict(list)
        for f in findings:
            by_contract[f.contract_id].append(f)
        titles = {c.id: c for c in contracts}
        scored = []
        for cid, fs in by_contract.items():
            if cid in titles:
                s = score_findings(fs, self._cfg)
                scored.append({"contract": titles[cid], "business": s.business, "extraction": s.extraction, "band": s.band, "top": [c.title for c in sorted(s.contributions, key=lambda c: -c.weight)[:3]]})
        d.top_risk = sorted(scored, key=lambda r: -r["business"])[:6]
        d.insights = self._insights(contracts, open_ob, deadlines, findings, d, today)
        return d

    def _insights(self, contracts: list[Contract], open_ob, deadlines, findings, d: DashboardData, today: date) -> list[str]:
        out: list[str] = []
        unassigned = sum(1 for o in open_ob if o.owner_id is None)
        if unassigned:
            out.append(f"{unassigned} open obligation(s) have no assigned owner.")
        unverified = sum(1 for x in deadlines if x.validation_status is ValidationStatus.PENDING_REVIEW and x.due_date)
        if unverified:
            out.append(f"{unverified} calculated deadline(s) are still awaiting human confirmation.")
        unresolved = sum(1 for x in deadlines if x.validation_status is ValidationStatus.UNRESOLVED)
        if unresolved:
            out.append(f"{unresolved} deadline(s) cannot be calculated yet (missing dates or events).")
        high = sum(1 for f in findings if f.status is FindingStatus.OPEN and f.severity in (Severity.HIGH, Severity.CRITICAL))
        if high:
            out.append(f"{high} high-severity review signal(s) are open.")
        failed = d.processing.get(IndexingStatus.FAILED.value, 0)
        if failed:
            out.append(f"{failed} document(s) failed processing.")
        return out

    # ------------------------------------------------------------------ risk observatory
    def risk_matrix(self) -> dict[tuple[str, str], int]:
        """Open findings by (severity, category)."""
        m: Counter[tuple[str, str]] = Counter()
        for f in self._r.findings.list([F.in_("status", [FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED])]):
            m[(f.severity.value, f.signal_category.value)] += 1
        return dict(m)

    def risk_heatmap(self) -> tuple[list[str], list[str], list[list[float]]]:
        """Contracts x finding types (summed weight of active findings)."""
        contracts = {c.id: c.title for c in self._r.contracts.list([F.is_null("deleted_at")])}
        types: list[str] = sorted({f.finding_type.value for f in self._r.findings.list()})
        grid: dict[Any, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for f in self._r.findings.list([F.in_("status", [FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED])]):
            if f.contract_id in contracts:
                grid[f.contract_id][f.finding_type.value] += f.weight
        ids = sorted(grid, key=lambda c: -sum(grid[c].values()))[:12]
        return [contracts[i] for i in ids], types, [[round(grid[i].get(t, 0.0), 1) for t in types] for i in ids]

    def risk_trend(self, days: int = 90) -> list[tuple[datetime, float, float]]:
        """Score history from completed analysis runs (business, extraction)."""
        out = []
        for r in self._r.runs.list([F.eq("run_type", "ingest_analysis")], order_by=[("created_at", False)]):
            rs = r.result_summary or {}
            if "business_risk" in rs and r.finished_at:
                out.append((r.finished_at, float(rs["business_risk"]), float(rs.get("extraction_uncertainty", 0.0))))
        return out[-days:]
