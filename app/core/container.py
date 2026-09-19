"""Dependency wiring: infrastructure (AppContainer) and per-session services (WorkspaceContext)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from app.agents.llm import LLMClient, UnavailableLLM, build_llm, build_openai_client
from app.config.risk import load_risk_config
from app.config.settings import Settings, get_settings
from app.core.errors import IndexMismatchError
from app.core.logging import configure_logging, get_logger
from app.database.auditing import AuditingStore
from app.database.sqlite_store import SqliteTableStore
from app.database.store import F, TableStore
from app.database.supabase_store import SupabaseTableStore, create_supabase_client
from app.rag.embeddings import Embedder, HashingEmbedder, build_embedder
from app.rag.factory import build_vector_store
from app.rag.indexer import DocumentIndexer
from app.rag.retriever import HybridRetriever
from app.rag.vector_store import InMemoryVectorStore, VectorStore, VectorStoreInfo
from app.repositories import Repositories
from app.security.access import Principal
from app.security.auth import AuthService, LocalAuthService, SupabaseAuthService
from app.services.admin import AdminService
from app.services.alerts import AlertService
from app.services.analysis import AnalysisService
from app.services.analytics import AnalyticsService
from app.services.audit import AuditService, AuditSink, LocalAuditSink, SupabaseAuditSink
from app.services.contracts import ContractService
from app.services.copilot import CopilotService
from app.services.deadlines import DeadlineService
from app.services.ingestion import IngestionService
from app.services.integrations import IntegrationService
from app.services.obligations import ObligationService
from app.services.renewals import RenewalService
from app.services.reviews import ReviewService
from app.services.risk import RiskService
from app.services.storage import FileStorage, LocalFileStorage, SupabaseFileStorage
from app.tools.ocr import OcrEngine, TesseractOcr

log = get_logger(__name__)


@dataclass
class SystemInfo:
    mode: str
    is_demo: bool
    ai_configured: bool
    model: str
    embedder: str
    embedder_note: str
    vector_backend: str
    vector_note: str
    ocr_available: bool
    index_error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class AppContainer:
    """Process-wide infrastructure. Holds no user identity."""

    def __init__(self, settings: Settings, store: TableStore, storage: FileStorage, auth: AuthService, audit_sink: AuditSink, vector_store: VectorStore,
                 vector_info: VectorStoreInfo, ocr: OcrEngine, *, client: Any | None = None, is_local: bool = True) -> None:
        self.settings, self.store, self.storage, self.auth, self.audit_sink = settings, store, storage, auth, audit_sink
        self.vector_store, self.vector_info, self.ocr, self.client, self.is_local = vector_store, vector_info, ocr, client, is_local

    # ------------------------------------------------------------------ factories
    @classmethod
    def build(cls, settings: Settings | None = None) -> "AppContainer":
        settings = settings or get_settings()
        settings.validate_security()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        configure_logging(settings.log_level, settings.log_dir)
        vector_store, info = build_vector_store(settings)
        ocr = TesseractOcr(settings.tesseract_cmd, settings.ocr_languages)
        if settings.mode == "supabase":
            client = create_supabase_client(settings.supabase_url or "", settings.supabase_anon_key.get_secret_value() if settings.supabase_anon_key else "")
            store = SupabaseTableStore(client)
            return cls(settings, store, SupabaseFileStorage(client, settings.supabase_storage_bucket), SupabaseAuthService(client, store), SupabaseAuditSink(client),
                       vector_store, info, ocr, client=client, is_local=False)
        store = SqliteTableStore(settings.local_db_path)
        return cls(settings, store, LocalFileStorage(settings.local_storage_dir), LocalAuthService(store), LocalAuditSink(store), vector_store, info, ocr)

    @classmethod
    def for_testing(cls, settings: Settings, store: TableStore, storage_dir: Path, *, vector_store: VectorStore | None = None) -> "AppContainer":
        vs = vector_store or InMemoryVectorStore()
        return cls(settings, store, LocalFileStorage(storage_dir), LocalAuthService(store), LocalAuditSink(store), vs, VectorStoreInfo("memory", False, "test"), TesseractOcr())

    # ------------------------------------------------------------------ auth
    def sign_in(self, email: str = "", password: str = "") -> list[Principal]:
        return self.auth.sign_in(email, password)

    def sign_out(self) -> None:
        self.auth.sign_out()

    # ------------------------------------------------------------------ workspace
    def open_workspace(self, principal: Principal, *, llm: LLMClient | None = None, embedder: Embedder | None = None) -> "WorkspaceContext":
        return WorkspaceContext(self, principal, llm=llm, embedder=embedder)


class WorkspaceContext:
    """Services bound to one signed-in user within one organisation."""

    def __init__(self, container: AppContainer, principal: Principal, *, llm: LLMClient | None = None, embedder: Embedder | None = None) -> None:
        self.container, self.principal, self.settings = container, principal, container.settings
        store: TableStore = container.store
        if container.is_local:
            store = AuditingStore(store, lambda: (str(principal.user_id), principal.email))
        self.repos = Repositories(store, principal.org_id)
        self.audit = AuditService(container.audit_sink, principal)
        token = principal.access_token
        openai_client = build_openai_client(self.settings, token)
        self.llm: LLMClient = llm or build_llm(self.settings, token)
        self.embedder: Embedder = embedder or build_embedder(self.settings.embedding_provider, openai_client, self.settings.openai_embedding_model)
        self.index_error: str | None = None
        self.retriever: HybridRetriever | None
        try:
            self.retriever = HybridRetriever(container.vector_store, self.embedder, str(principal.org_id))
        except IndexMismatchError as exc:
            self.retriever, self.index_error = None, exc.user_message
        self.indexer = DocumentIndexer(self.repos, container.vector_store, self.embedder)

        org = self.repos.organizations.get(principal.org_id)
        self.risk = RiskService(principal, self.repos, self.audit)
        self.alerts = AlertService(principal, self.repos, self.audit)
        self.deadlines = DeadlineService(principal, self.repos, self.audit)
        self.obligations = ObligationService(principal, self.repos, self.audit)
        self.renewals = RenewalService(self.repos)
        self.reviews = ReviewService(principal, self.repos, self.audit, self.deadlines, self.risk)
        self.analytics = AnalyticsService(self.repos, load_risk_config(org.settings if org else {}))
        self.ingestion = IngestionService(principal, self.repos, container.storage, self.indexer, container.ocr, self.settings, self.audit)
        self.analysis = AnalysisService(principal, self.repos, self.settings, self.llm, self.retriever, self.audit, self.risk, self.alerts)
        self.contracts = ContractService(principal, self.repos, self.ingestion, self.analysis, self.audit, lambda: self.llm.available)
        self.integrations = IntegrationService(principal, self.repos, self.audit, self.deadlines)
        self.admin = AdminService(principal, self.repos, self.audit, supabase_client=container.client, storage=container.storage, indexer=self.indexer)
        self.copilot: CopilotService | None = None
        if self.retriever is not None:
            self.copilot = CopilotService(principal, self.repos, self.settings, self.llm, self.retriever, self.audit, self.obligations, self.deadlines,
                                          compare=self._compare_latest)

    # ------------------------------------------------------------------
    def _compare_latest(self, contract_id: UUID) -> None:
        versions = self.repos.versions.list([F.eq("contract_id", str(contract_id))], order_by=[("version_number", False)])
        if len(versions) >= 2:
            self.analysis.compare_versions(contract_id, versions[-2].id, versions[-1].id)

    def reindex_all(self, on_progress=None) -> int:
        """Rebuild the vector index for every document (after changing the embedding model)."""
        vs = self.container.vector_store
        vs.reset(str(self.principal.org_id))
        vs.ensure_collection(str(self.principal.org_id), self.embedder.name, self.embedder.dim)
        self.repos.chunks.update_where([F.eq("embedded", True)], embedded=False)
        docs = self.repos.documents.list()
        for i, d in enumerate(docs):
            self.indexer.index_document(d, force=True)
            if on_progress:
                on_progress((i + 1) / max(1, len(docs)), d.filename)
        self.retriever = HybridRetriever(vs, self.embedder, str(self.principal.org_id))
        self.index_error = None
        self.analysis._retriever = self.retriever  # noqa: SLF001
        self.copilot = CopilotService(self.principal, self.repos, self.settings, self.llm, self.retriever, self.audit, self.obligations, self.deadlines,
                                      compare=self._compare_latest)
        self.audit.record("index.rebuild", "organization", self.principal.org_id, documents=len(docs), embedder=self.embedder.name)
        return len(docs)

    def system_info(self) -> SystemInfo:
        c = self.container
        hashing = isinstance(self.embedder, HashingEmbedder)
        return SystemInfo(
            mode=self.settings.mode, is_demo=self.settings.is_demo, ai_configured=self.llm.available, model=self.llm.model if self.llm.available else "not configured",
            embedder=self.embedder.name, embedder_note="Offline lexical embeddings (lower search quality). Configure OpenAI for semantic search." if hashing else "OpenAI embeddings",
            vector_backend=c.vector_info.backend, vector_note=c.vector_info.note, ocr_available=c.ocr.is_available(), index_error=self.index_error,
        )


_ = (UnavailableLLM,)
