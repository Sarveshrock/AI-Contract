"""Vector store abstraction with ChromaDB and in-memory implementations.

Isolation model: one collection per organisation, an ``organization_id`` metadata field on every
record, and a filter on every query. Chroma is a *derived* index: Postgres ``document_chunks`` is
authoritative and the index can be rebuilt from it.

Filters use a neutral form: ``{"contract_id": "abc"}`` (equals) or ``{"contract_id": ["a", "b"]}`` (IN).
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import IndexMismatchError, RetrievalError
from app.core.logging import get_logger

log = get_logger(__name__)
Where = dict[str, str | int | list[str]]


@dataclass
class VectorRecord:
    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any]


@dataclass
class VectorHit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float = 0.0  # cosine similarity in [-1, 1] (higher is closer); 0 for plain fetches


class VectorStore(Protocol):
    def ensure_collection(self, org_id: str, embedder_name: str, dim: int) -> None: ...

    def upsert(self, org_id: str, records: list[VectorRecord]) -> None: ...

    def query(self, org_id: str, embedding: list[float], *, k: int, where: Where | None = None) -> list[VectorHit]: ...

    def fetch(self, org_id: str, where: Where | None = None, *, limit: int | None = None) -> list[VectorHit]: ...

    def delete(self, org_id: str, where: Where) -> None: ...

    def count(self, org_id: str, where: Where | None = None) -> int: ...

    def reset(self, org_id: str) -> None: ...


def clean_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma accepts only str/int/float/bool metadata values."""
    return {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items() if v is not None}


def collection_name(org_id: str) -> str:
    return "cl_" + str(org_id).replace("-", "")


def _matches(meta: dict[str, Any], where: Where | None) -> bool:
    if not where:
        return True
    for key, cond in where.items():
        val = meta.get(key)
        if isinstance(cond, list):
            if val not in cond:
                return False
        elif val != cond:
            return False
    return True


