"""Shared builders for tests: a fully wired in-memory workspace stack."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config.settings import Settings
from app.rag.embeddings import HashingEmbedder
from app.rag.indexer import DocumentIndexer
from app.rag.retriever import HybridRetriever
from app.rag.vector_store import InMemoryVectorStore
from app.repositories import Repositories
from app.security.access import Principal
from app.services.audit import AuditService, LocalAuditSink
from app.services.ingestion import IngestionService
from app.services.storage import LocalFileStorage
from app.tools.ocr import OcrEngine, OcrResult, UnavailableOcr


class FakeOcr:
    """Deterministic OCR test double (returns fixed text)."""

    name = "fake"

    def __init__(self, text: str = "", confidence: float = 0.93, available: bool = True) -> None:
        self.text, self.confidence, self._available, self.calls = text, confidence, available, 0

    def is_available(self) -> bool:
        return self._available

    def ocr_image(self, png_bytes: bytes) -> OcrResult:
        self.calls += 1
        return OcrResult(self.text, self.confidence)


@dataclass
class Stack:
    principal: Principal
    repos: Repositories
    ingestion: IngestionService
    indexer: DocumentIndexer
    retriever: HybridRetriever
    vectors: InMemoryVectorStore
    embedder: HashingEmbedder
    audit: AuditService
    storage: LocalFileStorage


def build_stack(store, principal: Principal, tmp_path: Path, *, vectors: InMemoryVectorStore | None = None, ocr: OcrEngine | None = None) -> Stack:
    settings = Settings(_env_file=None, contractlens_mode="local", data_dir=tmp_path)
    repos = Repositories(store, principal.org_id)
    vectors = vectors or InMemoryVectorStore()
    embedder = HashingEmbedder()
    indexer = DocumentIndexer(repos, vectors, embedder)
    storage = LocalFileStorage(tmp_path / f"storage-{principal.org_id}")
    audit = AuditService(LocalAuditSink(store), principal)
    ingestion = IngestionService(principal, repos, storage, indexer, ocr or UnavailableOcr(), settings, audit)
    retriever = HybridRetriever(vectors, embedder, str(principal.org_id))
    return Stack(principal, repos, ingestion, indexer, retriever, vectors, embedder, audit, storage)


class Scripted:
    """LLM double: maps schema class -> instance or callable(user_prompt) -> instance. Records calls."""

    model = "scripted"
    available = True

    def __init__(self, responses=None, fail_first: dict | None = None):
        self.responses = responses or {}
        self.calls = []
        self.fail_first = dict(fail_first or {})

    def structured(self, *, system, user, schema, purpose, temperature=0.0, max_tokens=None):
        self.calls.append((schema.__name__, purpose, user))
        left = self.fail_first.get(schema.__name__, 0)
        if left:
            from app.core.errors import TransientError

            self.fail_first[schema.__name__] = left - 1
            raise TransientError("simulated", user_message="temporary failure")
        r = self.responses[schema]
        return r(user) if callable(r) else r


class FlakyFixture:
    """Wraps FixtureLLM; the first ``n`` calls for a schema raise TransientError."""

    model = "flaky-fixtures"
    available = True

    def __init__(self, fail_schema: str, n: int = 1):
        from app.demo.fixtures import FixtureLLM

        self.inner, self.fail_schema, self.left, self.calls = FixtureLLM(), fail_schema, n, 0

    def structured(self, **kw):
        self.calls += 1
        if kw["schema"].__name__ == self.fail_schema and self.left > 0:
            from app.core.errors import TransientError

            self.left -= 1
            raise TransientError("simulated outage", user_message="AI service temporarily unavailable")
        return self.inner.structured(**kw)


def make_workspace(store, principal, tmp_path, llm=None, vector_store=None):
    from app.config.settings import Settings
    from app.core.container import AppContainer
    from app.rag.embeddings import HashingEmbedder

    settings = Settings(_env_file=None, contractlens_mode="local", data_dir=tmp_path, openai_api_key=None, openai_base_url=None)
    container = AppContainer.for_testing(settings, store, tmp_path / "storage", vector_store=vector_store)
    return container.open_workspace(principal, llm=llm, embedder=HashingEmbedder())
