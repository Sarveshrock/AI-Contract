"""Chooses which AI provider serves chat/analysis and embeddings, and degrades gracefully when one has no credits."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.gemini import GeminiEmbedder, GeminiLLM
from app.agents.llm import LLMClient, UnavailableLLM, build_claude_llm
from app.config.settings import Settings
from app.core.errors import AIUnavailableError, QUOTA_MESSAGE, is_quota_exhausted
from app.core.logging import get_logger
from app.rag.embeddings import Embedder, HashingEmbedder

log = get_logger(__name__)


@dataclass
class ProviderChoice:
    llm: LLMClient
    embedder: Embedder
    notes: list[str] = field(default_factory=list)  # human-readable explanation of any fallback
    degraded: bool = False                            # True when nothing can run AI analysis
    switched: bool = False                            # True when a provider other than the configured default is in use


def _openai_exhausted(client: Any, model: str) -> bool:
    try:
        client.embeddings.create(model=model, input=["ok"], timeout=15)
        return False
    except Exception as exc:  # noqa: BLE001 - only an explicit empty balance counts, never network trouble
        return is_quota_exhausted(exc)


def _claude_exhausted(claude: Any) -> bool:
    try:
        claude._client.messages.create(model=claude.model, max_tokens=1, messages=[{"role": "user", "content": "ok"}])  # noqa: SLF001
        return False
    except Exception as exc:  # noqa: BLE001
        return is_quota_exhausted(exc)


def _gemini_unusable(component: Any) -> bool:
    try:
        component.probe()
        return False
    except AIUnavailableError:
        return True
    except Exception:  # noqa: BLE001 - rate limits and network errors are temporary
        return False


def select_providers(settings: Settings, openai_client: Any | None, default_llm: LLMClient, default_embedder: Embedder) -> ProviderChoice:
    """Keep OpenAI when it works. Otherwise use Claude and/or Gemini for chat and Gemini or offline hashing for embeddings."""
    openai_ok = openai_client is not None and not _openai_exhausted(openai_client, settings.openai_embedding_model)
    gemini_wanted = settings.gemini_configured and settings.embedding_provider != "hashing"
    if openai_ok and settings.embedding_provider != "gemini":
        return ProviderChoice(default_llm, default_embedder)

    notes: list[str] = []
    if openai_client is not None and not openai_ok:
        notes.append("OpenAI has no credits left.")

    gemini_llm = GeminiLLM(settings.gemini_api_key.get_secret_value(), settings.gemini_model, timeout=settings.openai_timeout_s) if settings.gemini_configured else None
    if gemini_llm is not None and _gemini_unusable(gemini_llm):
        notes.append("The Gemini key cannot be used (rejected or no quota).")
        gemini_llm = None
    claude = build_claude_llm(settings)
    if claude is not None and _claude_exhausted(claude):
        notes.append("The Anthropic (Claude) account has no credits left.")
        claude = None

    # chat / analysis: the configured OpenAI model when it works, otherwise Claude, otherwise Gemini
    if openai_ok:
        llm: LLMClient = default_llm
    elif claude is not None:
        llm = claude
    elif gemini_llm is not None:
        llm = gemini_llm
    else:
        llm = UnavailableLLM(reason=" ".join([QUOTA_MESSAGE if openai_client is not None and not openai_ok else "AI is not configured.", *notes[1:]]).strip())

    # embeddings: Gemini keeps search semantic; otherwise offline lexical hashing
    embedder: Embedder
    if openai_ok:
        embedder = default_embedder
    elif gemini_wanted and gemini_llm is not None:
        emb = GeminiEmbedder(settings.gemini_api_key.get_secret_value(), settings.gemini_embedding_model, dim=settings.gemini_embedding_dim, timeout=settings.openai_timeout_s)
        embedder = emb if not _gemini_unusable(emb) else HashingEmbedder()
    else:
        embedder = HashingEmbedder()

    degraded = not llm.available
    if llm.available and llm is not default_llm:
        notes.append(f"Using {getattr(llm, 'model', 'a fallback model')} for chat and analysis.")
    if isinstance(embedder, HashingEmbedder) and openai_client is not None and not openai_ok:
        notes.append("Search uses offline lexical matching.")
    elif embedder is not default_embedder and not isinstance(embedder, HashingEmbedder):
        notes.append("Search uses Gemini embeddings.")
    switched = llm is not default_llm or embedder is not default_embedder
    if switched or degraded:
        log.warning("AI provider fallback: %s", " ".join(notes))
    return ProviderChoice(llm, embedder, notes, degraded, switched)
