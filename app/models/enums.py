"""Domain enumerations. Their values are used verbatim in database CHECK constraints."""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    LEGAL_REVIEWER = "legal_reviewer"
    CONTRACT_MANAGER = "contract_manager"
    MEMBER = "member"
    VIEWER = "viewer"


class IndexingStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    OCR = "ocr"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    FAILED = "failed"


IN_PROGRESS_INDEXING = (IndexingStatus.PENDING, IndexingStatus.EXTRACTING, IndexingStatus.OCR, IndexingStatus.CHUNKING, IndexingStatus.EMBEDDING)


class ContractStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    TERMINATED = "terminated"
    ARCHIVED = "archived"


class ContractType(StrEnum):
    MSA = "msa"
    SOW = "sow"
    SAAS = "saas_subscription"
    NDA = "nda"
    EMPLOYMENT = "employment"
    LEASE = "lease"
    LICENSE = "license"
    PURCHASE = "purchase"
    SERVICES = "services"
    AMENDMENT = "amendment"
    OTHER = "other"
    UNKNOWN = "unknown"


class DocumentRole(StrEnum):
    ORIGINAL = "original"
    AMENDMENT = "amendment"
    ADDENDUM = "addendum"
    SCHEDULE = "schedule"
    REVISED = "revised"


PARTIAL_ROLES = (DocumentRole.AMENDMENT, DocumentRole.ADDENDUM, DocumentRole.SCHEDULE)


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class ClauseType(StrEnum):
    TERM = "term"
    PAYMENT = "payment"
    RENEWAL = "renewal"
    TERMINATION = "termination"
    SLA = "sla"
    CONFIDENTIALITY = "confidentiality"
    LIABILITY = "liability"
    INDEMNITY = "indemnity"
    GOVERNING_LAW = "governing_law"
    DISPUTE_RESOLUTION = "dispute_resolution"
    IP = "ip"
    DATA_PROTECTION = "data_protection"
    FORCE_MAJEURE = "force_majeure"
    ASSIGNMENT = "assignment"
    WARRANTY = "warranty"
    INSURANCE = "insurance"
    AUDIT = "audit"
    DEFINITIONS = "definitions"
    OTHER = "other"


class ObligationStatus(StrEnum):
    """Stored status. *Overdue* is derived from an open deadline in the past, never stored."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    WAIVED = "waived"


class ObligationCategory(StrEnum):
    PAYMENT = "payment"
    DELIVERY = "delivery"
    REPORTING = "reporting"
    NOTICE = "notice"
    COMPLIANCE = "compliance"
    CONFIDENTIALITY = "confidentiality"
    SERVICE_LEVEL = "service_level"
    RENEWAL = "renewal"
    TERMINATION = "termination"
    INSURANCE = "insurance"
    AUDIT = "audit"
    OTHER = "other"


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class DeadlineKind(StrEnum):
    EXPLICIT = "explicit"
    DERIVED = "derived"
    EVENT_TRIGGERED = "event_triggered"
    RECURRING = "recurring"
    UNRESOLVED = "unresolved"


class DeadlineCategory(StrEnum):
    RENEWAL_NOTICE = "renewal_notice"
    EXPIRATION = "expiration"
    TERMINATION_NOTICE = "termination_notice"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    REPORTING = "reporting"
    OTHER = "other"


class ValidationStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING_REVIEW = "pending_review"
    UNRESOLVED = "unresolved"
    REJECTED = "rejected"


class DeadlineStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    MISSED = "missed"
    WAIVED = "waived"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_ORDER = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


class FindingType(StrEnum):
    RISK_SIGNAL = "risk_signal"
    MISSING_INFORMATION = "missing_information"
    AMBIGUITY = "ambiguity"
    CONFLICT = "conflict"
    UNASSIGNED_OBLIGATION = "unassigned_obligation"
    DEADLINE_EXPOSURE = "deadline_exposure"
    PLAYBOOK_DEVIATION = "playbook_deviation"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    EXTRACTION_QUALITY = "extraction_quality"
    PROMPT_INJECTION = "prompt_injection"
    AMENDMENT_CHANGE = "amendment_change"


class SignalCategory(StrEnum):
    """Extraction uncertainty is kept separate from business risk (see Risk Observatory)."""

    BUSINESS_RISK = "business_risk"
    EXTRACTION_UNCERTAINTY = "extraction_uncertainty"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class AlertType(StrEnum):
    RENEWAL_WINDOW = "renewal_window"
    DEADLINE_APPROACHING = "deadline_approaching"
    DEADLINE_OVERDUE = "deadline_overdue"
    UNASSIGNED_OBLIGATION = "unassigned_obligation"
    UNRESOLVED_DEADLINE = "unresolved_deadline"
    REVIEW_AGING = "review_aging"
    CONTRACT_EXPIRING = "contract_expiring"
    SYSTEM = "system"


class AlertStatus(StrEnum):
    UNREAD = "unread"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunType(StrEnum):
    INGEST_ANALYSIS = "ingest_analysis"
    QUESTION = "question"
    COMPARE_VERSIONS = "compare_versions"
    RISK_REVIEW = "risk_review"
    REFRESH_DEADLINES = "refresh_deadlines"
    INDEXING = "indexing"


class ReviewCaseStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"
    ESCALATED = "escalated"


class SubjectType(StrEnum):
    CONTRACT = "contract"
    PARTY = "party"
    CLAUSE = "clause"
    OBLIGATION = "obligation"
    DEADLINE = "deadline"
    FINDING = "finding"
    AMENDMENT = "amendment"
    ANSWER = "answer"


class NoteKind(StrEnum):
    NOTE = "note"
    COMPLETION_EVIDENCE = "completion_evidence"
    DISPUTE = "dispute"
    EXCEPTION = "exception"


class DependencyType(StrEnum):
    FINISH_TO_START = "finish_to_start"
    EVENT = "event"
    OTHER = "other"


class PartyType(StrEnum):
    COMPANY = "company"
    INDIVIDUAL = "individual"
    GOVERNMENT = "government"
    OTHER = "other"


class PartyRole(StrEnum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    LICENSOR = "licensor"
    LICENSEE = "licensee"
    DISCLOSING = "disclosing_party"
    RECEIVING = "receiving_party"
    LANDLORD = "landlord"
    TENANT = "tenant"
    PARTNER = "partner"
    GUARANTOR = "guarantor"
    OTHER = "other"


class ChangeType(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


class AmendmentStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"


class EventType(StrEnum):
    INVOICE_RECEIVED = "invoice_received"
    NOTICE_SENT = "notice_sent"
    NOTICE_RECEIVED = "notice_received"
    DELIVERY_ACCEPTED = "delivery_accepted"
    SERVICE_START = "service_start"
    TERMINATION_NOTICE = "termination_notice"
    CUSTOM = "custom"


class IntegrationProvider(StrEnum):
    CALENDAR_ICS = "calendar_ics"
    WEBHOOK = "webhook"


class PlaybookRuleType(StrEnum):
    REQUIRE_CLAUSE = "require_clause"
    MAX_NOTICE_DAYS = "max_notice_days"
    MIN_NOTICE_DAYS = "min_notice_days"
    FORBID_AUTO_RENEWAL = "forbid_auto_renewal"
    FORBID_PHRASE = "forbid_phrase"
    MAX_PAYMENT_DAYS = "max_payment_days"
    REQUIRE_GOVERNING_LAW = "require_governing_law"
