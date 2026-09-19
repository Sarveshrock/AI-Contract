"""Evidence-preserving context assembly: every passage keeps a short label that models must cite."""
from __future__ import annotations

from dataclasses import dataclass

from app.rag.retriever import RetrievedChunk
from app.security.prompt_guard import wrap_untrusted


@dataclass
class Citation:
    label: str  # C1, C2...
    chunk_id: str
    contract_id: str
    document_id: str
    contract_title: str
    page_number: int | None
    section_reference: str | None
    section_title: str | None
    text: str

    @property
    def location(self) -> str:
        parts = [self.contract_title]
        if self.section_reference:
            parts.append(f"§{self.section_reference}")
        if self.page_number:
            parts.append(f"p.{self.page_number}")
        return " · ".join(parts)


def build_context(chunks: list[RetrievedChunk], contract_titles: dict[str, str] | None = None, *, max_chars: int = 14000) -> tuple[str, list[Citation]]:
    titles = contract_titles or {}
    blocks: list[str] = []
    citations: list[Citation] = []
    used = 0
    for c in chunks:
        if used + len(c.text) > max_chars and citations:
            break
        label = f"C{len(citations) + 1}"
        cit = Citation(
            label=label, chunk_id=c.chunk_id, contract_id=c.contract_id, document_id=c.document_id,
            contract_title=titles.get(c.contract_id, "Contract"), page_number=c.page_number,
            section_reference=c.section_reference, section_title=c.metadata.get("section_title"), text=c.text,
        )
        citations.append(cit)
        header = f"[{label}] contract=\"{cit.contract_title}\" section=\"{cit.section_reference or '-'}\" page={cit.page_number or '-'}"
        blocks.append(f"{header}\n{wrap_untrusted(c.text, 'PASSAGE')}")
        used += len(c.text)
    return "\n\n".join(blocks), citations
