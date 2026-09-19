"""Configurable risk-signal catalogue and scoring.

Two scores are always kept apart:
* **business review score**: signals about the *contract's content or exposure* that merit human attention.
* **extraction uncertainty score**: signals about how much the *AI output* still needs human verification.

Scores are review-prioritisation aids, never legal conclusions. Every point is traceable to a signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import FindingType, Severity, SignalCategory


@dataclass(frozen=True)
class SignalDef:
    key: str
    title: str
    finding_type: FindingType
    severity: Severity
    category: SignalCategory
    weight: float


_B, _E = SignalCategory.BUSINESS_RISK, SignalCategory.EXTRACTION_UNCERTAINTY
_T = FindingType

SIGNALS: dict[str, SignalDef] = {s.key: s for s in [
    SignalDef("missing_parties", "Fewer than two contracting parties identified", _T.MISSING_INFORMATION, Severity.MEDIUM, _E, 8),
    SignalDef("missing_effective_date", "Effective date not established", _T.MISSING_INFORMATION, Severity.MEDIUM, _E, 8),
    SignalDef("missing_term", "Contract term / expiration not established", _T.MISSING_INFORMATION, Severity.MEDIUM, _E, 8),
    SignalDef("missing_governing_law", "Governing law not found", _T.MISSING_INFORMATION, Severity.LOW, _E, 4),
    SignalDef("missing_payment_terms", "Payment terms not found", _T.MISSING_INFORMATION, Severity.LOW, _E, 4),
    SignalDef("missing_termination", "Termination provisions not found", _T.MISSING_INFORMATION, Severity.LOW, _E, 4),
    SignalDef("auto_renewal_notice", "Automatic renewal requires timely notice", _T.RISK_SIGNAL, Severity.LOW, _B, 6),
    SignalDef("auto_renewal_short_notice", "Very short window to prevent automatic renewal", _T.RISK_SIGNAL, Severity.MEDIUM, _B, 12),
    SignalDef("auto_renewal_long_notice", "Long notice lead time to prevent automatic renewal", _T.RISK_SIGNAL, Severity.MEDIUM, _B, 10),
    SignalDef("auto_renewal_no_notice", "Automatic renewal without a stated notice period", _T.AMBIGUITY, Severity.HIGH, _B, 15),
    SignalDef("renewal_window_approaching", "Renewal notice deadline is approaching", _T.DEADLINE_EXPOSURE, Severity.HIGH, _B, 18),
    SignalDef("expiring_soon", "Contract expires soon", _T.DEADLINE_EXPOSURE, Severity.MEDIUM, _B, 8),
    SignalDef("past_expiration", "Expiration date has passed without a recorded termination", _T.DEADLINE_EXPOSURE, Severity.HIGH, _B, 15),
    SignalDef("overdue_deadline", "Open deadline is overdue", _T.DEADLINE_EXPOSURE, Severity.HIGH, _B, 14),
    SignalDef("unresolved_deadlines", "Deadlines could not be calculated", _T.DEADLINE_EXPOSURE, Severity.MEDIUM, _E, 6),
    SignalDef("deadline_assumptions", "Deadlines rely on stated assumptions", _T.EXTRACTION_QUALITY, Severity.LOW, _E, 3),
    SignalDef("unassigned_obligations", "Obligations have no assigned owner", _T.UNASSIGNED_OBLIGATION, Severity.LOW, _B, 4),
    SignalDef("unsupported_claims", "Extracted items lack verifiable source evidence", _T.UNSUPPORTED_CLAIM, Severity.MEDIUM, _E, 10),
    SignalDef("low_evidence_coverage", "Evidence coverage is low", _T.EXTRACTION_QUALITY, Severity.MEDIUM, _E, 8),
    SignalDef("conflicting_provisions", "Possible conflicting provisions", _T.CONFLICT, Severity.HIGH, _B, 14),
    SignalDef("party_reference_mismatch", "Obligations refer to parties not identified in the contract", _T.EXTRACTION_QUALITY, Severity.LOW, _E, 4),
    SignalDef("date_inconsistency", "Dates or periods appear inconsistent", _T.CONFLICT, Severity.MEDIUM, _B, 10),
    SignalDef("low_ocr_confidence", "Low OCR confidence", _T.EXTRACTION_QUALITY, Severity.MEDIUM, _E, 8),
    SignalDef("playbook_deviation", "Deviates from an approved playbook rule", _T.PLAYBOOK_DEVIATION, Severity.MEDIUM, _B, 8),
    SignalDef("ambiguous_language", "Ambiguous or vague language", _T.AMBIGUITY, Severity.MEDIUM, _B, 8),
    SignalDef("unusual_term", "Unusual or one-sided term", _T.RISK_SIGNAL, Severity.MEDIUM, _B, 8),
    SignalDef("prompt_injection", "Text resembling AI instructions", _T.PROMPT_INJECTION, Severity.MEDIUM, _B, 5),
    SignalDef("amendment_change", "Amendment changes a material term", _T.AMENDMENT_CHANGE, Severity.MEDIUM, _B, 6),
]}

SEVERITY_WEIGHT = {Severity.INFO: 1.0, Severity.LOW: 4.0, Severity.MEDIUM: 8.0, Severity.HIGH: 14.0, Severity.CRITICAL: 20.0}


class RiskConfig(BaseModel):
    """Per-organisation scoring configuration (stored in ``organizations.settings['risk_scoring']``)."""

    weights: dict[str, float] = Field(default_factory=dict)  # signal key -> weight override
    disabled: list[str] = Field(default_factory=list)
    review_threshold: float = 40.0  # score at/above which a contract is queued for review
    cap: float = 100.0
    renewal_window_days: int = 60
    expiring_days: int = 90

    def weight_for(self, key: str, default: float | None = None) -> float:
        base = SIGNALS[key].weight if key in SIGNALS else (default if default is not None else 5.0)
        return float(self.weights.get(key, base))

    def enabled(self, key: str) -> bool:
        return key not in self.disabled


def load_risk_config(org_settings: dict[str, Any] | None) -> RiskConfig:
    raw = (org_settings or {}).get("risk_scoring") or {}
    try:
        return RiskConfig.model_validate(raw)
    except Exception:  # noqa: BLE001 - a malformed setting must not break analysis
        return RiskConfig()
