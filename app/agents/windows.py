"""Builds prompt-ready windows of passages. Every passage carries a label the model must cite."""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.evidence import ChunkView
from app.security.prompt_guard import wrap_untrusted

KEY_CLAUSE_TYPES = ("term", "renewal", "termination", "payment", "governing_law", "sla", "liability", "indemnity", "confidentiality", "definitions")


@dataclass
class Window:
    chunks: list[ChunkView]
    labels: dict[str, ChunkView]
    text: str

    @property
    def chars(self) -> int:
        return sum(len(c.text) for c in self.chunks)


def _block(label: str, c: ChunkView) -> str:
    header = f"[{label}] section=\"{c.section_reference or '-'}\" title=\"{c.section_title or '-'}\" page={c.page_number}"
    return f"{header}\n{wrap_untrusted(c.text, 'PASSAGE')}"


def build_window(chunks: list[ChunkView]) -> Window:
    labels: dict[str, ChunkView] = {}
    blocks: list[str] = []
    for i, c in enumerate(chunks, start=1):
        label = f"C{i}"
        labels[label] = c
        blocks.append(_block(label, c))
    return Window(chunks, labels, "\n\n".join(blocks))


def make_windows(chunks: list[ChunkView], max_chars: int = 24000) -> list[Window]:
    windows: list[Window] = []
    cur: list[ChunkView] = []
    size = 0
    for c in chunks:
        if cur and size + len(c.text) > max_chars:
            windows.append(build_window(cur))
            cur, size = [], 0
        cur.append(c)
        size += len(c.text)
    if cur:
        windows.append(build_window(cur))
    return windows


def key_window(chunks: list[ChunkView], budget: int = 26000) -> Window:
    """Head of the document (parties, dates) plus every key-clause passage, in document order, within budget."""
    chosen: dict[int, ChunkView] = {}
    used = 0
    for c in chunks[:4]:
        chosen[c.index] = c
        used += len(c.text)
    for c in chunks:
        if c.index in chosen or c.clause_type not in KEY_CLAUSE_TYPES:
            continue
        if used + len(c.text) > budget:
            continue
        chosen[c.index] = c
        used += len(c.text)
    for c in chunks:  # spend any remaining budget on the rest of the document, in order
        if c.index not in chosen and used + len(c.text) <= budget:
            chosen[c.index] = c
            used += len(c.text)
    ordered = [chosen[k] for k in sorted(chosen)]
    return build_window(ordered)
