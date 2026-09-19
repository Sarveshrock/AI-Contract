from __future__ import annotations

import io
import zipfile
from datetime import timedelta

import pytest

from app.core.errors import DocumentProcessingError, DuplicateDocumentError, FileValidationError, OCRUnavailableError
from app.database.store import F
from app.models.enums import DocumentRole, IndexingStatus
from app.rag.chunker import chunk_document
from app.security.file_validation import validate_upload, verify_hash
from app.tools.normalize import normalize_for_match, normalize_page_text, strip_repeated_lines
from app.tools.parsers import parse_docx, parse_document, parse_pdf
from app.tools.sections import detect_sections
from tests.helpers import FakeOcr, build_stack


@pytest.fixture()
def msa_pdf(sample_dir) -> bytes:
    return (sample_dir / "msa_v1.pdf").read_bytes()


# ---------------------------------------------------------------- parsing
def test_pdf_extraction_preserves_pages_and_text(msa_pdf):
    parsed = parse_pdf(msa_pdf)
    assert len(parsed.pages) >= 2
    assert [p.number for p in parsed.pages] == list(range(1, len(parsed.pages) + 1))
    text, spans = parsed.assemble()
    assert "Master Services Agreement" in text
    assert "ninety (90) days" in normalize_for_match(text) or "ninety (90) days" in text.replace("\n", " ")
    assert spans[0].start == 0 and spans[-1].end == len(text)
    assert parsed.page_basis == "physical" and not parsed.is_scanned
    # running footer ("... - Page N") is stripped
    assert "Confidential - Page" not in text


def test_docx_extraction(sample_dir):
    parsed = parse_docx((sample_dir / "msa_v2_restated.docx").read_bytes())
    text, _ = parsed.assemble()
    assert "AMENDED AND RESTATED" in text
    assert "forty-five (45) days" in text
    assert parsed.page_basis == "logical"
    assert any("logical" in w for w in parsed.warnings)


def test_scanned_pdf_without_ocr_is_a_clear_error(sample_dir):
    with pytest.raises(OCRUnavailableError) as exc:
        parse_pdf((sample_dir / "scanned_nda_image_only.pdf").read_bytes())
    assert "OCR" in exc.value.user_message


def test_ocr_fallback_used_for_image_only_pdf(sample_dir):
    ocr = FakeOcr("MUTUAL NON-DISCLOSURE AGREEMENT\nThis agreement is effective as of September 1, 2025 between Acme and Orion.", 0.9)
    parsed = parse_pdf((sample_dir / "scanned_nda_image_only.pdf").read_bytes(), ocr=ocr)
    assert ocr.calls == 1 and parsed.ocr_used and parsed.is_scanned
    assert parsed.ocr_confidence == pytest.approx(0.9)
    assert "September 1, 2025" in parsed.pages[0].text


def test_malformed_and_encrypted_files(sample_dir):
    with pytest.raises(DocumentProcessingError):
        parse_pdf(b"%PDF-1.4 this is not really a pdf")
    with pytest.raises(DocumentProcessingError):
        parse_docx(b"PK\x03\x04garbage")
    import pymupdf

    doc = pymupdf.open(stream=(sample_dir / "msa_v1.pdf").read_bytes(), filetype="pdf")
    enc = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(DocumentProcessingError) as exc:
        parse_pdf(enc)
    assert "password" in exc.value.user_message


def test_parse_document_dispatch_rejects_unknown():
    with pytest.raises(DocumentProcessingError):
        parse_document("x.txt", b"hello")


# ---------------------------------------------------------------- validation & hashing
def test_upload_validation_rules(msa_pdf):
    ok = validate_upload("a.pdf", msa_pdf, max_bytes=10_000_000)
    assert ok.mime_type == "application/pdf" and len(ok.sha256) == 64
    verify_hash(msa_pdf, ok.sha256)
    with pytest.raises(FileValidationError):
        verify_hash(msa_pdf + b"x", ok.sha256)
    with pytest.raises(FileValidationError):
        validate_upload("a.exe", b"MZ....", max_bytes=1000)
    with pytest.raises(FileValidationError):
        validate_upload("a.pdf", b"not a pdf", max_bytes=1000)
    with pytest.raises(FileValidationError):
        validate_upload("a.pdf", b"", max_bytes=1000)
    with pytest.raises(FileValidationError):
        validate_upload("a.pdf", msa_pdf, max_bytes=100)
    with pytest.raises(FileValidationError):
        validate_upload("a.pdf", b"%PDF-1.7\n/Launch /Action", max_bytes=1000)


