"""Evidence verification: every quote a model returns is checked against the source text.

A quote is *verified* only if it appears verbatim in a source passage (modulo whitespace, case,
quote-style and dash normalisation). Verified evidence stores the **source slice**, not the model's
wording, together with document-level character offsets.
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.schemas.llm_outputs import QuoteRef
from app.tools.normalize import _QUOTE_MAP


@dataclass
class ChunkView:
    """Immutable view of a persisted chunk as seen by agents."""

    id: UUID
    document_id: UUID
    contract_id: UUID
    version_id: UUID
    index: int
    text: str
    page_number: int
    section_reference: str | None
    section_title: str | None
    clause_type: str
    char_start: int
    char_end: int


@dataclass
class ResolvedEvidence:
    chunk_id: UUID | None
    document_id: UUID | None
    quote: str
    char_start: int | None  # document-level offsets
    char_end: int | None
    page_number: int | None
    section_reference: str | None
    verified: bool
    method: str  # exact | relabelled | unverified

    @property
    def quote_hash(self) -> str:
        return hashlib.sha256(self.quote.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_id": str(self.chunk_id) if self.chunk_id else None, "quote": self.quote, "page": self.page_number,
                "section": self.section_reference, "verified": self.verified, "method": self.method}


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise ``text`` and return, for every normalised character, its index in the original."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_QUOTE_MAP).casefold():
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalise(text: str) -> str:
    return _normalise_with_map(text)[0]


def locate_quote(chunk_text: str, quote: str) -> tuple[int, int] | None:
    """Return (start, end) offsets of ``quote`` within ``chunk_text``, tolerant of formatting differences only."""
    nq = normalise(quote)
    if len(nq) < 8:  # too short to be meaningful evidence
        return None
    ntext, idx = _normalise_with_map(chunk_text)
    pos = ntext.find(nq)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(nq) - 1] + 1


def resolve_quote(ref: QuoteRef, labels: dict[str, ChunkView]) -> ResolvedEvidence:
    """Verify one model-supplied quote. Falls back to searching sibling passages if the label was wrong."""
    chunk = labels.get(ref.chunk_label.strip().upper())
    if chunk is not None:
        loc = locate_quote(chunk.text, ref.quote)
        if loc:
            return _ok(chunk, loc, "exact")
    for other in labels.values():
        if other is chunk:
            continue
        loc = locate_quote(other.text, ref.quote)
        if loc:
            return _ok(other, loc, "relabelled")
    return ResolvedEvidence(chunk.id if chunk else None, chunk.document_id if chunk else None, ref.quote.strip()[:400], None, None,
                            chunk.page_number if chunk else None, chunk.section_reference if chunk else None, False, "unverified")


def _ok(chunk: ChunkView, loc: tuple[int, int], method: str) -> ResolvedEvidence:
    s, e = loc
    return ResolvedEvidence(chunk.id, chunk.document_id, chunk.text[s:e], chunk.char_start + s, chunk.char_start + e, chunk.page_number,
                            chunk.section_reference, True, method)


def resolve_all(refs: list[QuoteRef], labels: dict[str, ChunkView]) -> list[ResolvedEvidence]:
    seen: set[str] = set()
    out: list[ResolvedEvidence] = []
    for r in refs:
        ev = resolve_quote(r, labels)
        key = ev.quote_hash
        if key not in seen:
            seen.add(key)
            out.append(ev)
    return out
