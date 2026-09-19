"""Section-aware chunking that preserves page and section metadata.

Every chunk text is an exact contiguous slice of the assembled document text
(``text[char_start:char_end]``), so evidence offsets always map back to the source.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from app.models.enums import ClauseType
from app.tools.clause_classifier import classify_clause
from app.tools.parsers import ParsedDocument, page_for_offset
from app.tools.sections import Section, detect_sections

_SENTENCE_END = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z0-9(\"'])")


@dataclass
class Chunk:
    id: UUID
    index: int
    text: str
    page_number: int
    page_end: int
    section_reference: str | None
    section_title: str | None
    clause_type: ClauseType
    char_start: int
    char_end: int
    token_estimate: int
    text_hash: str

    @property
    def embedding_text(self) -> str:
        head = " ".join(x for x in (self.section_reference, self.section_title) if x)
        return f"{head}\n{self.text}" if head else self.text


def chunk_id(id_seed: str, index: int) -> UUID:
    """Deterministic id so re-indexing is idempotent."""
    return uuid5(NAMESPACE_URL, f"contractlens:{id_seed}:{index}")


def _units(text: str, start: int, end: int, max_len: int) -> list[tuple[int, int]]:
    """Split [start, end) into sentence-ish units no longer than ``max_len`` (offsets into ``text``)."""
    units: list[tuple[int, int]] = []
    para_start = start
    for m in re.finditer(r"\n+", text[start:end]):
        para_end = start + m.start()
        units.extend(_split_paragraph(text, para_start, para_end, max_len))
        para_start = start + m.end()
    units.extend(_split_paragraph(text, para_start, end, max_len))
    return units


def _split_paragraph(text: str, start: int, end: int, max_len: int) -> list[tuple[int, int]]:
    if end <= start or not text[start:end].strip():
        return []
    if end - start <= max_len:
        return [(start, end)]
    out: list[tuple[int, int]] = []
    cur = start
    for m in _SENTENCE_END.finditer(text[start:end]):
        cut = start + m.start()
        if cut - cur >= max_len * 0.5 and cut > cur:
            out.append((cur, cut))
            cur = start + m.end()
    if cur < end:
        out.append((cur, end))
    # hard split any remaining oversize unit on whitespace
    final: list[tuple[int, int]] = []
    for s, e in out:
        while e - s > max_len:
            cut = text.rfind(" ", s, s + max_len)
            cut = cut if cut > s else s + max_len
            final.append((s, cut))
            s = cut + 1 if text[cut : cut + 1] == " " else cut
        final.append((s, e))
    return final


def chunk_document(parsed: ParsedDocument, id_seed: str, *, target_chars: int = 1200, overlap_chars: int = 150) -> list[Chunk]:
    full_text, spans = parsed.assemble()
    sections = detect_sections(full_text)
    # child sections ("5.1") inherit the clause type of their article heading ("5. LIABILITY") when their own text is inconclusive
    parents = {s.reference: classify_clause(s.title, s.title) for s in sections if "." not in s.reference and s.reference[:1].isdigit()}
    chunks: list[Chunk] = []
    for section in sections:
        parent = parents.get(section.reference.split(".")[0].split("#")[0]) if "." in section.reference else None
        chunks.extend(_chunk_section(full_text, spans, section, id_seed, target_chars, overlap_chars, offset=len(chunks), fallback_type=parent))
    return chunks


def _chunk_section(text: str, spans, section: Section, id_seed: str, target: int, overlap: int, offset: int, fallback_type: ClauseType | None = None) -> list[Chunk]:
    units = _units(text, section.start, section.end, target)
    if not units:
        return []
    groups: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    cur_len = 0
    fresh = 0  # units in ``cur`` that are not just overlap carried from the previous chunk
    for u in units:
        ulen = u[1] - u[0]
        if cur and fresh and cur_len + ulen > target:
            groups.append(cur)
            carry: list[tuple[int, int]] = []
            carried = 0
            for prev in reversed(cur):
                if carried + (prev[1] - prev[0]) > overlap:
                    break
                carry.insert(0, prev)
                carried += prev[1] - prev[0]
            cur, cur_len, fresh = carry, carried, 0
        cur.append(u)
        cur_len += ulen
        fresh += 1
    if cur and fresh:
        groups.append(cur)
    chunks: list[Chunk] = []
    for g in groups:
        s, e = g[0][0], g[-1][1]
        raw = text[s:e]
        lead = len(raw) - len(raw.lstrip())
        s, e = s + lead, e - (len(raw) - len(raw.rstrip()))
        body = text[s:e]
        if not body:
            continue
        idx = offset + len(chunks)
        ctype = classify_clause(body, section.title)
        if ctype is ClauseType.OTHER and fallback_type is not None:
            ctype = fallback_type
        chunks.append(Chunk(
            id=chunk_id(id_seed, idx), index=idx, text=body,
            page_number=page_for_offset(spans, s), page_end=page_for_offset(spans, max(s, e - 1)),
            section_reference=section.reference, section_title=section.title,
            clause_type=ctype, char_start=s, char_end=e,
            token_estimate=max(1, len(body) // 4), text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest()[:32],
        ))
    return chunks
