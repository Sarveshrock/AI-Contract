"""Shared analysis state ("blackboard") passed between agents. Plain dataclasses, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Generic, TypeVar

from app.agents.evidence import ResolvedEvidence
from app.models.enums import ClauseType, ContractType, DocumentRole, FindingType, ObligationCategory, PartyRole, PartyType, Severity, SignalCategory
from app.schemas.llm_outputs import TemporalTermOut
from app.tools.temporal import DeadlineComputation

T = TypeVar("T")


@dataclass
class Fact(Generic[T]):
    value: T | None = None
    raw_text: str | None = None
    evidence: list[ResolvedEvidence] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return any(e.verified for e in self.evidence)

    @property
    def present(self) -> bool:
        return self.value is not None


@dataclass
class PartyDraft:
    name: str
    role: PartyRole
    party_type: PartyType
    evidence: list[ResolvedEvidence] = field(default_factory=list)


@dataclass
class ClauseDraft:
    clause_type: ClauseType
    heading: str | None
    section_reference: str | None
    summary: str
    evidence: list[ResolvedEvidence]
    fingerprint: str = ""
    text: str = ""
    page_number: int | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class ExtractedContract:
    title: str | None = None
    contract_type: ContractType = ContractType.UNKNOWN
    parties: list[PartyDraft] = field(default_factory=list)
    effective_date: Fact[date] = field(default_factory=Fact)
    expiration_date: Fact[date] = field(default_factory=Fact)
    initial_term_months: Fact[int] = field(default_factory=Fact)
    auto_renews: Fact[bool] = field(default_factory=Fact)
    renewal_term_months: Fact[int] = field(default_factory=Fact)
    renewal_notice_days: Fact[int] = field(default_factory=Fact)
    payment_terms: Fact[str] = field(default_factory=Fact)
    payment_days: Fact[int] = field(default_factory=Fact)
    currency: Fact[str] = field(default_factory=Fact)
    total_value: Fact[float] = field(default_factory=Fact)
    governing_law: Fact[str] = field(default_factory=Fact)
    termination_convenience_notice_days: Fact[int] = field(default_factory=Fact)
    termination_cure_days: Fact[int] = field(default_factory=Fact)
    sla_summary: Fact[str] = field(default_factory=Fact)
    clauses: list[ClauseDraft] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)

    FACT_FIELDS = ("effective_date", "expiration_date", "initial_term_months", "auto_renews", "renewal_term_months", "renewal_notice_days",
                   "payment_terms", "payment_days", "currency", "total_value", "governing_law", "termination_convenience_notice_days",
                   "termination_cure_days", "sla_summary")

    def facts(self) -> dict[str, Fact]:
        return {name: getattr(self, name) for name in self.FACT_FIELDS}

    def snapshot(self) -> dict[str, Any]:
        """JSON snapshot stored on the contract version (used for version comparison)."""
        snap: dict[str, Any] = {"title": self.title, "contract_type": self.contract_type.value,
                                "parties": [{"name": p.name, "role": p.role.value} for p in self.parties]}
        for name, fact in self.facts().items():
            v = fact.value
            snap[name] = v.isoformat() if isinstance(v, date) else v
        snap["missing_information"] = list(self.missing_information)
        return snap


@dataclass
class ObligationDraft:
    title: str
    description: str
    category: ObligationCategory
    responsible: str | None
    beneficiary: str | None
    trigger: str | None
    conditions: str | None
    frequency_text: str | None
    deadline_text: str | None
    temporal: TemporalTermOut | None
    section_reference: str | None
    evidence: list[ResolvedEvidence]
    confidence: float
    uncertainty: str | None
    fingerprint: str = ""
    flags: list[str] = field(default_factory=list)
    responsible_party_match: str | None = None
    beneficiary_match: str | None = None

    @property
    def verified(self) -> bool:
        return any(e.verified for e in self.evidence)


@dataclass
class DeadlineDraft:
    label: str
    category: str
    computation: DeadlineComputation
    section_reference: str | None
    evidence: list[ResolvedEvidence]
    obligation_fingerprint: str | None = None
    fingerprint: str = ""
    anchor_event: str | None = None


@dataclass
class FindingDraft:
    finding_type: FindingType
    severity: Severity
    category: SignalCategory
    title: str
    description: str
    weight: float
    evidence: list[ResolvedEvidence] = field(default_factory=list)
    obligation_fingerprint: str | None = None
    deadline_fingerprint: str | None = None
    fingerprint: str = ""
    confidence: float = 1.0
    signal: str = ""
    needs_review: bool = True


@dataclass
class ClauseDiff:
    change_type: str  # added | removed | modified
    section_old: str | None
    section_new: str | None
    title: str
    clause_type: str
    similarity: float
    old_text: str
    new_text: str
    summary: str = ""
    materiality: str = "medium"


@dataclass
class FieldChange:
    field: str
    old: Any
    new: Any
    summary: str


@dataclass
class AmendmentReport:
    base_version_id: str
    new_version_id: str
    clause_diffs: list[ClauseDiff] = field(default_factory=list)
    field_changes: list[FieldChange] = field(default_factory=list)
    instructions: list[dict[str, Any]] = field(default_factory=list)
    deadline_shifts: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass
class QAIssue:
    kind: str  # schema | coverage | citation | date | party | contradiction | unsupported
    severity: Severity
    subject: str
    message: str


@dataclass
class QAReport:
    score: float
    coverage: float
    citation_integrity: float
    issues: list[QAIssue] = field(default_factory=list)
    needs_review: bool = False
    evidence_total: int = 0
    evidence_verified: int = 0


@dataclass
class DocumentProfile:
    page_count: int
    section_count: int
    table_count: int
    ocr_used: bool
    ocr_confidence: float | None
    document_role: DocumentRole
    type_hint: str | None
    outline: list[dict[str, Any]] = field(default_factory=list)
    related_documents: list[dict[str, Any]] = field(default_factory=list)
    injection_signals: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisState:
    profile: DocumentProfile | None = None
    contract: ExtractedContract | None = None
    obligations: list[ObligationDraft] = field(default_factory=list)
    deadlines: list[DeadlineDraft] = field(default_factory=list)
    findings: list[FindingDraft] = field(default_factory=list)
    amendment: AmendmentReport | None = None
    qa: QAReport | None = None
    unsupported_fingerprints: set[str] = field(default_factory=set)
