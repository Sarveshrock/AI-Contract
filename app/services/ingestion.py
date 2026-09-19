"""Document ingestion pipeline: validate → hash → store → extract (OCR) → sections → chunk → embed → index.

Idempotent by design: chunk ids derive from (organisation, file hash, index) and the vector upsert is
keyed by chunk id, so re-running any stage is safe. In-progress documents carry a heartbeat so that
interrupted jobs can be detected and resumed by :meth:`IngestionService.recover_interrupted`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.config.settings import Settings
from app.core.errors import ContractLensError, DocumentProcessingError, DuplicateDocumentError
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import Contract, ContractVersion, Document, DocumentChunk
from app.models.enums import (
    IN_PROGRESS_INDEXING,
    PARTIAL_ROLES,
    ContractStatus,
    DocumentRole,
    IndexingStatus,
)
from app.rag.chunker import chunk_document
from app.rag.indexer import DocumentIndexer
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.security.file_validation import validate_upload, verify_hash
from app.services.audit import AuditService
from app.services.storage import FileStorage, object_path
from app.tools.ocr import OcrEngine
from app.tools.parsers import ParsedDocument, parse_document
from app.tools.similarity import relation_score

log = get_logger(__name__)
ProgressFn = Callable[[str, float, str], None]


@dataclass
class RelatedDocument:
    document_id: str
    contract_id: str
    filename: str
    jaccard: float
    containment: float


@dataclass
class IngestionResult:
    contract_id: UUID
    version_id: UUID
    document_id: UUID
    chunk_count: int
    page_count: int
    warnings: list[str] = field(default_factory=list)
    related: list[RelatedDocument] = field(default_factory=list)
    ocr_used: bool = False


def _title_from_filename(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Untitled contract"


class IngestionService:
    def __init__(self, principal: Principal, repos: Repositories, storage: FileStorage, indexer: DocumentIndexer,
                 ocr: OcrEngine, settings: Settings, audit: AuditService) -> None:
        self._p = principal
        self._repos = repos
        self._storage = storage
        self._indexer = indexer
        self._ocr = ocr
        self._settings = settings
        self._audit = audit

    # ------------------------------------------------------------------ public API
    def ingest_file(self, path: str | Path, **kwargs) -> IngestionResult:
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError as exc:
            raise DocumentProcessingError(f"cannot read {p}: {exc}", user_message=f"The file '{p.name}' could not be read.") from exc
        return self.ingest_bytes(p.name, data, **kwargs)

    def ingest_bytes(self, filename: str, data: bytes, *, contract_id: UUID | str | None = None,
                     role: DocumentRole = DocumentRole.ORIGINAL, label: str | None = None, title: str | None = None,
                     is_demo: bool = False, on_progress: ProgressFn | None = None) -> IngestionResult:
        self._p.require(Permission.CONTRACTS_WRITE)
        notify = on_progress or (lambda stage, frac, msg: None)
        notify("validate", 0.02, "Validating file")
        validated = validate_upload(filename, data, max_bytes=self._settings.max_upload_mb * 1_048_576)
        existing = self._repos.documents.first([F.eq("file_hash", validated.sha256)])
        if existing is not None:
            raise DuplicateDocumentError(
                f"duplicate of document {existing.id}",
                existing_document_id=str(existing.id), existing_contract_id=str(existing.contract_id),
            )

        created_contract = False
        try:
            contract, version = self._prepare_contract(contract_id, filename, title, role, label, is_demo)
            created_contract = contract_id is None
            doc_id = uuid4()
            path = object_path(str(self._p.org_id), str(contract.id), str(doc_id), validated.filename)
            document = self._repos.documents.add(Document(
                id=doc_id, org_id=self._p.org_id, contract_id=contract.id, contract_version_id=version.id, filename=validated.filename,
                mime_type=validated.mime_type, size_bytes=validated.size_bytes, file_hash=validated.sha256, storage_path=path,
                indexing_status=IndexingStatus.EXTRACTING, heartbeat_at=datetime.now(timezone.utc), uploaded_by=self._p.user_id,
                metadata={"validation_warnings": list(validated.warnings)},
            ))
            notify("store", 0.06, "Storing original file")
            self._storage.put(path, data, validated.mime_type)
        except ContractLensError:
            raise
        # from here on the document row exists; failures clean up
        try:
            result = self._process(document, data, contract, version, notify)
        except Exception as exc:
            self._fail(document, exc)
            self._cleanup_failed(document, contract if created_contract else None)
            raise
        self._audit.record("document.upload", "document", document.id, filename=validated.filename, sha256=validated.sha256,
                           pages=result.page_count, chunks=result.chunk_count)
        notify("done", 1.0, "Document indexed and searchable")
        return result

    def recover_interrupted(self, stale_after: timedelta = timedelta(minutes=10), *, max_attempts: int = 3,
                            on_progress: ProgressFn | None = None) -> list[str]:
        """Resume documents whose indexing stalled (crash, kill, network loss). Returns recovered document ids."""
        cutoff = datetime.now(timezone.utc) - stale_after
        stale = []
        for d in self._repos.documents.list([F.in_("indexing_status", [s.value for s in IN_PROGRESS_INDEXING])]):
            hb = d.heartbeat_at
            if hb is not None and hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            if hb is None or hb < cutoff:
                stale.append(d)
        recovered: list[str] = []
        for d in stale:
            if d.index_attempts >= max_attempts:
                self._repos.documents.update(d.id, indexing_status=IndexingStatus.FAILED, indexing_error="Recovery attempts exhausted. Delete and re-upload the document.")
                continue
            try:
                self._repos.documents.update(d.id, index_attempts=d.index_attempts + 1, heartbeat_at=datetime.now(timezone.utc))
                self._resume(d, on_progress or (lambda *_: None))
                recovered.append(str(d.id))
                self._audit.record("document.recovered", "document", d.id)
            except Exception as exc:  # noqa: BLE001
                log.exception("recovery failed for document %s", d.id)
                self._fail(d, exc)
        return recovered

    def reindex_document(self, document_id: UUID | str, *, on_progress: ProgressFn | None = None) -> None:
        """Rebuild the vector index for a document from its stored chunks (e.g. after changing embedding model)."""
        self._p.require(Permission.CONTRACTS_WRITE)
        doc = self._repos.documents.require(document_id)
        self._repos.chunks.update_where([F.eq("document_id", str(doc.id))], embedded=False)
        self._indexer.remove_document(str(doc.id))
        self._indexer.index_document(doc, force=True, on_progress=(lambda f, m: (on_progress or (lambda *_: None))("embed", f, m)))

    def delete_document(self, document_id: UUID | str) -> None:
        self._p.require(Permission.CONTRACTS_DELETE)
        doc = self._repos.documents.require(document_id)
        self._indexer.remove_document(str(doc.id))
        try:
            self._storage.delete(doc.storage_path)
        except ContractLensError:
            log.warning("could not delete stored file for %s", doc.id)
        self._repos.documents.delete(doc.id)
        self._audit.record("document.delete", "document", doc.id, filename=doc.filename)

    # ------------------------------------------------------------------ internals
    def _prepare_contract(self, contract_id, filename, title, role, label, is_demo) -> tuple[Contract, ContractVersion]:
        if contract_id is None:
            contract = self._repos.contracts.add(Contract(
                org_id=self._p.org_id, title=title or _title_from_filename(filename), status=ContractStatus.DRAFT,
                created_by=self._p.user_id, owner_id=self._p.user_id, is_demo=is_demo,
            ))
            version = self._repos.versions.add(ContractVersion(
                org_id=self._p.org_id, contract_id=contract.id, version_number=1, label=label or "Original", document_role=role,
                created_by=self._p.user_id, is_current=True,
            ))
        else:
            contract = self._repos.contracts.require(contract_id)
            versions = self._repos.versions.list([F.eq("contract_id", str(contract.id))], order_by=[("version_number", True)])
            number = (versions[0].version_number + 1) if versions else 1
            partial = role in PARTIAL_ROLES
            # partial documents (amendments, addenda, schedules) modify the current full agreement; they never replace it
            full = [v for v in versions if v.document_role not in PARTIAL_ROLES]
            previous = next((v for v in full if v.is_current), full[0] if full else None)
            version = self._repos.versions.add(ContractVersion(
                org_id=self._p.org_id, contract_id=contract.id, version_number=number,
                label=label or (f"{role.value.title()} {number}" if number > 1 else "Original"), document_role=role,
                supersedes_version_id=previous.id if previous else None, created_by=self._p.user_id, is_current=not partial,
            ))
            if previous and not partial:
                self._repos.versions.update(previous.id, is_current=False)
            if partial:
                return contract, version
        self._repos.contracts.update(contract.id, current_version_id=version.id)
        return contract, version

    def _heartbeat(self, doc: Document, status: IndexingStatus, progress: float | None = None) -> None:
        values = {"indexing_status": status, "heartbeat_at": datetime.now(timezone.utc)}
        if progress is not None:
            values["indexing_progress"] = progress
        self._repos.documents.update(doc.id, **values)

    def _process(self, document: Document, data: bytes, contract: Contract, version: ContractVersion, notify: ProgressFn) -> IngestionResult:
        notify("extract", 0.10, "Extracting text and layout")
        parsed = parse_document(document.filename, data, ocr=self._ocr, max_pages=self._settings.max_pages, ocr_dpi=self._settings.ocr_dpi)
        if parsed.ocr_used:
            self._heartbeat(document, IndexingStatus.OCR)
        notify("chunk", 0.35, "Detecting sections and chunking")
        self._heartbeat(document, IndexingStatus.CHUNKING, 0.35)
        chunks = self._persist_chunks(document, parsed)
        related = self._find_related(document, parsed)
        self._repos.documents.update(
            document.id, page_count=len(parsed.pages), page_basis=parsed.page_basis, is_scanned=parsed.is_scanned, ocr_used=parsed.ocr_used,
            ocr_confidence=parsed.ocr_confidence, chunk_count=len(chunks),
            metadata={**document.metadata, "warnings": parsed.warnings, "pdf_metadata": parsed.metadata,
                      "tables": sum(p.tables for p in parsed.pages), "related": [r.__dict__ for r in related]},
        )
        notify("embed", 0.45, "Generating embeddings")
        self._heartbeat(document, IndexingStatus.EMBEDDING, 0.45)
        fresh = self._repos.documents.require(document.id)
        self._indexer.index_document(fresh, on_progress=lambda f, m: notify("embed", 0.45 + 0.5 * f, m))
        return IngestionResult(
            contract_id=contract.id, version_id=version.id, document_id=document.id, chunk_count=len(chunks),
            page_count=len(parsed.pages), warnings=list(parsed.warnings) + list(document.metadata.get("validation_warnings", [])),
            related=related, ocr_used=parsed.ocr_used,
        )

    def _persist_chunks(self, document: Document, parsed: ParsedDocument) -> list[DocumentChunk]:
        seed = f"{self._p.org_id}:{document.file_hash}"
        chunks = chunk_document(parsed, seed, target_chars=self._settings.chunk_target_chars, overlap_chars=self._settings.chunk_overlap_chars)
        if not chunks:
            raise DocumentProcessingError("no chunks produced", user_message="No searchable text could be produced from this document.")
        self._repos.chunks.delete_where([F.eq("document_id", str(document.id))])
        rows = [DocumentChunk(
            id=c.id, org_id=self._p.org_id, document_id=document.id, contract_id=document.contract_id, contract_version_id=document.contract_version_id,
            chunk_index=c.index, text=c.text, page_number=c.page_number, page_end=c.page_end, section_reference=c.section_reference,
            section_title=c.section_title, clause_type=c.clause_type, char_start=c.char_start, char_end=c.char_end,
            token_estimate=c.token_estimate, text_hash=c.text_hash, embedded=False,
        ) for c in chunks]
        for i in range(0, len(rows), 200):
            self._repos.chunks.add_many(rows[i : i + 200])
        return rows

    def _find_related(self, document: Document, parsed: ParsedDocument) -> list[RelatedDocument]:
        text = parsed.assemble()[0][:6000]
        related: list[RelatedDocument] = []
        others = self._repos.documents.list([F.neq("id", str(document.id)), F.eq("indexing_status", IndexingStatus.INDEXED)], limit=200)
        for o in others:
            first = self._repos.chunks.list([F.eq("document_id", str(o.id)), F.lt("chunk_index", 5)], order_by=[("chunk_index", False)], limit=5)
            if not first:
                continue
            j, c = relation_score(text, " ".join(ch.text for ch in first))
            if j >= 0.35 or c >= 0.6:
                related.append(RelatedDocument(str(o.id), str(o.contract_id), o.filename, round(j, 3), round(c, 3)))
        return sorted(related, key=lambda r: r.containment, reverse=True)[:5]

    def _resume(self, doc: Document, notify: ProgressFn) -> None:
        """Resume from the furthest completed stage."""
        chunks = self._repos.chunks.count([F.eq("document_id", str(doc.id))])
        if chunks:
            self._heartbeat(doc, IndexingStatus.EMBEDDING)
            self._indexer.index_document(self._repos.documents.require(doc.id), on_progress=lambda f, m: notify("embed", f, m))
            return
        data = self._storage.get(doc.storage_path)
        verify_hash(data, doc.file_hash)
        contract = self._repos.contracts.require(doc.contract_id)
        version = self._repos.versions.require(doc.contract_version_id)
        self._process(doc, data, contract, version, notify)

    def _fail(self, doc: Document, exc: BaseException) -> None:
        message = exc.user_message if isinstance(exc, ContractLensError) else "Processing failed unexpectedly. See the application log."
        if not isinstance(exc, ContractLensError):
            log.exception("document processing failed for %s", doc.id)
        try:
            self._repos.documents.update(doc.id, indexing_status=IndexingStatus.FAILED, indexing_error=message, heartbeat_at=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            log.exception("could not record failure for %s", doc.id)

    def _cleanup_failed(self, doc: Document, contract: Contract | None) -> None:
        """Remove shells of a failed first upload so the user can retry the same file cleanly."""
        try:
            self._indexer.remove_document(str(doc.id))
            self._storage.delete(doc.storage_path)
            if contract is not None:
                self._repos.contracts.delete(contract.id)  # cascades to version, document, chunks
            else:
                self._repos.documents.delete(doc.id)
        except Exception:  # noqa: BLE001
            log.exception("cleanup after failed ingestion incomplete for %s", doc.id)
