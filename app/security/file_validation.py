"""Upload validation: extension allowlist, magic bytes, size, archive-bomb and active-content checks."""
from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import FileValidationError

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_ZIP_UNCOMPRESSED = 250 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000


@dataclass(frozen=True)
class ValidatedFile:
    filename: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_hash(data: bytes, expected: str) -> None:
    """Integrity check for downloaded originals; raises if the bytes differ from the recorded hash."""
    if sha256_hex(data) != expected:
        raise FileValidationError("document hash mismatch", user_message="The stored document no longer matches its recorded hash. It may have been altered.")


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> ValidatedFile:
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_TYPES:
        raise FileValidationError(f"extension {ext!r} not allowed", user_message=f"Unsupported file type '{ext or 'unknown'}'. Upload a PDF or DOCX file.")
    if not data:
        raise FileValidationError("empty file", user_message="The file is empty.")
    if len(data) > max_bytes:
        raise FileValidationError("file too large", user_message=f"The file is {len(data) / 1_048_576:.1f} MB; the limit is {max_bytes / 1_048_576:.0f} MB.")
    warnings: list[str] = []
    if ext == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise FileValidationError("bad pdf magic", user_message="This file is not a valid PDF (missing PDF header).")
        if b"/Launch" in data:
            raise FileValidationError("pdf launch action", user_message="The PDF contains a launch action and was rejected for safety.")
        if b"/JavaScript" in data or b"/JS" in data:
            warnings.append("PDF contains JavaScript; it is ignored (text extraction only).")
        if b"/Encrypt" in data:
            warnings.append("PDF appears to be encrypted; extraction may fail.")
    else:
        if data[:4] != b"PK\x03\x04":
            raise FileValidationError("bad docx magic", user_message="This file is not a valid DOCX document.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
                if len(infos) > MAX_ZIP_ENTRIES:
                    raise FileValidationError("too many zip entries", user_message="The DOCX archive has too many parts and was rejected.")
                if sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
                    raise FileValidationError("zip bomb", user_message="The DOCX expands to an unsafe size and was rejected.")
                names = {i.filename for i in infos}
                if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                    raise FileValidationError("missing docx parts", user_message="This file is not a valid DOCX document.")
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    raise FileValidationError("macros present", user_message="The document contains macros and was rejected for safety.")
                if any(n.startswith("word/embeddings/") for n in names):
                    warnings.append("DOCX contains embedded objects; they are ignored.")
        except zipfile.BadZipFile as exc:
            raise FileValidationError("bad zip", user_message="This DOCX file is corrupt.") from exc
    return ValidatedFile(filename=name, extension=ext, mime_type=ALLOWED_TYPES[ext], size_bytes=len(data), sha256=sha256_hex(data), warnings=tuple(warnings))
