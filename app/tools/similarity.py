"""Near-duplicate / related-document detection (word shingles + Jaccard)."""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str, n: int = 5, limit: int = 4000) -> set[str]:
    words = _WORD.findall(text.lower())[:limit]
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    """Fraction of the smaller set contained in the other (detects amendments quoting a base agreement)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def relation_score(text_a: str, text_b: str) -> tuple[float, float]:
    sa, sb = shingles(text_a), shingles(text_b)
    return jaccard(sa, sb), containment(sa, sb)
