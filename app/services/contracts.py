"""Contract portfolio and digital-twin service."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.errors import AIUnavailableError, ContractLensError, ValidationFailure
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import (
    Alert,
    Amendment,
    AnalysisFinding,
    Clause,
    Contract,
    ContractParty,
    ContractVersion,
    Deadline,
    Document,
    Event,
    Evidence,
    Obligation,
    Party,
    ReviewCase,
)
from app.models.enums import (
    AnalysisStatus,
    ContractStatus,
    DocumentRole,
    FindingStatus,
    ObligationStatus,
    ReviewCaseStatus,
    ReviewStatus,
    SubjectType,
)
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.analysis import AnalysisOutcome, AnalysisService
from app.services.audit import AuditService
from app.services.ingestion import IngestionResult, IngestionService

log = get_logger(__name__)


@dataclass
class ContractSummary:
    contract: Contract
    parties: list[str]
    open_obligations: int
    next_due: date | None
    open_reviews: int
    indexing_status: str | None
    document_count: int


@dataclass
class ContractTwin:
    contract: Contract
    versions: list[ContractVersion]
    documents: list[Document]
    parties: list[tuple[Party, ContractParty]]
    clauses: list[Clause]
    obligations: list[Obligation]
    deadlines: list[Deadline]
    events: list[Event]
    amendments: list[Amendment]
    findings: list[AnalysisFinding]
    review_cases: list[ReviewCase]
    alerts: list[Alert]
    evidence_by_subject: dict[tuple[str, str], list[Evidence]] = field(default_factory=dict)

    def evidence(self, subject_type: SubjectType, subject_id: UUID) -> list[Evidence]:
        return self.evidence_by_subject.get((subject_type.value, str(subject_id)), [])

    def current_version(self) -> ContractVersion | None:
        return next((v for v in self.versions if v.id == self.contract.current_version_id), self.versions[-1] if self.versions else None)


@dataclass
class UploadOutcome:
    ingestion: IngestionResult
    analysis: AnalysisOutcome | None
    analysis_skipped_reason: str | None = None


class ContractService:
    def __init__(self, principal: Principal, repos: Repositories, ingestion: IngestionService, analysis: AnalysisService, audit: AuditService,
                 ai_available: Callable[[], bool]) -> None:
        self._p, self._r, self._ing, self._analysis, self._audit = principal, repos, ingestion, analysis, audit
        self._ai_available = ai_available

    # ------------------------------------------------------------------ portfolio
    def list(self, *, search: str | None = None, status: ContractStatus | None = None, include_archived: bool = False) -> list[ContractSummary]:
        contracts = self._r.contracts.list([F.is_null("deleted_at")], order_by=[("updated_at", True)])
        if status:
            contracts = [c for c in contracts if c.status is status]
        elif not include_archived:
            contracts = [c for c in contracts if c.status is not ContractStatus.ARCHIVED]
        parties = {p.id: p.name for p in self._r.parties.list()}
        by_contract: dict[UUID, list[str]] = {}
        for cp in self._r.contract_parties.list():
            by_contract.setdefault(cp.contract_id, []).append(parties.get(cp.party_id, "?"))
        open_ob: dict[UUID, int] = {}
        for o in self._r.live_obligations([F.in_("status", [ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS])]):
            open_ob[o.contract_id] = open_ob.get(o.contract_id, 0) + 1
        next_due: dict[UUID, date] = {}
        for d in self._r.live_deadlines([F.eq("status", "open"), F.not_null("due_date"), F.gte("due_date", date.today())], order_by=[("due_date", False)]):
            next_due.setdefault(d.contract_id, d.due_date)
        reviews: dict[UUID, int] = {}
        for rc in self._r.reviews.list([F.in_("status", [ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW])]):
            if rc.contract_id:
                reviews[rc.contract_id] = reviews.get(rc.contract_id, 0) + 1
        docs: dict[UUID, list[Document]] = {}
        for d in self._r.documents.list():
            docs.setdefault(d.contract_id, []).append(d)
        out = []
        for c in contracts:
            if search and search.lower() not in f"{c.title} {' '.join(by_contract.get(c.id, []))} {c.contract_type.value}".lower():
                continue
            ds = docs.get(c.id, [])
            out.append(ContractSummary(c, sorted(set(by_contract.get(c.id, []))), open_ob.get(c.id, 0), next_due.get(c.id), reviews.get(c.id, 0),
                                       ds[-1].indexing_status.value if ds else None, len(ds)))
        return out

    def twin(self, contract_id: UUID | str) -> ContractTwin:
        cid = str(contract_id)
        c = self._r.contracts.require(cid)
        cps = self._r.contract_parties.list([F.eq("contract_id", cid)])
        parties = {p.id: p for p in self._r.parties.list([F.in_("id", [str(cp.party_id) for cp in cps])])} if cps else {}
        ev: dict[tuple[str, str], list[Evidence]] = {}
        for e in self._r.evidence.list([F.eq("contract_id", cid)]):
            ev.setdefault((e.subject_type.value, str(e.subject_id)), []).append(e)
        return ContractTwin(
            contract=c, versions=self._r.versions.list([F.eq("contract_id", cid)], order_by=[("version_number", False)]),
            documents=self._r.documents.list([F.eq("contract_id", cid)]), parties=[(parties[cp.party_id], cp) for cp in cps if cp.party_id in parties],
            clauses=self._r.clauses.list([F.eq("contract_id", cid)]), obligations=self._r.live_obligations([F.eq("contract_id", cid)]),
            deadlines=self._r.live_deadlines([F.eq("contract_id", cid)], order_by=[("due_date", False)]), events=self._r.events.list([F.eq("contract_id", cid)]),
            amendments=self._r.amendments.list([F.eq("contract_id", cid)]), findings=self._r.findings.list([F.eq("contract_id", cid)]),
            review_cases=self._r.reviews.list([F.eq("contract_id", cid)]), alerts=self._r.alerts.list([F.eq("contract_id", cid)]), evidence_by_subject=ev,
        )

    # ------------------------------------------------------------------ upload
    def upload(self, path: str | Path, *, contract_id: UUID | str | None = None, role: DocumentRole = DocumentRole.ORIGINAL, label: str | None = None,
               analyze: bool = True, on_progress: Callable[[str, float, str], None] | None = None,
               on_event: Callable[[str, dict], None] | None = None) -> UploadOutcome:
        """Ingest a file and, when AI is configured, analyse it. Ingestion errors propagate; analysis errors are reported."""
        res = self._ing.ingest_file(path, contract_id=contract_id, role=role, label=label, on_progress=on_progress)
        if not analyze:
            return UploadOutcome(res, None, "Analysis was not requested.")
        if not self._ai_available():
            self._r.contracts.update(res.contract_id, analysis_status=AnalysisStatus.PENDING)
            return UploadOutcome(res, None, "AI is not configured: the document is searchable, but extraction was not run. Set OPENAI_API_KEY to analyse it.")
        if on_progress:
            on_progress("analyze", 0.0, "Running agent analysis")
        try:
            outcome = self._analysis.analyze_version(res.contract_id, res.version_id, is_new_contract=contract_id is None, on_event=on_event)
        except AIUnavailableError as exc:
            return UploadOutcome(res, None, exc.user_message)
        except ContractLensError as exc:
            log.warning("analysis failed after upload: %s", type(exc).__name__)
            return UploadOutcome(res, None, exc.user_message)
        return UploadOutcome(res, outcome)

    def analyze_pending(self, on_event: Callable[[str, dict], None] | None = None) -> list[AnalysisOutcome]:
        """Analyse contracts whose document is indexed but not analysed yet (e.g. after configuring AI)."""
        out = []
        for c in self._r.contracts.list([F.in_("analysis_status", [AnalysisStatus.PENDING, AnalysisStatus.FAILED]), F.is_null("deleted_at")]):
            if c.current_version_id:
                out.append(self._analysis.analyze_version(c.id, c.current_version_id, is_new_contract=True, on_event=on_event))
        return out

    # ------------------------------------------------------------------ human decisions & edits
    def update_fields(self, contract_id: UUID | str, **fields: Any) -> Contract:
        self._p.require(Permission.CONTRACTS_WRITE)
        allowed = {"title", "contract_type", "effective_date", "expiration_date", "auto_renews", "renewal_notice_days", "renewal_term_months", "governing_law",
                   "payment_terms", "currency", "total_value", "owner_id", "tags", "summary"}
        bad = set(fields) - allowed
        if bad:
            raise ValidationFailure(f"fields not editable: {bad}", user_message="One of those fields cannot be edited.")
        c = self._r.contracts.update(contract_id, **fields)
        self._audit.record("contract.edit", "contract", c.id, fields=sorted(fields))
        return c

    def approve(self, contract_id: UUID | str) -> Contract:
        """A human accepts the analysis; the contract becomes ACTIVE (unless already expired/terminated)."""
        self._p.require(Permission.REVIEW_DECIDE)
        c = self._r.contracts.require(contract_id)
        status = ContractStatus.ACTIVE if c.status in (ContractStatus.DRAFT, ContractStatus.IN_REVIEW) else c.status
        c = self._r.contracts.update(c.id, status=status, review_status=ReviewStatus.APPROVED)
        self._audit.record("contract.approve", "contract", c.id)
        return c

    def set_status(self, contract_id: UUID | str, status: ContractStatus) -> Contract:
        self._p.require(Permission.CONTRACTS_WRITE)
        c = self._r.contracts.update(contract_id, status=status)
        self._audit.record("contract.status", "contract", c.id, status=status.value)
        return c

    def delete(self, contract_id: UUID | str) -> None:
        """Soft delete (recoverable until the retention purge)."""
        self._p.require(Permission.CONTRACTS_DELETE)
        c = self._r.contracts.update(contract_id, deleted_at=datetime.now(timezone.utc), status=ContractStatus.ARCHIVED)
        self._audit.record("contract.delete", "contract", c.id, title=c.title)

    # ------------------------------------------------------------------ viewer helpers
    def document_pages(self, version_id: UUID | str) -> list[tuple[int, str]]:
        """Reconstruct readable page text from chunks (overlap removed)."""
        chunks = self._r.chunks.list([F.eq("contract_version_id", str(version_id))], order_by=[("document_id", False), ("char_start", False)])
        pages: dict[int, list[str]] = {}
        last_end: dict[UUID, int] = {}
        for c in chunks:
            start = max(c.char_start, last_end.get(c.document_id, c.char_start))
            piece = c.text[start - c.char_start:]
            last_end[c.document_id] = max(last_end.get(c.document_id, 0), c.char_end)
            if piece.strip():
                pages.setdefault(c.page_number, []).append(piece)
        return [(p, "\n".join(parts)) for p, parts in sorted(pages.items())]

    def search_in_document(self, version_id: UUID | str, query: str) -> list[tuple[int, str]]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        hits = []
        for page, text in self.document_pages(version_id):
            i = text.lower().find(q)
            while i != -1 and len(hits) < 200:
                hits.append((page, text[max(0, i - 60): i + len(q) + 80].replace("\n", " ")))
                i = text.lower().find(q, i + len(q))
        return hits


_ = FindingStatus
