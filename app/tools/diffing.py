"""Deterministic clause-level comparison of two contract versions."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.agents.evidence import ChunkView, normalise
from app.agents.state import ClauseDiff

HIGH_IMPACT = {"payment", "renewal", "termination", "liability", "indemnity", "governing_law", "sla", "term", "data_protection"}
_NUM = re.compile(r"\d")


@dataclass
class SectionText:
    reference: str
    title: str
    text: str
    clause_type: str


def assemble_sections(chunks: list[ChunkView]) -> list[SectionText]:
    """Rebuild section texts from chunks (dropping the overlap between consecutive chunks)."""
    ordered = sorted(chunks, key=lambda c: (str(c.document_id), c.char_start))
    sections: list[SectionText] = []
    last_end: dict[str, int] = {}
    seen_refs: dict[str, int] = {}
    for c in ordered:
        doc = str(c.document_id)
        start = max(c.char_start, last_end.get(doc, c.char_start))
        piece = c.text[start - c.char_start:] if start > c.char_start else c.text
        last_end[doc] = max(last_end.get(doc, 0), c.char_end)
        if not piece.strip():
            continue
        ref = c.section_reference or "-"
        if sections and sections[-1].reference.split("#")[0] == ref and sections[-1].title == (c.section_title or ""):
            sections[-1].text += "\n" + piece
        else:
            n = seen_refs.get(ref, 0)
            seen_refs[ref] = n + 1
            sections.append(SectionText(f"{ref}#{n}" if n else ref, c.section_title or "", piece, c.clause_type))
    return sections


def _words(text: str) -> list[str]:
    return normalise(text).split()


def _replacements(old: str, new: str, limit: int = 4) -> list[tuple[str, str]]:
    a, b = old.split(), new.split()
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        o, n = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        out.append((o, n))
        if len(out) >= limit:
            break
    return out


def describe_change(old: str, new: str) -> str:
    parts: list[str] = []
    for o, n in _replacements(old, new):
        if o and n:
            parts.append(f"'{_clip(o)}' → '{_clip(n)}'")
        elif n:
            parts.append(f"added '{_clip(n)}'")
        else:
            parts.append(f"removed '{_clip(o)}'")
    return "; ".join(parts) or "wording changed"


def _clip(s: str, n: int = 90) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def diff_sections(old: list[SectionText], new: list[SectionText]) -> list[ClauseDiff]:
    old_by = {s.reference: s for s in old}
    new_by = {s.reference: s for s in new}
    matched_new: set[str] = set()
    pairs: list[tuple[SectionText | None, SectionText | None]] = []
    for s in old:
        if s.reference in new_by:
            pairs.append((s, new_by[s.reference]))
            matched_new.add(s.reference)
        else:
            pairs.append((s, None))
    # rescue renumbered / re-titled sections by content similarity
    unmatched_old = [i for i, (o, n) in enumerate(pairs) if n is None]
    leftovers = [s for s in new if s.reference not in matched_new]
    for i in unmatched_old:
        o = pairs[i][0]
        assert o is not None
        best, best_ratio = None, 0.0
        for cand in leftovers:
            ratio = difflib.SequenceMatcher(None, _words(o.text), _words(cand.text), autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = cand, ratio
        if best is not None and best_ratio >= 0.72:
            pairs[i] = (o, best)
            leftovers.remove(best)
    for cand in leftovers:
        pairs.append((None, cand))

    diffs: list[ClauseDiff] = []
    for o, n in pairs:
        if o and n:
            wa, wb = _words(o.text), _words(n.text)
            ratio = difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()
            if wa == wb:
                continue
            ctype = n.clause_type if n.clause_type != "other" else o.clause_type
            numeric = any(_NUM.search(x + y) for x, y in _replacements(o.text, n.text))
            mat = "high" if (ctype in HIGH_IMPACT and numeric) else "medium" if ctype in HIGH_IMPACT or numeric else "low"
            diffs.append(ClauseDiff("modified", o.reference.split("#")[0], n.reference.split("#")[0], n.title or o.title, ctype, round(ratio, 3),
                                    o.text, n.text, describe_change(o.text, n.text), mat))
        elif o:
            diffs.append(ClauseDiff("removed", o.reference.split("#")[0], None, o.title, o.clause_type, 0.0, o.text, "",
                                    f"Section {o.reference.split('#')[0]} ({o.title or 'untitled'}) no longer appears.", "high" if o.clause_type in HIGH_IMPACT else "medium"))
        elif n:
            diffs.append(ClauseDiff("added", None, n.reference.split("#")[0], n.title, n.clause_type, 0.0, "", n.text,
                                    f"New section {n.reference.split('#')[0]} ({n.title or 'untitled'}) was added.", "high" if n.clause_type in HIGH_IMPACT else "medium"))
    return diffs
