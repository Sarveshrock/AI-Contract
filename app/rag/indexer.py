"""Embeds persisted chunks and writes them to the vector store (idempotent and resumable)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import Document, DocumentChunk
from app.models.enums import IndexingStatus
from app.rag.embeddings import Embedder
from app.rag.vector_store import VectorRecord, VectorStore
from app.repositories import Repositories

log = get_logger(__name__)
BATCH = 64


def embedding_text(chunk: DocumentChunk) -> str:
    head = " ".join(x for x in (chunk.section_reference, chunk.section_title) if x)
    return f"{head}\n{chunk.text}" if head else chunk.text


def chunk_metadata(org_id: str, document: Document, chunk: DocumentChunk) -> dict:
    return {
        "organization_id": str(org_id),
        "contract_id": str(chunk.contract_id),
        "contract_version_id": str(chunk.contract_version_id),
        "document_id": str(document.id),
        "section_reference": chunk.section_reference or "",
        "section_title": chunk.section_title or "",
        "page_number": int(chunk.page_number),
        "chunk_id": str(chunk.id),
        "chunk_index": int(chunk.chunk_index),
        "document_hash": document.file_hash,
        "clause_type": chunk.clause_type.value,
        "source_text_reference": f"{document.id}#{chunk.char_start}-{chunk.char_end}",
    }


class DocumentIndexer:
    def __init__(self, repos: Repositories, store: VectorStore, embedder: Embedder) -> None:
        self._repos = repos
        self._store = store
        self._embedder = embedder
        self._org = repos.org_id

    def index_document(self, document: Document, *, force: bool = False,
                       on_progress: Callable[[float, str], None] | None = None) -> int:
        """Embed and upsert every (or every not-yet-embedded) chunk of ``document``. Returns chunks indexed."""
        self._store.ensure_collection(self._org, self._embedder.name, self._embedder.dim)
        filters = [F.eq("document_id", str(document.id))]
        all_chunks = self._repos.chunks.list(filters, order_by=[("chunk_index", False)])
        pending = all_chunks if force else [c for c in all_chunks if not c.embedded]
        done = 0
        for i in range(0, len(pending), BATCH):
            batch = pending[i : i + BATCH]
            vectors = self._embedder.embed([embedding_text(c) for c in batch])
            self._store.upsert(self._org, [
                VectorRecord(id=str(c.id), text=c.text, embedding=v, metadata=chunk_metadata(self._org, document, c))
                for c, v in zip(batch, vectors)
            ])
            self._repos.chunks.update_where([F.in_("id", [str(c.id) for c in batch])], embedded=True)
            done += len(batch)
            progress = (len(all_chunks) - len(pending) + done) / max(1, len(all_chunks))
            self._repos.documents.update(document.id, indexing_progress=round(progress, 4), heartbeat_at=datetime.now(timezone.utc))
            if on_progress:
                on_progress(progress, f"Embedded {done}/{len(pending)} chunks")
        self._repos.documents.update(
            document.id, indexing_status=IndexingStatus.INDEXED, indexing_progress=1.0, indexing_error=None,
            embedding_model=self._embedder.name, embedding_dim=self._embedder.dim, chunk_count=len(all_chunks),
            indexed_at=datetime.now(timezone.utc), heartbeat_at=datetime.now(timezone.utc),
        )
        return done

    def remove_document(self, document_id: str) -> None:
        self._store.ensure_collection(self._org, self._embedder.name, self._embedder.dim)
        self._store.delete(self._org, {"organization_id": self._org, "document_id": str(document_id)})
