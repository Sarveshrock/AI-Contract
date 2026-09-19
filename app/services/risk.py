"""Risk scoring: explainable, configurable, with business risk and extraction uncertainty kept apart."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.config.risk import SIGNALS, RiskConfig, load_risk_config
from app.database.store import F
from app.models.entities import AnalysisFinding
from app.models.enums import FindingStatus, Severity, SignalCategory
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService

_ACTIVE = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED)


@dataclass
class Contribution:
    finding_id: str
    signal: str
    title: str
    severity: str
    category: str
    weight: float
    description: str


@dataclass
class RiskScore:
    business: float = 0.0
    extraction: float = 0.0
    contributions: list[Contribution] = field(default_factory=list)
    review_recommended: bool = False

    def explain(self) -> list[str]:
        lines = []
        for c in sorted(self.contributions, key=lambda c: -c.weight):
            lines.append(f"+{c.weight:.0f}  [{c.category.replace('_', ' ')}] {c.title}")
        return lines

    @property
    def band(self) -> str:
        top = max(self.business, self.extraction)
        return "high" if top >= 60 else "medium" if top >= 30 else "low"


def score_findings(findings: list[AnalysisFinding], cfg: RiskConfig) -> RiskScore:
    """Sum active finding weights per category, capped. Acknowledged findings still count; dismissed/resolved do not."""
    score = RiskScore()
    for f in findings:
        if f.status not in _ACTIVE:
            continue
        signal = (f.contributions or {}).get("signal", "")
        if signal and not cfg.enabled(signal):
            continue
        base = float(cfg.weights[signal]) if signal in cfg.weights else float(f.weight)  # org-configured weights re-score stored findings
        weight = base * (0.5 if f.status is FindingStatus.ACKNOWLEDGED else 1.0)
        c = Contribution(str(f.id), signal, f.title, f.severity.value, f.signal_category.value, weight, f.description)
        score.contributions.append(c)
        if f.signal_category is SignalCategory.BUSINESS_RISK:
            score.business += weight
        else:
            score.extraction += weight
    score.business = round(min(cfg.cap, score.business), 1)
    score.extraction = round(min(cfg.cap, score.extraction), 1)
    score.review_recommended = score.business >= cfg.review_threshold or any(
        f.status in _ACTIVE and f.severity in (Severity.HIGH, Severity.CRITICAL) for f in findings)
    return score


class RiskService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService) -> None:
        self._p, self._r, self._audit = principal, repos, audit

    def config(self) -> RiskConfig:
        org = self._r.organizations.get(self._p.org_id)
        return load_risk_config(org.settings if org else {})

    def save_config(self, cfg: RiskConfig) -> None:
        self._p.require(Permission.PLAYBOOK_MANAGE)
        org = self._r.organizations.get(self._p.org_id)
        settings = dict(org.settings if org else {})
        settings["risk_scoring"] = cfg.model_dump()
        self._r.organizations.update(self._p.org_id, settings=settings)
        self._audit.record("risk.config.update", "organization", self._p.org_id, weights=len(cfg.weights), disabled=len(cfg.disabled))

    def score_contract(self, contract_id: UUID | str, *, persist: bool = True) -> RiskScore:
        cfg = self.config()
        findings = self._r.findings.list([F.eq("contract_id", str(contract_id))])
        score = score_findings(findings, cfg)
        if persist:
            self._r.contracts.update(contract_id, business_risk_score=score.business, extraction_uncertainty_score=score.extraction)
        return score

    def rescore_all(self) -> int:
        n = 0
        for c in self._r.contracts.list([F.is_null("deleted_at")]):
            self.score_contract(c.id)
            n += 1
        return n

    def explain(self, contract_id: UUID | str) -> dict[str, Any]:
        s = self.score_contract(contract_id, persist=False)
        return {"business": s.business, "extraction": s.extraction, "band": s.band, "review_recommended": s.review_recommended,
                "contributions": [c.__dict__ for c in sorted(s.contributions, key=lambda c: -c.weight)],
                "note": "Scores prioritise human review. They are not legal conclusions. Extraction uncertainty measures how much the AI output still needs checking; it is not contract risk."}


_ = SIGNALS
