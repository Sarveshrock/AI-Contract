"""Gemini provider and the automatic provider fallback (no network: httpx and the SDK clients are faked)."""
from __future__ import annotations

import math
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app.agents import gemini as gm
from app.agents import providers
from app.agents.llm import UnavailableLLM
from app.config.settings import Settings
from app.core.errors import AIUnavailableError
from app.rag.embeddings import HashingEmbedder


class Out(BaseModel):
    answer: str


def _resp(status: int, payload: dict | str) -> httpx.Response:
    req = httpx.Request("POST", "https://example.test")
    return httpx.Response(status, json=payload, request=req) if isinstance(payload, dict) else httpx.Response(status, text=payload, request=req)


def test_gemini_structured_output_is_validated(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, key=headers["x-goog-api-key"], gen=json["generationConfig"])
        return _resp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '```json\n{"answer": "hello"}\n```'}]}}],
                           "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}})

    monkeypatch.setattr(gm.httpx, "post", fake_post)
    llm = gm.GeminiLLM("k", "gemini-test")
    assert llm.structured(system="s", user="u", schema=Out, purpose="t").answer == "hello"
    assert "gemini-test:generateContent" in seen["url"] and seen["key"] == "k" and "responseJsonSchema" in seen["gen"]
    assert llm.last_call.prompt_tokens == 5


def test_gemini_retries_without_schema_when_schema_is_rejected(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append("responseJsonSchema" in json["generationConfig"])
        if calls[-1]:
            return _resp(400, "Invalid JSON payload: unsupported schema keyword")
        return _resp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"answer": "ok"}'}]}}]})

    monkeypatch.setattr(gm.httpx, "post", fake_post)
    assert gm.GeminiLLM("k", "m").structured(system="s", user="u", schema=Out, purpose="t").answer == "ok"
    assert calls == [True, False]


def test_gemini_bad_key_is_a_clear_unavailable_error(monkeypatch):
    monkeypatch.setattr(gm.httpx, "post", lambda *a, **k: _resp(400, "API key not valid. Please pass a valid API key."))
    with pytest.raises(AIUnavailableError):
        gm.GeminiLLM("bad", "m").structured(system="s", user="u", schema=Out, purpose="t")


def test_gemini_embeddings_are_unit_length(monkeypatch):
    def fake_post(url, headers, json, timeout):
        n = len(json["requests"])
        assert all(r["outputDimensionality"] == 4 for r in json["requests"])
        return _resp(200, {"embeddings": [{"values": [3.0, 4.0, 0.0, 0.0]} for _ in range(n)]})

    monkeypatch.setattr(gm.httpx, "post", fake_post)
    vecs = gm.GeminiEmbedder("k", dim=4).embed(["a", "b", "c"])
    assert len(vecs) == 3 and all(math.isclose(sum(x * x for x in v), 1.0) for v in vecs)


class _FakeOpenAI:
    def __init__(self, exc: Exception | None) -> None:
        self.embeddings = SimpleNamespace(create=self._create)
        self._exc = exc

    def _create(self, **_):
        if self._exc:
            raise self._exc
        return SimpleNamespace(data=[])


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, contractlens_mode="local", openai_api_key=None, openai_base_url=None, **kw)


def test_openai_with_credits_is_kept_untouched():
    default_llm, default_emb = SimpleNamespace(available=True, model="gpt"), HashingEmbedder(16)
    choice = providers.select_providers(_settings(), _FakeOpenAI(None), default_llm, default_emb)
    assert choice.llm is default_llm and choice.embedder is default_emb and not choice.switched


def test_empty_openai_balance_falls_back_to_gemini_for_chat_and_embeddings(monkeypatch):
    monkeypatch.setattr(providers, "_gemini_unusable", lambda component: False)
    monkeypatch.setattr(providers, "_claude_exhausted", lambda c: True)
    s = _settings(gemini_api_key="g-key")
    choice = providers.select_providers(s, _FakeOpenAI(Exception("Error code: 429 - credit_balance_exhausted")), UnavailableLLM(), HashingEmbedder(16))
    assert isinstance(choice.llm, gm.GeminiLLM) and isinstance(choice.embedder, gm.GeminiEmbedder)
    assert choice.switched and not choice.degraded and "no credits" in " ".join(choice.notes)


def test_everything_unusable_degrades_to_offline_with_an_explanation(monkeypatch):
    monkeypatch.setattr(providers, "_gemini_unusable", lambda component: True)
    choice = providers.select_providers(_settings(gemini_api_key="g-key"), _FakeOpenAI(Exception("insufficient_quota")), UnavailableLLM(), HashingEmbedder(16))
    assert choice.degraded and not choice.llm.available and isinstance(choice.embedder, HashingEmbedder)
    assert "credits" in choice.llm.reason.lower() and "Gemini" in choice.llm.reason


def test_network_trouble_does_not_switch_provider():
    default_llm, default_emb = SimpleNamespace(available=True, model="gpt"), HashingEmbedder(16)
    choice = providers.select_providers(_settings(), _FakeOpenAI(ConnectionError("network down")), default_llm, default_emb)
    assert choice.llm is default_llm and not choice.switched
