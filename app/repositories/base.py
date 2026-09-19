"""Generic organisation-scoped repository.

Every read is filtered by ``org_id`` and every write is forced into the repository's organisation.
On Supabase, RLS enforces the same rule server-side; in local mode this is the enforcement point.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from app.core.errors import AuthorizationError, DatabaseError
from app.database.store import F, Filter, OrderBy, TableStore
from app.models.base import OrgScopedModel, TableModel

M = TypeVar("M", bound=TableModel)
OM = TypeVar("OM", bound=OrgScopedModel)


class GlobalRepository(Generic[M]):
    """For tables that are not organisation scoped (organizations, profiles)."""

    def __init__(self, store: TableStore, model: type[M]) -> None:
        self.store = store
        self.model = model
        self.table = model.table_name

    def get(self, id_: UUID | str) -> M | None:
        rows = self.store.select(self.table, [F.eq("id", str(id_))], limit=1)
        return self.model.from_row(rows[0]) if rows else None

    def list(self, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None) -> list[M]:
        return [self.model.from_row(r) for r in self.store.select(self.table, filters, order_by=order_by, limit=limit)]

    def add(self, item: M) -> M:
        rows = self.store.insert(self.table, [item.to_row()])
        return self.model.from_row(rows[0]) if rows else item

    def upsert(self, items: Sequence[M], on_conflict: Sequence[str] = ("id",)) -> list[M]:
        rows = self.store.upsert(self.table, [i.to_row() for i in items], on_conflict)
        return [self.model.from_row(r) for r in rows]

    def update(self, id_: UUID | str, **values: Any) -> M:
        rows = self.store.update(self.table, _plain(values), [F.eq("id", str(id_))])
        if not rows:
            raise DatabaseError(f"{self.table} {id_} not found or not permitted", user_message="The record was not found or you do not have permission to change it.")
        return self.model.from_row(rows[0])


class Repository(Generic[OM]):
    def __init__(self, store: TableStore, model: type[OM], org_id: UUID | str) -> None:
        self.store = store
        self.model = model
        self.table = model.table_name
        self.org_id = str(org_id)

    # -- reads
    def _scoped(self, filters: Sequence[Filter]) -> list[Filter]:
        return [F.eq("org_id", self.org_id), *filters]

    def get(self, id_: UUID | str | None) -> OM | None:
        if id_ is None:
            return None
        rows = self.store.select(self.table, self._scoped([F.eq("id", str(id_))]), limit=1)
        return self.model.from_row(rows[0]) if rows else None

    def require(self, id_: UUID | str) -> OM:
        item = self.get(id_)
        if item is None:
            raise DatabaseError(f"{self.table} {id_} not found", user_message="The requested record was not found.")
        return item

    def list(self, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None, offset: int = 0) -> list[OM]:
        rows = self.store.select(self.table, self._scoped(filters), order_by=order_by, limit=limit, offset=offset)
        return [self.model.from_row(r) for r in rows]

    def count(self, filters: Sequence[Filter] = ()) -> int:
        return self.store.count(self.table, self._scoped(filters))

    def first(self, filters: Sequence[Filter] = (), *, order_by: OrderBy = ()) -> OM | None:
        rows = self.list(filters, order_by=order_by, limit=1)
        return rows[0] if rows else None

    # -- writes
    def _check_org(self, item: OM) -> None:
        if str(item.org_id) != self.org_id:
            raise AuthorizationError(
                f"cross-organisation write to {self.table}: {item.org_id} != {self.org_id}",
                user_message="This operation would write data into a different organisation and was blocked.",
            )

    def add(self, item: OM) -> OM:
        self._check_org(item)
        rows = self.store.insert(self.table, [item.to_row()])
        return self.model.from_row(rows[0]) if rows else item

    def add_many(self, items: Sequence[OM]) -> list[OM]:
        for i in items:
            self._check_org(i)
        rows = self.store.insert(self.table, [i.to_row() for i in items])
        return [self.model.from_row(r) for r in rows]

    def upsert(self, items: Sequence[OM], on_conflict: Sequence[str]) -> list[OM]:
        for i in items:
            self._check_org(i)
        rows = self.store.upsert(self.table, [i.to_row() for i in items], on_conflict)
        return [self.model.from_row(r) for r in rows]

    def update(self, id_: UUID | str, **values: Any) -> OM:
        values.pop("org_id", None)
        rows = self.store.update(self.table, _plain(values), self._scoped([F.eq("id", str(id_))]))
        if not rows:
            raise DatabaseError(f"{self.table} {id_} not found or not permitted", user_message="The record was not found or you do not have permission to change it.")
        return self.model.from_row(rows[0])

    def update_where(self, filters: Sequence[Filter], **values: Any) -> list[OM]:
        values.pop("org_id", None)
        rows = self.store.update(self.table, _plain(values), self._scoped(filters))
        return [self.model.from_row(r) for r in rows]

    def delete(self, id_: UUID | str) -> bool:
        return self.store.delete(self.table, self._scoped([F.eq("id", str(id_))])) > 0

    def delete_where(self, filters: Sequence[Filter]) -> int:
        return self.store.delete(self.table, self._scoped(filters))


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    """Convert python values (UUID, enum, date, datetime) into JSON-compatible primitives."""
    from datetime import date, datetime
    from enum import Enum

    out: dict[str, Any] = {}
    for k, v in values.items():
        if isinstance(v, Enum):
            v = v.value
        elif isinstance(v, UUID):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    return out
