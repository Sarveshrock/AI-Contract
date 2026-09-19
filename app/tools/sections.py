"""Section / clause detection over normalised contract text (deterministic)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ARTICLE = re.compile(r"^(ARTICLE|Article)\s+([IVXLC]+|\d+)\b[\s.:\-–—]*(.*)$")
_SECTION = re.compile(r"^(SECTION|Section)\s+(\d+(?:\.\d+)*)\b[\s.:\-–—]*(.*)$")
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.*)$")
_ANNEX = re.compile(r"^(EXHIBIT|SCHEDULE|ANNEX|APPENDIX|Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]{1,4})\b[\s.:\-–—]*(.*)$")
_UPPER = re.compile(r"^[A-Z][A-Z0-9 ,&/'\-]{3,70}$")
_TITLE_END = re.compile(r"^(.{3,90}?)[.:—–\-]\s+(?=[A-Z(])")


@dataclass
class Section:
    reference: str
    title: str
    level: int
    start: int
    end: int = 0


def _title_from(rest: str) -> str:
    rest = rest.strip()
    m = _TITLE_END.match(rest)
    if m and len(m.group(1).split()) <= 9:
        return m.group(1).strip()
    words = rest.split()
    if len(words) <= 9 and len(rest) < 90 and not rest.endswith((",", ";")):
        return rest.rstrip(".:")
    return " ".join(words[:8]).rstrip(",;:.") + ("…" if len(words) > 8 else "")


def detect_sections(text: str) -> list[Section]:
    """Split ``text`` into contiguous sections keyed by their contractual reference."""
    sections: list[Section] = [Section("Preamble", "Preamble", 0, 0)]
    pos = 0
    for line in text.split("\n"):
        stripped = line.strip()
        start = pos + (len(line) - len(line.lstrip()))
        pos += len(line) + 1
        if not stripped:
            continue
        m = _ARTICLE.match(stripped)
        if m:
            sections.append(Section(f"Article {m.group(2)}", m.group(3).strip().title() or f"Article {m.group(2)}", 1, start))
            continue
        m = _SECTION.match(stripped)
        if m:
            depth = m.group(2).count(".") + 1
            sections.append(Section(m.group(2), _title_from(m.group(3)) if m.group(3) else f"Section {m.group(2)}", depth + 1, start))
            continue
        m = _ANNEX.match(stripped)
        if m:
            sections.append(Section(f"{m.group(1).title()} {m.group(2)}", m.group(3).strip().title() or f"{m.group(1).title()} {m.group(2)}", 1, start))
            continue
        m = _NUMBERED.match(stripped)
        if m and _plausible_number(m.group(1)) and _plausible_heading(m.group(2)):
            depth = m.group(1).count(".") + 1
            sections.append(Section(m.group(1), _title_from(m.group(2)), depth, start))
            continue
        if _UPPER.match(stripped) and len(stripped.split()) <= 8 and not stripped.endswith(("LLC", "INC", "LTD", "CORP")):
            sections.append(Section(stripped.title(), stripped.title(), 1, start))
    # close sections
    result: list[Section] = []
    for i, s in enumerate(sections):
        s.end = sections[i + 1].start if i + 1 < len(sections) else len(text)
        if s.end > s.start and text[s.start : s.end].strip():
            result.append(s)
    if not result:
        result = [Section("Document", "Document", 0, 0, len(text))]
    return result


def _plausible_number(num: str) -> bool:
    first = int(num.split(".")[0])
    return 1 <= first <= 60


def _plausible_heading(rest: str) -> bool:
    """Reject lines that are dates, amounts or list continuations rather than clause starts."""
    if re.match(r"^(?:days?|months?|years?|%|percent|business|calendar)\b", rest, re.IGNORECASE):
        return False
    return bool(re.match(r"^[A-Z(\"']", rest))


def section_at(sections: list[Section], offset: int) -> Section:
    lo, hi = 0, len(sections) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sections[mid].start <= offset:
            lo = mid
        else:
            hi = mid - 1
    return sections[lo]
