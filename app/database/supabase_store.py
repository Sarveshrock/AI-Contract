"""Supabase (PostgREST) implementation of :class:`TableStore`.

All calls run with the signed-in user's JWT, so Postgres Row Level Security is the authority.
The client is created with the anon key only.
"""
from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any

from app.core.errors import ConfigurationError, DatabaseError, TransientError
from app.core.logging import get_logger
from app.core.retry import retry_call
from app.database.store import Filter, OrderBy, no_transaction, normalize_value

log = get_logger(__name__)


def create_supabase_client(url: str, anon_key: str) -> Any:
    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover
        raise ConfigurationError("supabase package is not installed", user_message="The 'supabase' Python package is not installed.") from exc
    return create_client(url, anon_key)


def apply_filters(query: Any, filters: Sequence[Filter]) -> Any:
    """Translate :class:`Filter` objects into PostgREST builder calls."""
    for raw in filters:
        f = Filter(raw.column, raw.op, normalize_value(raw.value))
        if f.op == "eq":
            query = query.is_(f.column, "null") if f.value is None else query.eq(f.column, f.value)
        elif f.op == "neq":
            query = query.not_.is_(f.column, "null") if f.value is None else query.neq(f.column, f.value)
        elif f.op == "gt":
            query = query.gt(f.column, f.value)
        elif f.op == "gte":
            query = query.gte(f.column, f.value)
        elif f.op == "lt":
            query = query.lt(f.column, f.value)
        elif f.op == "lte":
            query = query.lte(f.column, f.value)
        elif f.op == "in":
            query = query.in_(f.column, list(f.value or []))
        elif f.op == "is_null":
            query = query.is_(f.column, "null")
        elif f.op == "not_null":
            query = query.not_.is_(f.column, "null")
        elif f.op == "ilike":
            query = query.ilike(f.column, f.value)
    return query


class SupabaseTableStore:
    PAGE = 1000  # PostgREST default max rows per request

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        return self._client

    def _execute(self, builder: Any, label: str) -> Any:
        def call() -> Any:
            try:
                return builder.execute()
            except Exception as exc:  # noqa: BLE001 - normalise SDK errors
                name = type(exc).__name__
                msg = str(exc)
                if name in ("ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError", "PoolTimeout"):
                    raise TransientError(f"{label}: {name}", user_message="Cannot reach Supabase. Check your network connection.") from exc
                if "JWT" in msg and "expired" in msg.lower():
                    raise DatabaseError(msg, user_message="Your session has expired. Please sign in again.") from exc
                if "row-level security" in msg.lower() or "42501" in msg:
                    raise DatabaseError(msg, user_message="You do not have permission to perform this action.") from exc
                if "duplicate key" in msg.lower() or "23505" in msg:
                    raise DatabaseError(msg, user_message="A record with the same unique values already exists.") from exc
                raise DatabaseError(f"{label} failed: {msg}", user_message="The database request failed.") from exc

        return retry_call(call, label=label)

    def select(self, table: str, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None,
               offset: int = 0, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        cols = ",".join(columns) if columns else "*"
        rows: list[dict[str, Any]] = []
        start = offset
        while True:
            page = self.PAGE if limit is None else min(self.PAGE, limit - len(rows))
            if page <= 0:
                break
            q = apply_filters(self._client.table(table).select(cols), filters)
            for col, desc in order_by:
                q = q.order(col, desc=desc)
            res = self._execute(q.range(start, start + page - 1), f"select {table}")
            data = list(res.data or [])
            rows.extend(data)
            if len(data) < page:
                break
            start += page
        return rows

    def count(self, table: str, filters: Sequence[Filter] = ()) -> int:
        q = apply_filters(self._client.table(table).select("id", count="exact"), filters).limit(1)
        res = self._execute(q, f"count {table}")
        return int(res.count or 0)

    def insert(self, table: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        return list(self._execute(self._client.table(table).insert(list(rows)), f"insert {table}").data or [])

    def upsert(self, table: str, rows: Sequence[dict[str, Any]], on_conflict: Sequence[str]) -> list[dict[str, Any]]:
        if not rows:
            return []
        q = self._client.table(table).upsert(list(rows), on_conflict=",".join(on_conflict))
        return list(self._execute(q, f"upsert {table}").data or [])

    def update(self, table: str, values: dict[str, Any], filters: Sequence[Filter]) -> list[dict[str, Any]]:
        if not values:
            return []
        q = apply_filters(self._client.table(table).update(values), filters)
        return list(self._execute(q, f"update {table}").data or [])

    def delete(self, table: str, filters: Sequence[Filter]) -> int:
        if not filters:
            raise DatabaseError("Refusing unfiltered delete", user_message="Refusing to delete without a filter.")
        q = apply_filters(self._client.table(table).delete(), filters)
        return len(self._execute(q, f"delete {table}").data or [])

    def transaction(self) -> AbstractContextManager[None]:
        return no_transaction()