# ---------------------------------------------------------------------------------------------
class InMemoryVectorStore:
    """Exact cosine search over numpy-free python lists. Used by tests and CHROMA_MODE=memory."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, VectorRecord]] = {}
        self._meta: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()

    def ensure_collection(self, org_id: str, embedder_name: str, dim: int) -> None:
        with self._lock:
            existing = self._meta.get(org_id)
            if existing and existing != (embedder_name, dim):
                raise IndexMismatchError(
                    f"index built with {existing}, now {(embedder_name, dim)}",
                    user_message="The search index was built with a different embedding model. Re-index the documents to continue.",
                )
            self._meta[org_id] = (embedder_name, dim)
            self._collections.setdefault(org_id, {})

    def upsert(self, org_id: str, records: list[VectorRecord]) -> None:
        with self._lock:
            col = self._collections.setdefault(org_id, {})
            for r in records:
                if str(r.metadata.get("organization_id")) != str(org_id):
                    raise RetrievalError("record organisation mismatch", user_message="Refused to index a chunk into another organisation.")
                col[r.id] = VectorRecord(r.id, r.text, list(r.embedding), clean_metadata(r.metadata))

    @staticmethod
    def _cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    def query(self, org_id: str, embedding: list[float], *, k: int, where: Where | None = None) -> list[VectorHit]:
        with self._lock:
            recs = [r for r in self._collections.get(org_id, {}).values() if _matches(r.metadata, where)]
            scored = sorted(((self._cos(embedding, r.embedding), r) for r in recs), key=lambda t: t[0], reverse=True)[:k]
            return [VectorHit(r.id, r.text, dict(r.metadata), s) for s, r in scored]

    def fetch(self, org_id: str, where: Where | None = None, *, limit: int | None = None) -> list[VectorHit]:
        with self._lock:
            recs = [r for r in self._collections.get(org_id, {}).values() if _matches(r.metadata, where)]
            recs.sort(key=lambda r: (str(r.metadata.get("document_id")), int(r.metadata.get("chunk_index", 0))))
            return [VectorHit(r.id, r.text, dict(r.metadata)) for r in (recs[:limit] if limit else recs)]

    def delete(self, org_id: str, where: Where) -> None:
        with self._lock:
            col = self._collections.get(org_id, {})
            for rid in [rid for rid, r in col.items() if _matches(r.metadata, where)]:
                del col[rid]

    def count(self, org_id: str, where: Where | None = None) -> int:
        with self._lock:
            return sum(1 for r in self._collections.get(org_id, {}).values() if _matches(r.metadata, where))

    def reset(self, org_id: str) -> None:
        with self._lock:
            self._collections.pop(org_id, None)
            self._meta.pop(org_id, None)


# ---------------------------------------------------------------------------------------------
def to_chroma_where(where: Where | None) -> dict[str, Any] | None:
    if not where:
        return None
    conds: list[dict[str, Any]] = []
    for k, v in where.items():
        conds.append({k: {"$in": list(v)}} if isinstance(v, list) else {k: {"$eq": v}})
    return conds[0] if len(conds) == 1 else {"$and": conds}


class ChromaVectorStore:
    """ChromaDB-backed store: persistent (embedded) or HTTP client. See docs/ARCHITECTURE.md."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._lock = threading.RLock()
        self._collections: dict[str, Any] = {}

    # -- factories
    @classmethod
    def persistent(cls, path: Path) -> "ChromaVectorStore":
        import chromadb
        from chromadb.config import Settings

        path.mkdir(parents=True, exist_ok=True)
        return cls(chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False)))

    @classmethod
    def http(cls, host: str, port: int, ssl: bool, token: str | None) -> "ChromaVectorStore":
        import chromadb
        from chromadb.config import Settings

        headers = {"Authorization": f"Bearer {token}"} if token else None
        return cls(chromadb.HttpClient(host=host, port=port, ssl=ssl, headers=headers, settings=Settings(anonymized_telemetry=False)))

    # -- internals
    def _collection(self, org_id: str) -> Any:
        col = self._collections.get(org_id)
        if col is None:
            raise RetrievalError("collection not initialised", user_message="The search index is not initialised for this workspace.")
        return col

    def ensure_collection(self, org_id: str, embedder_name: str, dim: int) -> None:
        with self._lock:
            col = self._client.get_or_create_collection(
                name=collection_name(org_id),
                metadata={"hnsw:space": "cosine", "embedder": embedder_name, "dim": dim, "organization_id": str(org_id)},
                embedding_function=None,
            )
            meta = col.metadata or {}
            if meta.get("embedder") not in (None, embedder_name) or int(meta.get("dim", dim)) != dim:
                raise IndexMismatchError(
                    f"collection built with {meta.get('embedder')}/{meta.get('dim')}, now {embedder_name}/{dim}",
                    user_message="The search index was built with a different embedding model. Re-index the documents to continue.",
                )
            self._collections[org_id] = col

    def upsert(self, org_id: str, records: list[VectorRecord]) -> None:
        if not records:
            return
        for r in records:
            if str(r.metadata.get("organization_id")) != str(org_id):
                raise RetrievalError("record organisation mismatch", user_message="Refused to index a chunk into another organisation.")
        with self._lock:
            col = self._collection(org_id)
            for i in range(0, len(records), 128):
                batch = records[i : i + 128]
                col.upsert(
                    ids=[r.id for r in batch],
                    embeddings=[[float(x) for x in r.embedding] for r in batch],
                    documents=[r.text for r in batch],
                    metadatas=[clean_metadata(r.metadata) for r in batch],
                )

    def query(self, org_id: str, embedding: list[float], *, k: int, where: Where | None = None) -> list[VectorHit]:
        with self._lock:
            col = self._collection(org_id)
            total = col.count()
            if total == 0:
                return []
            res = col.query(query_embeddings=[[float(x) for x in embedding]], n_results=min(k, total), where=to_chroma_where(where),
                            include=["documents", "metadatas", "distances"])
        ids, docs, metas, dists = res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
        return [VectorHit(i, d or "", dict(m or {}), 1.0 - float(dist)) for i, d, m, dist in zip(ids, docs, metas, dists)]

    def fetch(self, org_id: str, where: Where | None = None, *, limit: int | None = None) -> list[VectorHit]:
        with self._lock:
            col = self._collection(org_id)
            res = col.get(where=to_chroma_where(where), limit=limit, include=["documents", "metadatas"])
        hits = [VectorHit(i, d or "", dict(m or {})) for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]
        hits.sort(key=lambda h: (str(h.metadata.get("document_id")), int(h.metadata.get("chunk_index", 0))))
        return hits

    def delete(self, org_id: str, where: Where) -> None:
        with self._lock:
            self._collection(org_id).delete(where=to_chroma_where(where))

    def count(self, org_id: str, where: Where | None = None) -> int:
        with self._lock:
            col = self._collection(org_id)
            if not where:
                return int(col.count())
            return len(col.get(where=to_chroma_where(where), include=[])["ids"])

    def reset(self, org_id: str) -> None:
        with self._lock:
            try:
                self._client.delete_collection(collection_name(org_id))
            except Exception:  # noqa: BLE001 - nothing to delete
                pass
            self._collections.pop(org_id, None)


@dataclass
class VectorStoreInfo:
    backend: str
    persistent: bool
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