def test_docx_macro_and_zipbomb_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", "<x/>")
        z.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(FileValidationError):
        validate_upload("m.docx", buf.getvalue(), max_bytes=10_000_000)
    with pytest.raises(FileValidationError):
        validate_upload("m.docx", b"PK\x03\x04broken", max_bytes=10_000_000)


# ---------------------------------------------------------------- normalisation / sections / chunks
def test_normalization_joins_wrapped_lines_and_dehyphenates():
    raw = "The Customer shall pay each undisputed\ninvoice within thirty (30) days after re-\nceipt of the invoice.\n3.3 Late Payment. Overdue\namounts accrue interest."
    out = normalize_page_text(raw)
    assert "undisputed invoice within" in out and "receipt of the invoice." in out
    assert "\n3.3 Late Payment. Overdue amounts accrue interest." in "\n" + out


def test_repeated_headers_removed():
    pages = [f"ACME CONFIDENTIAL\nBody text of page {i} with enough words.\nPage {i} of 4" for i in range(1, 5)]
    cleaned = strip_repeated_lines(pages)
    assert all("ACME CONFIDENTIAL" not in p and "Page" not in p for p in cleaned)


def test_section_detection_on_sample(msa_pdf):
    text, _ = parse_pdf(msa_pdf).assemble()
    refs = [s.reference for s in detect_sections(text)]
    for expected in ("2.2", "3.2", "8.1", "10.1"):
        assert expected in refs


def test_chunks_are_exact_slices_with_metadata(msa_pdf):
    parsed = parse_pdf(msa_pdf)
    text, _ = parsed.assemble()
    chunks = chunk_document(parsed, "seed", target_chars=600, overlap_chars=100)
    assert len(chunks) > 5
    assert len({c.id for c in chunks}) == len(chunks)
    for c in chunks:
        assert text[c.char_start:c.char_end] == c.text
        assert 1 <= c.page_number <= c.page_end <= len(parsed.pages)
        assert c.section_reference
    renewal = next(c for c in chunks if "automatically renews" in c.text)
    assert renewal.section_reference == "2.2" and renewal.clause_type.value == "renewal"
    assert chunk_document(parsed, "seed", target_chars=600, overlap_chars=100)[3].id == chunks[3].id  # deterministic


