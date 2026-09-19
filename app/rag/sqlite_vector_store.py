"""Persistent, dependency-light vector store (SQLite + numpy brute-force cosine).

Used as the *fallback* when the embedded ChromaDB engine fails its start-up self-test (native crash
on some machines) and for single-user local mode. It implements the same ``VectorStore`` interface,
so switching to Chroma (embedded or HTTP) needs no code changes. Not intended for very large corpora
(exact search is O(n)); use ChromaDB over HTTP for shared, larger deployments.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

from app.core.errors import IndexMismatchError, RetrievalError
from app.rag.vector_store import VectorHit, VectorRecord, Where, _matches, clean_metadata

_SQL_COLUMNS = ("contract_id", "contract_version_id", "document_id", "clause_type")


class SqliteVectorStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL") if self._path != ":memory:" else None
            self._conn.execute("CREATE TABLE IF NOT EXISTS collections (org_id TEXT PRIMARY KEY, embedder TEXT NOT NULL, dim INTEGER NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vectors (org_id TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, embedding BLOB NOT NULL, "
                "metadata TEXT NOT NULL, contract_id TEXT, contract_version_id TEXT, document_id TEXT, clause_type TEXT, PRIMARY KEY (org_id, id))"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_doc ON vectors (org_id, document_id)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_contract ON vectors (org_id, contract_id)")

    def ensure_collection(self, org_id: str, embedder_name: str, dim: int) -> None:
        with self._lock:
            row = self._conn.execute("SELECT embedder, dim FROM collections WHERE org_id = ?", (org_id,)).fetchone()
            if row is None:
                self._conn.execute("INSERT INTO collections VALUES (?, ?, ?)", (org_id, embedder_name, dim))
            elif (row[0], int(row[1])) != (embedder_name, dim):
                raise IndexMismatchError(
                    f"index built with {row}, now {(embedder_name, dim)}",
                    user_message="The search index was built with a different embedding model. Re-index the documents to continue.",
                )

    def upsert(self, org_id: str, records: list[VectorRecord]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for r in records:
                    if str(r.metadata.get("organization_id")) != str(org_id):
                        raise RetrievalError("record organisation mismatch", user_message="Refused to index a chunk into another organisation.")
                    meta = clean_metadata(r.metadata)
                    self._conn.execute(
                        "INSERT OR REPLACE INTO vectors (org_id, id, text, embedding, metadata, contract_id, contract_version_id, document_id, clause_type) VALUES (?,?,?,?,?,?,?,?,?)",
                        (org_id, r.id, r.text, np.asarray(r.embedding, dtype=np.float32).tobytes(), json.dumps(meta),
                         *(str(meta.get(c)) if meta.get(c) is not None else None for c in _SQL_COLUMNS)),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _select(self, org_id: str, where: Where | None, columns: str) -> list[tuple]:
        clauses, params = ["org_id = ?"], [org_id]
        for key, cond in (where or {}).items():
            if key in _SQL_COLUMNS:
                if isinstance(cond, list):
                    if not cond:
                        return []
                    clauses.append(f"{key} IN ({','.join('?' * len(cond))})")
                    params.extend(cond)
                else:
                    clauses.append(f"{key} = ?")
                    params.append(cond)
        with self._lock:
            return self._conn.execute(f"SELECT {columns} FROM vectors WHERE {' AND '.join(clauses)}", params).fetchall()

    def query(self, org_id: str, embedding: list[float], *, k: int, where: Where | None = None) -> list[VectorHit]:
        rows = [r for r in self._select(org_id, where, "id, text, metadata, embedding") if _matches(json.loads(r[2]), where)]
        if not rows:
            return []
        mat = np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
        q = np.asarray(embedding, dtype=np.float32)
        sims = (mat @ q) / ((np.linalg.norm(mat, axis=1) * (np.linalg.norm(q) or 1.0)) + 1e-9)
        order = np.argsort(-sims)[:k]
        return [VectorHit(rows[i][0], rows[i][1], json.loads(rows[i][2]), float(sims[i])) for i in order]

    def fetch(self, org_id: str, where: Where | None = None, *, limit: int | None = None) -> list[VectorHit]:
        rows = [r for r in self._select(org_id, where, "id, text, metadata") if _matches(json.loads(r[2]), where)]
        hits = [VectorHit(r[0], r[1], json.loads(r[2])) for r in rows]
        hits.sort(key=lambda h: (str(h.metadata.get("document_id")), int(h.metadata.get("chunk_index", 0))))
        return hits[:limit] if limit else hits

    def delete(self, org_id: str, where: Where) -> None:
        ids = [r[0] for r in self._select(org_id, where, "id, metadata") if _matches(json.loads(r[1]), where)]
        with self._lock:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
                self._conn.execute(f"DELETE FROM vectors WHERE org_id = ? AND id IN ({','.join('?' * len(batch))})", [org_id, *batch])

    def count(self, org_id: str, where: Where | None = None) -> int:
        if where and not set(where) <= set(_SQL_COLUMNS) | {"organization_id"}:
            return len(self.fetch(org_id, where))
        return len(self._select(org_id, {k: v for k, v in (where or {}).items() if k != "organization_id"}, "id"))

    def reset(self, org_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM vectors WHERE org_id = ?", (org_id,))
            self._conn.execute("DELETE FROM collections WHERE org_id = ?", (org_id,))
