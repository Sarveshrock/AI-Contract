"""Google Gemini provider (chat / structured output and embeddings) over the public REST API. No extra SDK is required."""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.agents.llm import LLMCall
from app.core.errors import AIResponseError, AIUnavailableError, TransientError
from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_call

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class ModelUnavailable(AIUnavailableError):
    """The chosen model exists no more (or is not offered to this key); another model may still work."""


#: Tried in order after the configured model when it is overloaded (503) or unavailable (404).
FALLBACK_MODELS = ("gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-flash-lite-latest")


class _SchemaRejected(Exception):
    """Gemini refused the JSON schema itself (it supports only a subset); the caller retries with the schema in the prompt."""


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    text = resp.text[:600]
    low = text.lower()
    code = resp.status_code
    if code == 400 and ("api key not valid" in low or "api_key_invalid" in low):
        raise AIUnavailableError("gemini key invalid", user_message="Google rejected the Gemini API key. Check GEMINI_API_KEY.")
    if code == 400 and "schema" in low:
        raise _SchemaRejected(text)
    if code in (401, 403):
        raise AIUnavailableError(f"gemini http {code}", user_message="Google rejected the Gemini API key or the key may not be allowed to use this model. Check GEMINI_API_KEY.")
    if code == 404:
        raise ModelUnavailable("gemini model not found", user_message="The configured Gemini model was not found. Check GEMINI_MODEL / GEMINI_EMBEDDING_MODEL.")
    if code == 429:
        if "limit: 0" in low:
            raise AIUnavailableError("gemini quota zero", user_message="This Gemini key has no quota for the selected model. Enable billing or pick another GEMINI_MODEL.")
        raise TransientError("gemini rate limited", user_message="The Gemini service is rate limited. Try again in a moment.")
    if code >= 500:
        raise TransientError(f"gemini http {code}", user_message="The Gemini service is temporarily unavailable.")
    raise AIResponseError(f"gemini http {code}: {text[:200]}", user_message="The AI request failed.")


class _GeminiHttp:
    def __init__(self, api_key: str, timeout: float) -> None:
        self._key, self._timeout = api_key, timeout

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = httpx.post(f"{BASE_URL}/{path}", headers={"x-goog-api-key": self._key, "content-type": "application/json"}, json=body, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise TransientError(f"gemini network: {type(exc).__name__}", user_message="Could not reach the Gemini service. Check the internet connection.") from exc
        _raise_for(resp)
        return resp.json()


def _json_from_text(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    return m.group(1) if m else t


class GeminiLLM:
    available = True

    def __init__(self, api_key: str, model: str, *, timeout: float = 90.0) -> None:
        self._http = _GeminiHttp(api_key, timeout)
        self.model = model
        self.last_call: LLMCall | None = None

    def structured(self, *, system: str, user: str, schema: type[T], purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> T:
        schema_json = schema.model_json_schema()

        def body(with_schema: bool) -> dict[str, Any]:
            sys_text = system + "\n\nRespond with a single JSON object only."
            gen: dict[str, Any] = {"responseMimeType": "application/json"}
            if with_schema:
                gen["responseJsonSchema"] = schema_json
            else:
                sys_text += "\nThe JSON must match this JSON Schema exactly:\n" + json.dumps(schema_json)
            if temperature is not None:
                gen["temperature"] = temperature
            if max_tokens:
                gen["maxOutputTokens"] = max_tokens
            return {"systemInstruction": {"parts": [{"text": sys_text}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}

        def call() -> T:
            started = time.monotonic()
            data, last_exc = None, None
            for m in dict.fromkeys([self.model, *FALLBACK_MODELS]):  # configured model first, then alternatives
                path = f"models/{m}:generateContent"
                try:
                    try:
                        data = self._http.post(path, body(True))
                    except _SchemaRejected:
                        data = self._http.post(path, body(False))
                except (TransientError, ModelUnavailable) as exc:
                    last_exc = exc
                    continue
                if m != self.model:
                    log.warning("Gemini model %s unavailable; using %s", self.model, m)
                    self.model = m
                break
            if data is None:
                raise last_exc  # type: ignore[misc]
            cands = data.get("candidates") or []
            if not cands:
                raise AIResponseError("gemini no candidates", user_message="The AI declined to process this content.")
            cand = cands[0]
            reason = cand.get("finishReason", "")
            if reason == "MAX_TOKENS":
                raise AIResponseError("output truncated", user_message="The AI response was cut off. Try a smaller document section.")
            if reason in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION"):
                raise AIResponseError(f"gemini blocked: {reason}", user_message="The AI declined to process this content.")
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
            try:
                parsed = schema.model_validate_json(_json_from_text(text))
            except Exception as exc:  # noqa: BLE001
                raise AIResponseError(f"schema mismatch: {type(exc).__name__}", user_message="The AI returned a response that did not match the required format.") from exc
            usage = data.get("usageMetadata") or {}
            self.last_call = LLMCall(purpose, int(usage.get("promptTokenCount", 0) or 0), int(usage.get("candidatesTokenCount", 0) or 0), time.monotonic() - started)
            return parsed

        return retry_call(call, RetryPolicy(max_attempts=3, retry_on=(TransientError,)), label=f"llm:{purpose}")

    def probe(self) -> None:
        """Cheapest possible call; raises AIUnavailableError only when no model works. Overload (503) counts as usable."""
        last: Exception | None = None
        for m in dict.fromkeys([self.model, *FALLBACK_MODELS]):
            try:
                self._http.post(f"models/{m}:generateContent", {"contents": [{"role": "user", "parts": [{"text": "ok"}]}], "generationConfig": {"maxOutputTokens": 8}})
                self.model = m
                return
            except ModelUnavailable as exc:
                last = exc
            except TransientError:
                return  # the key works; the service is just busy right now
        if last:
            raise last


class GeminiEmbedder:
    relevance_floor = 0.30
    BATCH = 64

    def __init__(self, api_key: str, model: str = "gemini-embedding-001", *, dim: int = 768, timeout: float = 60.0) -> None:
        self._http = _GeminiHttp(api_key, timeout)
        self.model = model
        self.dim = dim
        self.name = f"gemini:{model}:{dim}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = [t.replace("\n", " ")[:8000] or " " for t in texts[i : i + self.BATCH]]
            out.extend(retry_call(lambda b=batch: self._call(b), label="gemini embeddings"))
        return out

    def _call(self, batch: list[str]) -> list[list[float]]:
        body = {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}, "outputDimensionality": self.dim} for t in batch]}
        data = self._http.post(f"models/{self.model}:batchEmbedContents", body)
        vectors = [e.get("values", []) for e in data.get("embeddings", [])]
        if len(vectors) != len(batch):
            raise AIResponseError("gemini embedding count mismatch", user_message="The embedding service returned an unexpected response.")
        normed = []
        for v in vectors:  # truncated embeddings are not unit length; cosine search expects them to be
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            normed.append([x / n for x in v])
        return normed

    def probe(self) -> None:
        self._call(["ok"])
