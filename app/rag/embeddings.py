"""Embedding providers: OpenAI (production) and a deterministic offline hashing embedder."""
from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

from app.core.errors import QUOTA_MESSAGE, AIUnavailableError, TransientError, is_quota_exhausted
from app.core.logging import get_logger
from app.core.retry import retry_call
from app.rag.text_utils import tokenize

log = get_logger(__name__)
OPENAI_DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}


class Embedder(Protocol):
    name: str
    dim: int
    #: Cosine similarity above which a hit is considered semantically relevant on its own.
    relevance_floor: float

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature-hashing of unigrams+bigrams. Lexical-quality only, but deterministic and offline.

    Used for tests, demo mode and when no OpenAI credentials exist. The UI labels it as such.
    """

    relevance_floor = 0.18

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> list[float]:
        tokens = tokenize(text)
        feats: dict[int, float] = {}
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
            idx, sign = h % self.dim, 1.0 if (h >> 63) & 1 else -1.0
            feats[idx] = feats.get(idx, 0.0) + sign * (1.0 if "_" not in g else 0.7)
        vec = [0.0] * self.dim
        for i, v in feats.items():
            vec[i] = math.copysign(math.log1p(abs(v)), v)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    relevance_floor = 0.30
    BATCH = 96

    def __init__(self, client: Any, model: str = "text-embedding-3-small", *, timeout: float = 60.0) -> None:
        self._client = client
        self.model = model
        self.name = f"openai:{model}"
        self.dim = OPENAI_DIMS.get(model, 1536)
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = [t.replace("\n", " ")[:8000] or " " for t in texts[i : i + self.BATCH]]
            out.extend(retry_call(lambda b=batch: self._call(b), label="openai embeddings"))
        if out and len(out[0]) != self.dim:
            self.dim = len(out[0])
        return out

    def _call(self, batch: list[str]) -> list[list[float]]:
        try:
            res = self._client.embeddings.create(model=self.model, input=batch, timeout=self._timeout)
            return [list(d.embedding) for d in res.data]
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if is_quota_exhausted(exc):
                raise AIUnavailableError("openai quota exhausted", user_message=QUOTA_MESSAGE) from exc
            if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"):
                raise TransientError(f"embeddings: {name}", user_message="The embedding service is temporarily unavailable.") from exc
            if name == "AuthenticationError":
                raise AIUnavailableError("openai auth", user_message="OpenAI rejected the API key. Check OPENAI_API_KEY.") from exc
            raise


def build_embedder(provider: str, openai_client: Any | None, model: str) -> Embedder:
    if provider == "openai" or (provider == "auto" and openai_client is not None):
        if openai_client is None:
            raise AIUnavailableError("openai embeddings requested without credentials", user_message="OpenAI embeddings were requested but no API key or proxy is configured.")
        return OpenAIEmbedder(openai_client, model)
    log.warning("Using offline hashing embedder (lower retrieval quality). Configure OpenAI for semantic embeddings.")
    return HashingEmbedder()
