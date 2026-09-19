"""Sensitive-data redaction for logs, run summaries and error messages."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_NUMBER]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[REDACTED_IBAN]"),
)
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
SENSITIVE_KEYS = ("password", "secret", "token", "api_key", "apikey", "authorization", "anon_key", "service_role", "cookie")


def redact_text(text: str, *, mask_emails: bool = True) -> str:
    """Mask credentials, national ids, card/IBAN-like numbers and (optionally) e-mail local parts."""
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    if mask_emails:
        out = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", out)
    return out


def redact_mapping(data: Any, *, mask_emails: bool = True) -> Any:
    """Recursively redact a JSON-like structure; values under sensitive keys are dropped."""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for key, value in data.items():
            if any(s in str(key).lower() for s in SENSITIVE_KEYS):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_mapping(value, mask_emails=mask_emails)
        return result
    if isinstance(data, (list, tuple)):
        return [redact_mapping(v, mask_emails=mask_emails) for v in data]
    if isinstance(data, str):
        return redact_text(data, mask_emails=mask_emails)
    return data