# ---------------------------------------------------------------- ingestion pipeline
def test_ingest_end_to_end(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    progress = []
    res = s.ingestion.ingest_file(sample_dir / "msa_v1.pdf", on_progress=lambda st, f, m: progress.append((st, f)))
    doc = s.repos.documents.require(res.document_id)
    assert doc.indexing_status == IndexingStatus.INDEXED and doc.chunk_count == res.chunk_count > 0
    assert doc.embedding_model == s.embedder.name and doc.page_count == res.page_count
    assert s.vectors.count(str(principal.org_id)) == res.chunk_count
    assert s.repos.chunks.count([F.eq("embedded", True)]) == res.chunk_count
    assert [p[0] for p in progress][0] == "validate" and progress[-1][0] == "done"
    assert s.repos.versions.count() == 1 and s.repos.contracts.require(res.contract_id).current_version_id == res.version_id
    assert s.storage.get(doc.storage_path)[:5] == b"%PDF-"
    assert s.repos.audit.count([F.eq("action", "document.upload")]) == 1


def test_duplicate_file_detected(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    first = s.ingestion.ingest_file(sample_dir / "mutual_nda.pdf")
    with pytest.raises(DuplicateDocumentError) as exc:
        s.ingestion.ingest_file(sample_dir / "mutual_nda.pdf")
    assert exc.value.existing_document_id == str(first.document_id)
    assert s.repos.contracts.count() == 1


def test_failed_ingestion_cleans_up_and_allows_retry(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    with pytest.raises(OCRUnavailableError):
        s.ingestion.ingest_file(sample_dir / "scanned_nda_image_only.pdf")
    assert s.repos.contracts.count() == 0 and s.repos.documents.count() == 0
    s2 = build_stack(store, principal, tmp_path, ocr=FakeOcr("MUTUAL NON-DISCLOSURE AGREEMENT\n1. PURPOSE\n1.1 The Parties may disclose Confidential Information to each other for evaluation purposes only under this Agreement."))
    res = s2.ingestion.ingest_file(sample_dir / "scanned_nda_image_only.pdf")
    assert res.ocr_used
    assert s2.repos.documents.require(res.document_id).ocr_used is True


def test_second_upload_becomes_new_version_and_related_detected(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    v1 = s.ingestion.ingest_file(sample_dir / "msa_v1.pdf")
    v2 = s.ingestion.ingest_file(sample_dir / "msa_v2_restated.docx", contract_id=v1.contract_id, role=DocumentRole.REVISED, label="Restated")
    versions = s.repos.versions.list([F.eq("contract_id", str(v1.contract_id))], order_by=[("version_number", False)])
    assert [v.version_number for v in versions] == [1, 2]
    assert versions[0].is_current is False and versions[1].is_current and versions[1].supersedes_version_id == versions[0].id
    assert s.repos.contracts.require(v1.contract_id).current_version_id == v2.version_id
    # unrelated upload as a new contract still flags similarity to the base agreement
    other = s.ingestion.ingest_file(sample_dir / "msa_amendment_1.pdf")
    assert other.contract_id != v1.contract_id


def test_recover_interrupted_indexing(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    res = s.ingestion.ingest_file(sample_dir / "saas_subscription.pdf")
    doc = s.repos.documents.require(res.document_id)
    # simulate a crash mid-embedding: vectors lost, flags reset, heartbeat stale
    s.vectors.delete(str(principal.org_id), {"organization_id": str(principal.org_id)})
    s.repos.chunks.update_where([F.eq("document_id", str(doc.id))], embedded=False)
    s.repos.documents.update(doc.id, indexing_status=IndexingStatus.EMBEDDING, indexing_progress=0.2,
                             heartbeat_at=doc.updated_at - timedelta(hours=1))
    assert s.vectors.count(str(principal.org_id)) == 0
    recovered = s.ingestion.recover_interrupted(stale_after=timedelta(minutes=10))
    assert recovered == [str(doc.id)]
    assert s.repos.documents.require(doc.id).indexing_status == IndexingStatus.INDEXED
    assert s.vectors.count(str(principal.org_id)) == res.chunk_count
    # a fresh heartbeat is not touched
    s.repos.documents.update(doc.id, indexing_status=IndexingStatus.EMBEDDING, heartbeat_at=doc.updated_at + timedelta(hours=1))
    assert s.ingestion.recover_interrupted(stale_after=timedelta(minutes=10)) == []


def test_recover_reprocesses_from_storage_when_no_chunks(store, principal, tmp_path, sample_dir):
    s = build_stack(store, principal, tmp_path)
    res = s.ingestion.ingest_file(sample_dir / "mutual_nda.pdf")
    doc = s.repos.documents.require(res.document_id)
    s.repos.chunks.delete_where([F.eq("document_id", str(doc.id))])
    s.vectors.delete(str(principal.org_id), {"organization_id": str(principal.org_id)})
    s.repos.documents.update(doc.id, indexing_status=IndexingStatus.CHUNKING, heartbeat_at=doc.updated_at - timedelta(hours=2))
    assert s.ingestion.recover_interrupted() == [str(doc.id)]
    assert s.repos.chunks.count() == res.chunk_count and s.vectors.count(str(principal.org_id)) == res.chunk_count


def test_viewer_cannot_upload(store, principal, tmp_path, sample_dir):
    from app.models.enums import Role
    from app.security.access import Principal
    from app.core.errors import AuthorizationError

    viewer = Principal(user_id=principal.user_id, email="v@x.test", org_id=principal.org_id, role=Role.VIEWER)
    s = build_stack(store, viewer, tmp_path)
    with pytest.raises(AuthorizationError):
        s.ingestion.ingest_file(sample_dir / "msa_v1.pdf")


def test_real_tesseract_reads_scanned_pdf(sample_dir):
    """Runs only where Tesseract is installed: the image-only NDA must yield its text with a sane confidence."""
    from app.tools.ocr import TesseractOcr

    engine = TesseractOcr()
    if not engine.is_available():
        pytest.skip("Tesseract not installed")
    parsed = parse_pdf((sample_dir / "scanned_nda_image_only.pdf").read_bytes(), ocr=engine, ocr_dpi=200)
    text = parsed.assemble()[0]
    assert parsed.ocr_used and parsed.is_scanned and (parsed.ocr_confidence or 0) > 0.7
    assert "NON-DISCLOSURE" in text.upper() and "September 1, 2025" in text and "New York" in text
