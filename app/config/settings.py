"""Application configuration. All secrets come from the environment / .env, never from code."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # bundled, read-only resources (migrations, samples) live here
FROZEN = bool(getattr(sys, "frozen", False))


def _app_home() -> Path:
    """Where the user's settings and data live. Installed builds must not write into the (read-only) install folder."""
    if FROZEN:
        return Path(os.environ.get("APPDATA") or Path.home()) / "ContractLens"
    return PROJECT_ROOT


APP_HOME = _app_home()


def ensure_app_home() -> Path:
    """First run of an installed build: create the folder and a starter .env from the bundled example. Never overwrites."""
    if FROZEN:
        APP_HOME.mkdir(parents=True, exist_ok=True)
        env, example = APP_HOME / ".env", PROJECT_ROOT / ".env.example"
        if not env.exists() and example.exists():
            shutil.copyfile(example, env)
    return APP_HOME


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(APP_HOME / ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    # --- runtime -------------------------------------------------------------------------
    contractlens_mode: Literal["auto", "supabase", "local"] = "auto"
    log_level: str = "INFO"
    data_dir: Path = APP_HOME / "data"
    reduced_motion: bool = False

    # --- Supabase (anon key only; service-role keys are refused) -------------------------
    supabase_url: str | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_storage_bucket: str = "contracts"

    # --- OpenAI ---------------------------------------------------------------------------
    openai_api_key: SecretStr | None = None
    #: Point at the ``openai-proxy`` Edge Function (…/functions/v1/openai-proxy) to keep the key server-side.
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_timeout_s: float = 90.0
    #: Optional alternative chat/analysis model. Used when OpenAI is not configured or its balance is empty.
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5"
    #: Optional Google Gemini for chat/analysis and (when OpenAI is unavailable) embeddings. Has a free tier.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dim: int = 768
    embedding_provider: Literal["auto", "openai", "gemini", "hashing"] = "auto"

    # --- ChromaDB -------------------------------------------------------------------------
    chroma_mode: Literal["persistent", "http", "memory"] = "persistent"
    chroma_path: Path | None = None
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_ssl: bool = False
    chroma_token: SecretStr | None = None

    # --- document processing --------------------------------------------------------------
    max_upload_mb: int = 50
    max_pages: int = 600
    chunk_target_chars: int = 1200
    chunk_overlap_chars: int = 150
    tesseract_cmd: str | None = None
    ocr_languages: str = "eng"
    ocr_dpi: int = 200

    # --- agents / quality -----------------------------------------------------------------
    agent_max_attempts: int = 3
    quality_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    retrieval_min_score: float = 0.05

    # --- retention ------------------------------------------------------------------------
    default_retention_days: int | None = None

    @field_validator("supabase_url", "openai_base_url", "tesseract_cmd", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    # --- derived --------------------------------------------------------------------------
    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key and self.supabase_anon_key.get_secret_value())

    @property
    def mode(self) -> Literal["supabase", "local"]:
        if self.contractlens_mode == "supabase":
            if not self.supabase_configured:
                raise ConfigurationError("CONTRACTLENS_MODE=supabase but SUPABASE_URL / SUPABASE_ANON_KEY are not set",
                                         user_message="Supabase is selected but SUPABASE_URL and SUPABASE_ANON_KEY are missing in .env.")
            return "supabase"
        if self.contractlens_mode == "local":
            return "local"
        return "supabase" if self.supabase_configured else "local"

    @property
    def is_demo(self) -> bool:
        return self.mode == "local"

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value())

    @property
    def claude_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value()) or bool(self.openai_base_url)

    @property
    def chroma_dir(self) -> Path:
        return self.chroma_path or (self.data_dir / "chroma")

    @property
    def local_db_path(self) -> Path:
        return self.data_dir / "contractlens_local.db"

    @property
    def local_storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def validate_security(self) -> None:
        """Refuse privileged keys in the desktop client."""
        if self.supabase_anon_key is not None:
            role = jwt_role(self.supabase_anon_key.get_secret_value())
            if role == "service_role":
                raise ConfigurationError(
                    "SUPABASE_ANON_KEY contains a service_role key",
                    user_message="A Supabase service-role key was found in the configuration. Service-role keys must never be used in the desktop client. Use the anon (publishable) key.",
                )


def jwt_role(token: str) -> str | None:
    """Return the ``role`` claim of a (possibly non-JWT) key without verifying it."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(padded)).get("role")
    except Exception:  # noqa: BLE001 - opaque publishable keys are fine
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_security()
    return settings
