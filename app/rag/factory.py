"""Selects the vector-store backend and guards against native ChromaDB crashes.

The embedded ChromaDB engine is native code. On some machines it aborts the whole process
(access violation), which no ``try/except`` can catch. We therefore run a self-test in a *child
process* first and cache the verdict; if it fails we fall back to :class:`SqliteVectorStore` and tell
the user. HTTP mode is never silently downgraded (a shared index must not fork).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config.settings import Settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.rag.sqlite_vector_store import SqliteVectorStore
from app.rag.vector_store import ChromaVectorStore, InMemoryVectorStore, VectorStore, VectorStoreInfo

log = get_logger(__name__)

_PROBE = r"""
import sys, tempfile
import chromadb
from chromadb.config import Settings
c = chromadb.PersistentClient(path=tempfile.mkdtemp(), settings=Settings(anonymized_telemetry=False))
col = c.get_or_create_collection("probe", embedding_function=None)
col.upsert(ids=["a"], embeddings=[[1.0, 0.0, 0.0]], documents=["x"], metadatas=[{"k": "v"}])
r = col.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)
assert r["ids"][0] == ["a"]
print("ok")
"""


def chroma_version() -> str | None:
    try:
        import importlib.metadata as md

        return md.version("chromadb")
    except Exception:  # noqa: BLE001
        return None


def probe_embedded_chroma(cache_file: Path, *, timeout: float = 90.0, force: bool = False) -> tuple[bool, str]:
    """Run an embedded-Chroma write/query in a child process. Cached per chromadb+python version."""
    version = chroma_version()
    if version is None:
        return False, "chromadb is not installed"
    key = f"{version}|{sys.version_info[:3]}|{sys.platform}"
    if not force and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return bool(cached["ok"]), str(cached["detail"])
        except (OSError, ValueError, KeyError):
            pass
    try:
        proc = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
        ok = proc.returncode == 0 and "ok" in proc.stdout
        detail = "self-test passed" if ok else f"self-test failed (exit code {proc.returncode})"
    except subprocess.TimeoutExpired:
        ok, detail = False, "self-test timed out"
    except OSError as exc:
        ok, detail = False, f"self-test could not start: {exc}"
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"key": key, "ok": ok, "detail": detail}), encoding="utf-8")
    except OSError:
        pass
    return ok, detail


def build_vector_store(settings: Settings) -> tuple[VectorStore, VectorStoreInfo]:
    mode = settings.chroma_mode
    if mode == "http":
        token = settings.chroma_token.get_secret_value() if settings.chroma_token else None
        try:
            store = ChromaVectorStore.http(settings.chroma_host, settings.chroma_port, settings.chroma_ssl, token)
            store._client.heartbeat()  # noqa: SLF001 - fail fast if the server is unreachable
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(f"chroma http unreachable: {exc}", user_message=f"Cannot reach the ChromaDB server at {settings.chroma_host}:{settings.chroma_port}. Check CHROMA_HOST/CHROMA_PORT and that the server is running.") from exc
        return store, VectorStoreInfo("chromadb-http", True, f"{settings.chroma_host}:{settings.chroma_port}")
    if mode == "memory":
        return InMemoryVectorStore(), VectorStoreInfo("memory", False, "Volatile in-memory index (testing only)")
    ok, detail = probe_embedded_chroma(settings.data_dir / ".chroma_probe.json")
    if ok:
        return ChromaVectorStore.persistent(settings.chroma_dir), VectorStoreInfo("chromadb-persistent", True, str(settings.chroma_dir))
    log.warning("Embedded ChromaDB unavailable (%s); using the local SQLite vector index instead.", detail)
    return (
        SqliteVectorStore(settings.data_dir / "vectors.db"),
        VectorStoreInfo("sqlite-fallback", True, f"Embedded ChromaDB failed its self-test ({detail}). Using local SQLite vector index. Run a Chroma server and set CHROMA_MODE=http to use ChromaDB.", {"chroma_probe": detail}),
    )
