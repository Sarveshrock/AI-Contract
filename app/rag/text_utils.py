"""Shared tokenisation for lexical retrieval and the offline hashing embedder."""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have he her his how i if in into is it its may me more most my no nor not of on or our out shall she should so than that the their them then there these they this those to too under until up us was we were what when where which while who whom why will with would you your any all each per upon".split()
)


def stem(token: str) -> str:
    for suffix in ("ations", "ation", "ingly", "ings", "ing", "edly", "ies", "ied", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return token


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    if not keep_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens]


def query_terms(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in tokenize(query):
        seen.setdefault(t, None)
    return list(seen)
