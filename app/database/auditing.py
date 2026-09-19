"""Row-level audit trail for the local SQLite store.

On Postgres the same job is done by the ``audit_row_change`` trigger; this wrapper reproduces it so
local mode behaves identically.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database.store import Filter, OrderBy, TableStore
from app.models.registry import TABLES


class AuditingStore:
    def __init__(self, inner: TableStore, actor: Callable[[], tuple[str | None, str | None]]) -> None:
        self._inner = inner
        self._actor = actor
        self._audited = {name for name, m in TABLES.items() if m.table_policy.audited}

    # -- pass-through reads
    def select(self, table: str, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None,
               offset: int = 0, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        return self._inner.select(table, filters, order_by=order_by, limit=limit, offset=offset, columns=columns)

    def count(self, table: str, filters: Sequence[Filter] = ()) -> int:
        return self._inner.count(table, filters)

    def transaction(self) -> AbstractContextManager[None]:
        return self._inner.transaction()

    # -- audited writes
    def _log(self, table: str, action: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        row = after or before or {}
        org_id = row.get("org_id") or (row.get("id") if table == "organizations" else None)
        if not org_id:
            return
        actor_id, actor_email = self._actor()
        self._inner.insert("audit_logs", [{
            "id": str(uuid4()), "org_id": org_id, "actor_id": actor_id, "actor_email": actor_email,
            "action": f"{table}.{action}", "entity_type": table, "entity_id": row.get("id"),
            "before": before, "after": after, "metadata": {"source": "row_trigger"},
            "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
        }])

    def insert(self, table: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        out = self._inner.insert(table, rows)
        if table in self._audited:
            for r in out:
                self._log(table, "insert", None, r)
        return out

    def upsert(self, table: str, rows: Sequence[dict[str, Any]], on_conflict: Sequence[str]) -> list[dict[str, Any]]:
        out = self._inner.upsert(table, rows, on_conflict)
        if table in self._audited:
            for r in out:
                self._log(table, "upsert", None, r)
        return out

    def update(self, table: str, values: dict[str, Any], filters: Sequence[Filter]) -> list[dict[str, Any]]:
        if table not in self._audited:
            return self._inner.update(table, values, filters)
        before = {r["id"]: r for r in self._inner.select(table, filters)}
        out = self._inner.update(table, values, filters)
        for r in out:
            self._log(table, "update", before.get(r["id"]), r)
        return out

    def delete(self, table: str, filters: Sequence[Filter]) -> int:
        if table not in self._audited:
            return self._inner.delete(table, filters)
        before = self._inner.select(table, filters)
        n = self._inner.delete(table, filters)
        for r in before:
            self._log(table, "delete", r, None)
        return n
