"""Hybrid (dense + BM25) retrieval with organisation isolation, RRF fusion and heuristic reranking."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.models.enums import ClauseType
from app.rag.embeddings import Embedder
from app.rag.keyword import BM25Index
from app.rag.text_utils import query_terms, tokenize
from app.rag.vector_store import VectorHit, VectorStore, Where
from app.tools.clause_classifier import classify_clause

log = get_logger(__name__)
CORPUS_CAP = 60_000


@dataclass
class RetrievalScope:
    """Contract-level, version-level and clause-type filters applied *in addition to* the org filter."""

    contract_ids: list[str] | None = None
    version_ids: list[str] | None = None
    document_ids: list[str] | None = None
    clause_types: list[str] | None = None

    def to_where(self, org_id: str) -> Where:
        where: Where = {"organization_id": str(org_id)}
        if self.contract_ids is not None:
            where["contract_id"] = [str(x) for x in self.contract_ids]
        if self.version_ids is not None:
            where["contract_version_id"] = [str(x) for x in self.version_ids]
        if self.document_ids is not None:
            where["document_id"] = [str(x) for x in self.document_ids]
        if self.clause_types is not None:
            where["clause_type"] = [str(x) for x in self.clause_types]
        return where

    @property
    def is_empty_selection(self) -> bool:
        return any(v is not None and len(v) == 0 for v in (self.contract_ids, self.version_ids, self.document_ids, self.clause_types))


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    dense_score: float = 0.0
    keyword_score: float = 0.0
    fused_score: float = 0.0
    coverage: float = 0.0
    rerank_score: float = 0.0
    relevant: bool = False

    @property
    def contract_id(self) -> str:
        return str(self.metadata.get("contract_id", ""))

    @property
    def document_id(self) -> str:
        return str(self.metadata.get("document_id", ""))

    @property
    def page_number(self) -> int | None:
        v = self.metadata.get("page_number")
        return int(v) if v is not None else None

    @property
    def section_reference(self) -> str | None:
        return self.metadata.get("section_reference")


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def relevant_chunks(self) -> list[RetrievedChunk]:
        return [c for c in self.chunks if c.relevant]


_CLAUSE_HINTS: dict[ClauseType, tuple[str, ...]] = {
    ClauseType.PAYMENT: ("pay", "payment", "invoice", "fee", "price"),
    ClauseType.RENEWAL: ("renew", "renewal", "auto-renew"),
    ClauseType.TERMINATION: ("terminat", "cancel"),
    ClauseType.SLA: ("sla", "service level", "uptime", "credit"),
    ClauseType.CONFIDENTIALITY: ("confidential", "nda"),
    ClauseType.LIABILITY: ("liab", "cap"),
    ClauseType.INDEMNITY: ("indemn",),
    ClauseType.GOVERNING_LAW: ("governing law", "jurisdiction"),
}


class HybridRetriever:
    def __init__(self, store: VectorStore, embedder: Embedder, org_id: str) -> None:
        self._store = store
        self._embedder = embedder
        self._org = str(org_id)
        self._bm25: dict[str, tuple[int, BM25Index, dict[str, VectorHit]]] = {}
        self._lock = threading.Lock()
        self._store.ensure_collection(self._org, embedder.name, embedder.dim)

    @property
    def org_id(self) -> str:
        return self._org

    def invalidate(self) -> None:
        with self._lock:
            self._bm25.clear()

    def _keyword_index(self, where: Where) -> tuple[BM25Index, dict[str, VectorHit]]:
        key = json.dumps(where, sort_keys=True)
        total = self._store.count(self._org, where)
        with self._lock:
            cached = self._bm25.get(key)
            if cached and cached[0] == total:
                return cached[1], cached[2]
        hits = self._store.fetch(self._org, where, limit=CORPUS_CAP)
        index = BM25Index.build([h.id for h in hits], [h.text for h in hits])
        by_id = {h.id: h for h in hits}
        with self._lock:
            self._bm25[key] = (total, index, by_id)
        return index, by_id

    def retrieve(self, query: str, scope: RetrievalScope | None = None, *, k: int = 8) -> RetrievalResult:
        scope = scope or RetrievalScope()
        if scope.is_empty_selection or not query.strip():
            return RetrievalResult(query, [], {"reason": "empty scope or query"})
        where = scope.to_where(self._org)
        pool = max(k * 3, 20)
        q_vec = self._embedder.embed([query])[0]
        dense = self._store.query(self._org, q_vec, k=pool, where=where)
        index, corpus = self._keyword_index(where)
        kw = index.score(query)[:pool]
        kw_max = kw[0][1] if kw else 1.0

        candidates: dict[str, RetrievedChunk] = {}
        rrf: dict[str, float] = {}
        for rank, h in enumerate(dense):
            c = candidates.setdefault(h.id, RetrievedChunk(h.id, h.text, h.metadata))
            c.dense_score = h.score
            rrf[h.id] = rrf.get(h.id, 0.0) + 1.0 / (60 + rank + 1)
        for rank, (cid, s) in enumerate(kw):
            h = corpus[cid]
            c = candidates.setdefault(cid, RetrievedChunk(cid, h.text, h.metadata))
            c.keyword_score = s / kw_max if kw_max else 0.0
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (60 + rank + 1)

        # defence in depth: never surface a hit from another organisation
        for cid in [cid for cid, c in candidates.items() if str(c.metadata.get("organization_id")) != self._org]:
            log.error("cross-organisation hit dropped: chunk %s", cid)
            del candidates[cid]

        terms = query_terms(query)
        hinted = {ct.value for ct, words in _CLAUSE_HINTS.items() if any(w in query.lower() for w in words)}
        max_rrf = max(rrf.values(), default=1.0)
        q_lower = " ".join(tokenize(query))
        for cid, c in candidates.items():
            toks = set(tokenize(c.text))
            c.coverage = (sum(1 for t in terms if t in toks) / len(terms)) if terms else 0.0
            c.fused_score = rrf.get(cid, 0.0) / max_rrf
            phrase = 0.0
            qt = q_lower.split()
            ctext = " ".join(tokenize(c.text))
            if len(qt) >= 2 and any(" ".join(qt[i : i + 2]) in ctext for i in range(len(qt) - 1)):
                phrase = 1.0
            clause_bonus = 1.0 if str(c.metadata.get("clause_type")) in hinted else 0.0
            c.rerank_score = 0.40 * c.fused_score + 0.25 * max(0.0, c.dense_score) + 0.20 * c.coverage + 0.08 * phrase + 0.07 * clause_bonus
            floor = getattr(self._embedder, "relevance_floor", 0.25)
            c.relevant = (c.coverage >= 0.34 and (c.keyword_score > 0 or c.dense_score > 0)) or c.dense_score >= floor + 0.1
        ranked = sorted(candidates.values(), key=lambda c: c.rerank_score, reverse=True)[:k]
        return RetrievalResult(query, ranked, {"dense": len(dense), "keyword": len(kw), "corpus": len(corpus), "embedder": self._embedder.name})


def infer_clause_types(query: str) -> list[str]:
    ct = classify_clause(query)
    return [ct.value] if ct is not ClauseType.OTHER else []
