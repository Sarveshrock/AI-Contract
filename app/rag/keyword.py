"""BM25 keyword index (the lexical half of hybrid retrieval)."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from app.rag.text_utils import tokenize


@dataclass
class BM25Index:
    ids: list[str]
    doc_tokens: list[Counter[str]]
    doc_len: list[int]
    df: Counter[str]
    avgdl: float
    k1: float = 1.4
    b: float = 0.75

    @classmethod
    def build(cls, ids: list[str], texts: list[str]) -> "BM25Index":
        toks = [tokenize(t) for t in texts]
        df: Counter[str] = Counter()
        for t in toks:
            df.update(set(t))
        lens = [len(t) for t in toks]
        return cls(ids, [Counter(t) for t in toks], lens, df, (sum(lens) / len(lens)) if lens else 0.0)

    def score(self, query: str) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(tokenize(query)))
        n = len(self.ids)
        if not terms or not n:
            return []
        out: list[tuple[str, float]] = []
        for i, tf in enumerate(self.doc_tokens):
            s = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1)))
            if s > 0:
                out.append((self.ids[i], s))
        out.sort(key=lambda t: t[1], reverse=True)
        return out
