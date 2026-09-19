"""Retrieval quality evaluation (recall@k, MRR, hit-rate) against a golden set."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.retriever import HybridRetriever, RetrievalScope
from app.tools.normalize import normalize_for_match


@dataclass
class EvalCase:
    query: str
    #: substrings (normalised) that a relevant chunk must contain; a chunk is relevant if it contains ALL of them
    must_contain: list[str]
    contract_ids: list[str] | None = None


@dataclass
class EvalResult:
    k: int
    cases: int
    hit_rate: float
    mrr: float
    per_case: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return f"cases={self.cases} hit@{self.k}={self.hit_rate:.2f} mrr={self.mrr:.2f}"


def evaluate_retrieval(retriever: HybridRetriever, cases: list[EvalCase], *, k: int = 5) -> EvalResult:
    hits = 0
    rr_total = 0.0
    per_case: list[dict] = []
    for case in cases:
        scope = RetrievalScope(contract_ids=case.contract_ids) if case.contract_ids is not None else None
        result = retriever.retrieve(case.query, scope, k=k)
        needles = [normalize_for_match(n) for n in case.must_contain]
        rank = next((i + 1 for i, c in enumerate(result.chunks) if all(n in normalize_for_match(c.text) for n in needles)), None)
        if rank:
            hits += 1
            rr_total += 1.0 / rank
        per_case.append({"query": case.query, "rank": rank})
    n = max(1, len(cases))
    return EvalResult(k=k, cases=len(cases), hit_rate=hits / n, mrr=rr_total / n, per_case=per_case)
