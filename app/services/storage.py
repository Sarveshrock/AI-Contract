"""Document file storage: Supabase Storage (private bucket) or the local filesystem."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import StorageError, TransientError
from app.core.retry import retry_call


class FileStorage(Protocol):
    def put(self, path: str, data: bytes, content_type: str) -> None: ...

    def get(self, path: str) -> bytes: ...

    def delete(self, path: str) -> None: ...


_SAFE = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = _SAFE.sub("_", base).strip("._") or "document"
    return cleaned[:120]


def object_path(org_id: str, contract_id: str, document_id: str, filename: str) -> str:
    """Object path convention; the first segment is the organisation id (used by storage RLS)."""
    return f"{org_id}/{contract_id}/{document_id}/{safe_filename(filename)}"


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if self.root not in target.parents:
            raise StorageError(f"path traversal blocked: {path}", user_message="Invalid storage path.")
        return target

    def put(self, path: str, data: bytes, content_type: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, path: str) -> bytes:
        target = self._resolve(path)
        if not target.exists():
            raise StorageError(f"missing object {path}", user_message="The stored document file could not be found.")
        return target.read_bytes()

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.exists():
            target.unlink()


class SupabaseFileStorage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _bucket_api(self) -> Any:
        return self._client.storage.from_(self._bucket)

    def _call(self, fn: Any, label: str) -> Any:
        def run() -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                if name in ("ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError"):
                    raise TransientError(f"{label}: {name}", user_message="Cannot reach Supabase Storage.") from exc
                raise StorageError(f"{label} failed: {exc}", user_message="The document storage request failed.") from exc

        return retry_call(run, label=label)

    def put(self, path: str, data: bytes, content_type: str) -> None:
        self._call(lambda: self._bucket_api().upload(path, data, {"content-type": content_type, "upsert": "true"}), "storage upload")

    def get(self, path: str) -> bytes:
        return bytes(self._call(lambda: self._bucket_api().download(path), "storage download"))

    def delete(self, path: str) -> None:
        self._call(lambda: self._bucket_api().remove([path]), "storage delete")
