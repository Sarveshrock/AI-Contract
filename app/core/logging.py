"""Structured JSON logging with redaction. Contract text must never be passed to loggers."""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.security.redaction import redact_mapping, redact_text

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "ctx"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload["ctx"] = redact_mapping(ctx)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            payload["extra"] = redact_mapping(extras)
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: {redact_text(record.getMessage())}"
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict) and ctx:
            base += " " + json.dumps(redact_mapping(ctx), default=str)
        if record.exc_info:
            base += "\n" + redact_text(self.formatException(record.exc_info))
        return base


_configured = False


def configure_logging(level: str = "INFO", log_dir: Path | None = None, *, json_console: bool = False) -> None:
    global _configured
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level.upper())
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(JsonFormatter() if json_console else ConsoleFormatter())
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "contractlens.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "chromadb", "urllib3", "openai", "PIL", "posthog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_ctx(**ctx: Any) -> dict[str, Any]:
    """Helper: ``logger.info("msg", extra=log_ctx(a=1))``."""
    return {"ctx": ctx}
