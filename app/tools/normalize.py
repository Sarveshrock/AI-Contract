"""Text normalisation for extracted contract pages (deterministic, offset-preserving downstream)."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.security.prompt_guard import sanitize

_PAGE_NUM = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$|^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$", re.IGNORECASE)
_STRUCTURE_START = re.compile(
    r"^\s*(?:\((?:[a-z]{1,4}|\d{1,3})\)\s+|\d+(?:\.\d+)+\.?\s+|\d{1,2}\.\s+|article\s+[ivxlc\d]+|section\s+\d+|schedule\s+\w+|exhibit\s+\w+|annex\s+\w+|appendix\s+\w+|•|-\s+)",
    re.IGNORECASE,
)
_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " ", "−": "-"})


def normalize_for_match(text: str) -> str:
    """Canonical form used to compare model-returned quotes against source text."""
    t = unicodedata.normalize("NFKC", text).translate(_QUOTE_MAP)
    return re.sub(r"\s+", " ", t).strip().casefold()


def normalize_page_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", sanitize(raw))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)  # de-hyphenate soft line breaks
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    for i, line in enumerate(lines):
        if not line:
            out.append("")
            continue
        if out and out[-1] != "" and not _starts_new_block(out[-1], line):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    joined = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _starts_new_block(prev: str, line: str) -> bool:
    if _STRUCTURE_START.match(line):
        return True
    if line.isupper() and len(line) < 80:
        return True
    if prev.isupper() and len(prev) < 80:
        return True
    if len(prev) < 45 and prev.endswith((".", ":", ";")):
        return True
    return False


def strip_repeated_lines(pages: list[str], *, min_pages: int = 2, ratio: float = 0.6, edge_lines: int = 3) -> list[str]:
    """Remove running headers/footers and bare page numbers (edge lines repeating on most pages)."""
    if len(pages) < min_pages:
        return [_drop_page_numbers(p) for p in pages]
    key = lambda s: re.sub(r"\d+", "#", s.strip().lower())  # noqa: E731
    counts: Counter[str] = Counter()
    for p in pages:
        lines = [ln for ln in p.split("\n") if ln.strip()]
        edges = set(key(ln) for ln in lines[:edge_lines] + lines[-edge_lines:])
        counts.update(edges)
    repeated = {k for k, c in counts.items() if c / len(pages) >= ratio and len(k) > 2}
    cleaned: list[str] = []
    for p in pages:
        lines = p.split("\n")
        nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
        edge_idx = set(nonblank[:edge_lines] + nonblank[-edge_lines:])
        kept = [ln for i, ln in enumerate(lines) if not (i in edge_idx and key(ln) in repeated)]
        cleaned.append(_drop_page_numbers("\n".join(kept)))
    return cleaned


def _drop_page_numbers(page: str) -> str:
    lines = page.split("\n")
    nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
    drop = {i for i in nonblank[:1] + nonblank[-1:] if _PAGE_NUM.match(lines[i])}
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop).strip()
