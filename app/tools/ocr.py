"""OCR engines. Tesseract is optional; its absence is reported clearly, never silently ignored."""
from __future__ import annotations

import io
import os
import shutil
import threading
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import OCRUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None  # 0..1


class OcrEngine(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def ocr_image(self, png_bytes: bytes) -> OcrResult: ...


_WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


def find_tesseract(explicit: str | None = None) -> str | None:
    """Locate the tesseract executable: explicit setting, PATH, then standard install locations."""
    if explicit and os.path.isfile(explicit):
        return explicit
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    return next((p for p in _WINDOWS_PATHS if os.path.isfile(p)), None)


class TesseractOcr:
    name = "tesseract"

    def __init__(self, cmd: str | None = None, languages: str = "eng") -> None:
        self._cmd = cmd
        self._languages = languages
        self._available: bool | None = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        with self._lock:
            if self._available is None:
                try:
                    import pytesseract

                    found = find_tesseract(self._cmd)
                    if found:
                        pytesseract.pytesseract.tesseract_cmd = found
                    pytesseract.get_tesseract_version()
                    self._available = True
                except Exception as exc:  # noqa: BLE001 - missing binary or package
                    log.warning("Tesseract OCR unavailable: %s", type(exc).__name__)
                    self._available = False
            return self._available

    def ocr_image(self, png_bytes: bytes) -> OcrResult:
        if not self.is_available():
            raise OCRUnavailableError(
                "tesseract not installed",
                user_message="This document needs OCR but Tesseract is not installed. Install Tesseract OCR (https://github.com/UB-Mannheim/tesseract/wiki) and set TESSERACT_CMD if it is not on PATH.",
            )
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(png_bytes))
        data = pytesseract.image_to_data(image, lang=self._languages, output_type=pytesseract.Output.DICT)
        words = [w for w in data["text"] if w.strip()]
        confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and str(c) not in ("-1", "-1.0")]
        text = pytesseract.image_to_string(image, lang=self._languages)
        confidence = (sum(confs) / len(confs) / 100.0) if confs else (0.0 if words else None)
        return OcrResult(text=text, confidence=confidence)


class UnavailableOcr:
    """Explicit null-object: reports unavailability instead of pretending to read pages."""

    name = "none"

    def is_available(self) -> bool:
        return False

    def ocr_image(self, png_bytes: bytes) -> OcrResult:
        raise OCRUnavailableError("no OCR engine configured", user_message="This document needs OCR but no OCR engine is configured.")
