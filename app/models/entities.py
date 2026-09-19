"""Table models: the contract "digital twin" and its operational entities.

Every model here becomes a Postgres table (RLS-protected) and a local SQLite table.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar
from uuid import UUID

from pydantic import Field

from app.models.base import (
    ADMIN_ROLES,
    FK,
    MANAGER_ROLES,
    OPERATOR_ROLES,
    OrgScopedModel,
    Policy,
    TableModel,
)
from app.models.enums import (
    AlertStatus,
    AlertType,
    AmendmentStatus,
    AnalysisStatus,
    ClauseType,
    ContractStatus,
    ContractType,
    DeadlineCategory,
    DeadlineKind,
    DeadlineStatus,
    DependencyType,
    DocumentRole,
    EventType,
    FindingStatus,
    FindingType,
    IndexingStatus,
    IntegrationProvider,
    NoteKind,
    ObligationCategory,
    ObligationStatus,
    PartyRole,
    PartyType,
    PlaybookRuleType,
    ReviewCaseStatus,
    ReviewDecision,
    ReviewStatus,
    Role,
    RunStatus,
    RunType,
    Severity,
    SignalCategory,
    SubjectType,
    ValidationStatus,
)

# --- reference aliases --------------------------------------------------------------------
ContractRef = Annotated[UUID, FK("contracts")]
ContractRefOpt = Annotated[UUID | None, FK("contracts")]
VersionRef = Annotated[UUID, FK("contract_versions")]
VersionRefOpt = Annotated[UUID | None, FK("contract_versions", on_delete="SET NULL")]
DocumentRef = Annotated[UUID, FK("documents")]
DocumentRefOpt = Annotated[UUID | None, FK("documents", on_delete="SET NULL")]
UserRefOpt = Annotated[UUID | None, FK("profiles", on_delete="SET NULL")]
PartyRefOpt = Annotated[UUID | None, FK("parties", on_delete="SET NULL")]
ClauseRefOpt = Annotated[UUID | None, FK("clauses", on_delete="SET NULL")]
ObligationRef = Annotated[UUID, FK("obligations")]
ObligationRefOpt = Annotated[UUID | None, FK("obligations", on_delete="SET NULL")]
DeadlineRefOpt = Annotated[UUID | None, FK("deadlines", on_delete="SET NULL")]
RunRefOpt = Annotated[UUID | None, FK("analysis_runs", on_delete="SET NULL")]
EventRefOpt = Annotated[UUID | None, FK("events", on_delete="SET NULL")]
FindingRefOpt = Annotated[UUID | None, FK("analysis_findings", on_delete="SET NULL")]


# --- tenancy & identity -------------------------------------------------------------------
class Organization(TableModel):
    table_name: ClassVar[str] = "organizations"
    table_unique: ClassVar = (("slug",),)
    table_policy: ClassVar[Policy] = Policy(kind="organizations", write=ADMIN_ROLES, delete=(Role.OWNER,))
    org_scoped: ClassVar[bool] = False

    name: str
    slug: str
    settings: dict[str, Any] = Field(default_factory=dict)
    retention_days: int | None = None


class Profile(TableModel):
    table_name: ClassVar[str] = "profiles"
    table_policy: ClassVar[Policy] = Policy(kind="profiles")
    org_scoped: ClassVar[bool] = False

    id: Annotated[UUID, FK("auth.users")]
    email: str
    full_name: str | None = None
    avatar_url: str | None = None
    default_org_id: Annotated[UUID | None, FK("organizations", on_delete="SET NULL")] = None


class OrganizationMember(OrgScopedModel):
    table_name: ClassVar[str] = "organization_members"
    table_unique: ClassVar = (("org_id", "user_id"),)
    table_indexes: ClassVar = (("user_id",),)
    table_policy: ClassVar[Policy] = Policy(write=ADMIN_ROLES, delete=ADMIN_ROLES, audited=True)

    user_id: Annotated[UUID, FK("profiles")]
    role: Role = Role.MEMBER


# --- contract digital twin ----------------------------------------------------------------
class Contract(OrgScopedModel):
    table_name: ClassVar[str] = "contracts"
    table_indexes: ClassVar = (("org_id", "status"), ("org_id", "expiration_date"), ("org_id", "deleted_at"))
    table_policy: ClassVar[Policy] = Policy(audited=True)

    title: str
    contract_type: ContractType = ContractType.UNKNOWN
    status: ContractStatus = ContractStatus.DRAFT
    effective_date: date | None = None
    expiration_date: date | None = None
    auto_renews: bool | None = None
    renewal_notice_days: int | None = None
    renewal_term_months: int | None = None
    governing_law: str | None = None
    payment_terms: str | None = None
    currency: str | None = None
    total_value: float | None = None
    current_version_id: Annotated[UUID | None, FK("contract_versions", on_delete="SET NULL", deferred=True)] = None
    owner_id: UserRefOpt = None
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    analysis_status: AnalysisStatus = AnalysisStatus.PENDING
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    business_risk_score: float | None = None
    extraction_uncertainty_score: float | None = None
    is_demo: bool = False
    created_by: UserRefOpt = None
    deleted_at: datetime | None = None


class ContractVersion(OrgScopedModel):
    table_name: ClassVar[str] = "contract_versions"
    table_unique: ClassVar = (("contract_id", "version_number"),)
    table_indexes: ClassVar = (("contract_id",),)
    table_policy: ClassVar[Policy] = Policy(audited=True)

    contract_id: ContractRef
    version_number: int = 1
    label: str = "Original"
    document_role: DocumentRole = DocumentRole.ORIGINAL
    supersedes_version_id: Annotated[UUID | None, FK("contract_versions", on_delete="SET NULL")] = None
    effective_date: date | None = None
    is_current: bool = True
    extraction: dict[str, Any] = Field(default_factory=dict)
    created_by: UserRefOpt = None


class Document(OrgScopedModel):
    table_name: ClassVar[str] = "documents"
    table_unique: ClassVar = (("org_id", "file_hash"),)
    table_indexes: ClassVar = (("contract_id",), ("contract_version_id",), ("indexing_status",))
    table_policy: ClassVar[Policy] = Policy(audited=True)

    contract_id: ContractRef
    contract_version_id: VersionRef
    filename: str
    mime_type: str
    size_bytes: int
    file_hash: str
    storage_path: str
    page_count: int = 0
    page_basis: str = "physical"  # physical | logical (DOCX has no physical pages)
    is_scanned: bool = False
    ocr_used: bool = False
    ocr_confidence: float | None = None
    indexing_status: IndexingStatus = IndexingStatus.PENDING
    indexing_progress: float = 0.0
    indexing_error: str | None = None
    index_attempts: int = 0
    heartbeat_at: datetime | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    chunk_count: int = 0
    indexed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    uploaded_by: UserRefOpt = None


class DocumentChunk(OrgScopedModel):
    table_name: ClassVar[str] = "document_chunks"
    table_unique: ClassVar = (("document_id", "chunk_index"),)
    table_indexes: ClassVar = (("contract_version_id",), ("contract_id",))

    document_id: DocumentRef
    contract_id: ContractRef
    contract_version_id: VersionRef
    chunk_index: int
    text: str
    page_number: int
    page_end: int
    section_reference: str | None = None
    section_title: str | None = None
    clause_type: ClauseType = ClauseType.OTHER
    char_start: int = 0
    char_end: int = 0
    token_estimate: int = 0
    text_hash: str
    embedded: bool = False


class Party(OrgScopedModel):
    table_name: ClassVar[str] = "parties"
    table_unique: ClassVar = (("org_id", "normalized_name"),)

    name: str
    normalized_name: str
    party_type: PartyType = PartyType.COMPANY
    address: str | None = None
    identifiers: dict[str, Any] = Field(default_factory=dict)


class ContractParty(OrgScopedModel):
    table_name: ClassVar[str] = "contract_parties"
    table_unique: ClassVar = (("contract_id", "party_id", "role"),)
    table_indexes: ClassVar = (("contract_id",),)

    contract_id: ContractRef
    party_id: Annotated[UUID, FK("parties")]
    role: PartyRole = PartyRole.OTHER
    confidence: float = 0.0
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class Clause(OrgScopedModel):
    table_name: ClassVar[str] = "clauses"
    table_unique: ClassVar = (("contract_version_id", "fingerprint"),)
    table_indexes: ClassVar = (("contract_id", "clause_type"),)

    contract_id: ContractRef
    contract_version_id: VersionRef
    document_id: DocumentRefOpt = None
    clause_type: ClauseType = ClauseType.OTHER
    heading: str | None = None
    section_reference: str | None = None
    page_number: int | None = None
    text: str
    summary: str | None = None
    fingerprint: str
    confidence: float = 0.0
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    human_modified: bool = False
    analysis_run_id: RunRefOpt = None


class Obligation(OrgScopedModel):
    table_name: ClassVar[str] = "obligations"
    table_unique: ClassVar = (("contract_version_id", "fingerprint"),)
    table_indexes: ClassVar = (("contract_id", "status"), ("org_id", "owner_id"), ("org_id", "review_status"))
    table_policy: ClassVar[Policy] = Policy(audited=True)

    contract_id: ContractRef
    contract_version_id: VersionRef
    clause_id: ClauseRefOpt = None
    title: str
    description: str
    category: ObligationCategory = ObligationCategory.OTHER
    responsible_party_id: PartyRefOpt = None
    responsible_party_name: str | None = None
    beneficiary_party_id: PartyRefOpt = None
    beneficiary_name: str | None = None
    trigger: str | None = None
    conditions: str | None = None
    frequency: str | None = None
    deadline_text: str | None = None
    owner_id: UserRefOpt = None
    status: ObligationStatus = ObligationStatus.PENDING
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    confidence: float = 0.0
    uncertainty: str | None = None
    fingerprint: str
    human_modified: bool = False
    is_recurring: bool = False
    dispute_flag: bool = False
    analysis_run_id: RunRefOpt = None
    completed_at: datetime | None = None
    completed_by: UserRefOpt = None


class ObligationDependency(OrgScopedModel):
    table_name: ClassVar[str] = "obligation_dependencies"
    table_unique: ClassVar = (("obligation_id", "depends_on_obligation_id"),)

    obligation_id: ObligationRef
    depends_on_obligation_id: ObligationRef
    dependency_type: DependencyType = DependencyType.FINISH_TO_START
    notes: str | None = None


class ObligationNote(OrgScopedModel):
    """Notes, completion evidence, disputes and exceptions recorded by humans."""

    table_name: ClassVar[str] = "obligation_notes"
    table_indexes: ClassVar = (("obligation_id",),)

    obligation_id: ObligationRef
    kind: NoteKind = NoteKind.NOTE
    body: str
    attachment_path: str | None = None
    author_id: UserRefOpt = None


class Event(OrgScopedModel):
    """A real-world event (e.g. invoice received) that anchors event-triggered deadlines."""

    table_name: ClassVar[str] = "events"
    table_indexes: ClassVar = (("contract_id",),)

    contract_id: ContractRef
    event_type: EventType = EventType.CUSTOM
    name: str
    occurred_on: date | None = None
    description: str | None = None
    recorded_by: UserRefOpt = None


class Deadline(OrgScopedModel):
    table_name: ClassVar[str] = "deadlines"
    table_unique: ClassVar = (("contract_version_id", "fingerprint"),)
    table_indexes: ClassVar = (("org_id", "due_date"), ("contract_id",), ("obligation_id",))
    table_policy: ClassVar[Policy] = Policy(audited=True)

    contract_id: ContractRef
    contract_version_id: VersionRef
    obligation_id: Annotated[UUID | None, FK("obligations")] = None
    kind: DeadlineKind = DeadlineKind.UNRESOLVED
    category: DeadlineCategory = DeadlineCategory.OTHER
    label: str
    due_date: date | None = None
    due_at: datetime | None = None
    timezone: str = "UTC"
    anchor_event: str | None = None
    anchor_date: date | None = None
    trigger_event_id: EventRefOpt = None
    depends_on_deadline_id: DeadlineRefOpt = None
    calculation_rule: dict[str, Any] = Field(default_factory=dict)
    calculation_trace: list[Any] = Field(default_factory=list)
    assumptions: list[Any] = Field(default_factory=list)
    missing_anchors: list[Any] = Field(default_factory=list)
    recurrence: dict[str, Any] = Field(default_factory=dict)
    source_clause_id: ClauseRefOpt = None
    validation_status: ValidationStatus = ValidationStatus.PENDING_REVIEW
    status: DeadlineStatus = DeadlineStatus.OPEN
    escalation_level: int = 0
    fingerprint: str
    human_modified: bool = False
    analysis_run_id: RunRefOpt = None


class Evidence(OrgScopedModel):
    table_name: ClassVar[str] = "evidence"
    table_indexes: ClassVar = (("subject_type", "subject_id"), ("contract_id",), ("document_id",))

    contract_id: ContractRef
    contract_version_id: VersionRef
    document_id: DocumentRefOpt = None
    chunk_id: Annotated[UUID | None, FK("document_chunks", on_delete="SET NULL")] = None
    subject_type: SubjectType
    subject_id: UUID
    field_name: str | None = None
    page_number: int | None = None
    section_reference: str | None = None
    quote: str
    quote_hash: str
    char_start: int | None = None
    char_end: int | None = None
    verified: bool = False
    verification_method: str | None = None
    analysis_run_id: RunRefOpt = None


# --- analysis, review, alerts -------------------------------------------------------------
class AnalysisRun(OrgScopedModel):
    table_name: ClassVar[str] = "analysis_runs"
    table_indexes: ClassVar = (("org_id", "created_at"), ("contract_id",))

    contract_id: Annotated[UUID | None, FK("contracts", on_delete="SET NULL")] = None
    contract_version_id: VersionRefOpt = None
    run_type: RunType = RunType.INGEST_ANALYSIS
    status: RunStatus = RunStatus.QUEUED
    workflow_plan: dict[str, Any] = Field(default_factory=dict)
    steps: list[Any] = Field(default_factory=list)
    input_summary: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    quality_score: float | None = None
    error: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    requested_by: UserRefOpt = None


class AnalysisFinding(OrgScopedModel):
    table_name: ClassVar[str] = "analysis_findings"
    table_unique: ClassVar = (("contract_id", "fingerprint"),)
    table_indexes: ClassVar = (("org_id", "status"), ("contract_id",))

    contract_id: ContractRef
    contract_version_id: VersionRefOpt = None
    run_id: RunRefOpt = None
    finding_type: FindingType = FindingType.RISK_SIGNAL
    severity: Severity = Severity.MEDIUM
    signal_category: SignalCategory = SignalCategory.BUSINESS_RISK
    title: str
    description: str
    clause_id: ClauseRefOpt = None
    obligation_id: ObligationRefOpt = None
    deadline_id: DeadlineRefOpt = None
    evidence_ids: list[Any] = Field(default_factory=list)
    contributions: dict[str, Any] = Field(default_factory=dict)
    weight: float = 0.0
    status: FindingStatus = FindingStatus.OPEN
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    confidence: float = 0.0
    fingerprint: str


class ReviewCase(OrgScopedModel):
    table_name: ClassVar[str] = "review_cases"
    table_indexes: ClassVar = (("org_id", "status"), ("contract_id",))
    table_policy: ClassVar[Policy] = Policy(write=MANAGER_ROLES, insert=OPERATOR_ROLES, audited=True)

    contract_id: Annotated[UUID | None, FK("contracts")] = None
    finding_id: FindingRefOpt = None
    run_id: RunRefOpt = None
    subject_type: SubjectType = SubjectType.CONTRACT
    subject_id: UUID | None = None
    title: str
    reason: str
    priority: Severity = Severity.MEDIUM
    status: ReviewCaseStatus = ReviewCaseStatus.OPEN
    assignee_id: UserRefOpt = None
    decision: ReviewDecision | None = None
    decision_notes: str | None = None
    decided_by: UserRefOpt = None
    decided_at: datetime | None = None
    proposed_change: dict[str, Any] = Field(default_factory=dict)


class Alert(OrgScopedModel):
    table_name: ClassVar[str] = "alerts"
    table_unique: ClassVar = (("org_id", "dedupe_key"),)
    table_indexes: ClassVar = (("org_id", "status"), ("deadline_id",))

    contract_id: Annotated[UUID | None, FK("contracts")] = None
    obligation_id: Annotated[UUID | None, FK("obligations")] = None
    deadline_id: Annotated[UUID | None, FK("deadlines")] = None
    alert_type: AlertType = AlertType.SYSTEM
    severity: Severity = Severity.MEDIUM
    title: str
    message: str
    due_on: date | None = None
    status: AlertStatus = AlertStatus.UNREAD
    dedupe_key: str
    escalation_level: int = 0
    acknowledged_by: UserRefOpt = None
    acknowledged_at: datetime | None = None


class Amendment(OrgScopedModel):
    table_name: ClassVar[str] = "amendments"
    table_unique: ClassVar = (("base_version_id", "amendment_version_id"),)
    table_indexes: ClassVar = (("contract_id",),)
    table_policy: ClassVar[Policy] = Policy(audited=True)

    contract_id: ContractRef
    base_version_id: VersionRef
    amendment_version_id: VersionRef
    summary: str | None = None
    changes: list[Any] = Field(default_factory=list)
    status: AmendmentStatus = AmendmentStatus.DRAFT
    run_id: RunRefOpt = None


class PlaybookRule(OrgScopedModel):
    table_name: ClassVar[str] = "playbook_rules"
    table_unique: ClassVar = (("org_id", "name"),)
    table_policy: ClassVar[Policy] = Policy(write=(Role.OWNER, Role.ADMIN, Role.LEGAL_REVIEWER), delete=(Role.OWNER, Role.ADMIN, Role.LEGAL_REVIEWER), audited=True)

    name: str
    description: str | None = None
    clause_type: ClauseType | None = None
    rule_type: PlaybookRuleType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = Severity.MEDIUM
    enabled: bool = True


class Integration(OrgScopedModel):
    table_name: ClassVar[str] = "integrations"
    table_unique: ClassVar = (("org_id", "name"),)
    table_policy: ClassVar[Policy] = Policy(read=MANAGER_ROLES, write=ADMIN_ROLES, delete=ADMIN_ROLES, audited=True)

    provider: IntegrationProvider
    name: str
    config: dict[str, Any] = Field(default_factory=dict)  # never holds secrets
    enabled: bool = False
    requires_confirmation: bool = True
    allowed_actions: list[str] = Field(default_factory=list)
    created_by: UserRefOpt = None


class AuditLog(OrgScopedModel):
    table_name: ClassVar[str] = "audit_logs"
    table_indexes: ClassVar = (("org_id", "created_at"), ("entity_type", "entity_id"))
    table_policy: ClassVar[Policy] = Policy(kind="audit", read=(Role.OWNER, Role.ADMIN, Role.LEGAL_REVIEWER))

    actor_id: UserRefOpt = None
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: UUID | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
