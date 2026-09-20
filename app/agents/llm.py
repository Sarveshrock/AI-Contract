"""LLM access. Structured outputs only; refusals, truncation and transient failures are explicit."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.config.settings import Settings
from app.core.errors import QUOTA_MESSAGE, AIResponseError, AIUnavailableError, TransientError, is_quota_exhausted
from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_call

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)


@dataclass
class LLMCall:
    purpose: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    ok: bool = True
    error: str | None = None


class LLMClient(Protocol):
    model: str
    available: bool

    def structured(self, *, system: str, user: str, schema: type[T], purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> T: ...


class UnavailableLLM:
    """Used when no API key/proxy is configured. Never fabricates output."""

    model = "unavailable"
    available = False

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason  # e.g. "out of OpenAI credits"; shown instead of the generic "not configured" text

    def structured(self, **_: Any) -> Any:
        raise AIUnavailableError(
            "no llm configured",
            user_message=self.reason or "AI analysis is not configured. Set OPENAI_API_KEY (or OPENAI_BASE_URL for the Edge Function proxy) in .env and restart.",
        )


class OpenAILLM:
    available = True

    def __init__(self, client: Any, model: str, *, timeout: float = 90.0) -> None:
        self._client = client
        self.model = model
        self._timeout = timeout

    def structured(self, *, system: str, user: str, schema: type[T], purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> T:
        def call() -> T:
            kwargs: dict[str, Any] = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                      "response_format": schema, "timeout": self._timeout}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            started = time.monotonic()
            try:
                completion = self._client.chat.completions.parse(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc) from exc
            choice = completion.choices[0]
            if choice.finish_reason == "length":
                raise AIResponseError("output truncated", user_message="The AI response was cut off. Try a smaller document section.")
            if getattr(choice.message, "refusal", None):
                raise AIResponseError(f"refusal: {choice.message.refusal}", user_message="The AI declined to process this content.")
            parsed = choice.message.parsed
            if parsed is None:
                raise AIResponseError("unparseable structured output", user_message="The AI returned a response that did not match the required format.")
            usage = getattr(completion, "usage", None)
            self.last_call = LLMCall(purpose, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0, time.monotonic() - started)
            return parsed

        return retry_call(call, RetryPolicy(max_attempts=3, retry_on=(TransientError,)), label=f"llm:{purpose}")

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        name = type(exc).__name__
        if is_quota_exhausted(exc):
            return AIUnavailableError("openai quota exhausted", user_message=QUOTA_MESSAGE)
        if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"):
            return TransientError(f"openai {name}", user_message="The AI service is temporarily unavailable or rate limited.")
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return AIUnavailableError(f"openai {name}", user_message="OpenAI rejected the credentials. Check OPENAI_API_KEY (or your session, when using the proxy).")
        if name in ("LengthFinishReasonError",):
            return AIResponseError("output truncated", user_message="The AI response was cut off. Try a smaller document section.")
        if name in ("ContentFilterFinishReasonError",):
            return AIResponseError("content filter", user_message="The AI declined to process this content.")
        if name == "NotFoundError":
            return AIUnavailableError(str(exc), user_message="The configured OpenAI model was not found. Check OPENAI_MODEL.")
        return AIResponseError(f"openai error {name}: {exc}", user_message="The AI request failed.")


@dataclass
class MeteredLLM:
    """Wraps a client to record calls and token usage for run logging."""

    inner: LLMClient
    calls: list[LLMCall] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def available(self) -> bool:
        return self.inner.available

    @property
    def offline_rules(self) -> bool:
        return bool(getattr(self.inner, "offline_rules", False))

    def structured(self, *, system: str, user: str, schema: type[T], purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> T:
        started = time.monotonic()
        try:
            out = self.inner.structured(system=system, user=user, schema=schema, purpose=purpose, temperature=temperature, max_tokens=max_tokens)
        except Exception as exc:
            self.calls.append(LLMCall(purpose, seconds=time.monotonic() - started, ok=False, error=type(exc).__name__))
            raise
        inner_call: LLMCall | None = getattr(self.inner, "last_call", None)
        self.calls.append(LLMCall(purpose, inner_call.prompt_tokens if inner_call else 0, inner_call.completion_tokens if inner_call else 0, time.monotonic() - started))
        return out

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)


class AnthropicLLM:
    """Claude via the Anthropic API. Structured output is obtained by forcing a single tool call whose input schema is the Pydantic model."""

    available = True

    def __init__(self, api_key: str, model: str, *, timeout: float = 90.0) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)
        self.model = model
        self.last_call: LLMCall | None = None

    def structured(self, *, system: str, user: str, schema: type[T], purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> T:
        def call() -> T:
            tool = {"name": "respond", "description": "Return the result in exactly the required structure.", "input_schema": schema.model_json_schema()}
            kwargs: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens or 4096, "system": system, "messages": [{"role": "user", "content": user}],
                                      "tools": [tool], "tool_choice": {"type": "tool", "name": "respond"}}
            if temperature is not None:
                kwargs["temperature"] = temperature
            started = time.monotonic()
            try:
                msg = self._client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise self._translate(exc) from exc
            if msg.stop_reason == "max_tokens":
                raise AIResponseError("output truncated", user_message="The AI response was cut off. Try a smaller document section.")
            block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
            if block is None:
                raise AIResponseError("no structured output", user_message="The AI returned a response that did not match the required format.")
            try:
                parsed = schema.model_validate(block.input)
            except Exception as exc:  # noqa: BLE001
                raise AIResponseError(f"schema mismatch: {type(exc).__name__}", user_message="The AI returned a response that did not match the required format.") from exc
            usage = getattr(msg, "usage", None)
            self.last_call = LLMCall(purpose, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0, time.monotonic() - started)
            return parsed

        return retry_call(call, RetryPolicy(max_attempts=3, retry_on=(TransientError,)), label=f"llm:{purpose}")

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        name = type(exc).__name__
        if is_quota_exhausted(exc):
            return AIUnavailableError("anthropic credits exhausted", user_message="The Anthropic account has no credits left. Add credits in the Anthropic console.")
        if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError", "OverloadedError"):
            return TransientError(f"anthropic {name}", user_message="The AI service is temporarily unavailable or rate limited.")
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return AIUnavailableError(f"anthropic {name}", user_message="Anthropic rejected the API key. Check ANTHROPIC_API_KEY.")
        if name == "NotFoundError":
            return AIUnavailableError(str(exc), user_message="The configured Claude model was not found. Check ANTHROPIC_MODEL.")
        return AIResponseError(f"anthropic error {name}", user_message="The AI request failed.")


def build_claude_llm(settings: Settings) -> "AnthropicLLM | None":
    if not settings.claude_configured:
        return None
    return AnthropicLLM(settings.anthropic_api_key.get_secret_value(), settings.anthropic_model, timeout=settings.openai_timeout_s)


def build_openai_client(settings: Settings, access_token: str | None = None) -> Any | None:
    """Create the OpenAI SDK client. In proxy mode the *user's Supabase JWT* is the credential, so no
    OpenAI key ever exists on the desktop."""
    if not settings.ai_configured:
        return None
    from openai import OpenAI

    if settings.openai_base_url:
        token = access_token or (settings.openai_api_key.get_secret_value() if settings.openai_api_key else None)
        if not token:
            return None
        return OpenAI(api_key=token, base_url=settings.openai_base_url, timeout=settings.openai_timeout_s, max_retries=0)
    return OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_s, max_retries=0)


def build_llm(settings: Settings, access_token: str | None = None) -> LLMClient:
    client = build_openai_client(settings, access_token)
    if client is None:
        return build_claude_llm(settings) or UnavailableLLM()
    return OpenAILLM(client, settings.openai_model, timeout=settings.openai_timeout_s)
