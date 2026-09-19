"""Storage-agnostic table access. Repositories are written once against this protocol."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

FILTER_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "is_null", "not_null", "ilike"}


@dataclass(frozen=True)
class Filter:
    column: str
    op: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.op not in FILTER_OPS:
            raise ValueError(f"Unsupported filter operator: {self.op}")


class F:
    """Filter constructors: ``F.eq("status", "open")``."""

    @staticmethod
    def eq(col: str, value: Any) -> Filter:
        return Filter(col, "eq", value)

    @staticmethod
    def neq(col: str, value: Any) -> Filter:
        return Filter(col, "neq", value)

    @staticmethod
    def gt(col: str, value: Any) -> Filter:
        return Filter(col, "gt", value)

    @staticmethod
    def gte(col: str, value: Any) -> Filter:
        return Filter(col, "gte", value)

    @staticmethod
    def lt(col: str, value: Any) -> Filter:
        return Filter(col, "lt", value)

    @staticmethod
    def lte(col: str, value: Any) -> Filter:
        return Filter(col, "lte", value)

    @staticmethod
    def in_(col: str, values: Sequence[Any]) -> Filter:
        return Filter(col, "in", list(values))

    @staticmethod
    def is_null(col: str) -> Filter:
        return Filter(col, "is_null")

    @staticmethod
    def not_null(col: str) -> Filter:
        return Filter(col, "not_null")

    @staticmethod
    def ilike(col: str, pattern: str) -> Filter:
        return Filter(col, "ilike", pattern)


OrderBy = Sequence[tuple[str, bool]]  # (column, descending)


def normalize_value(value: Any) -> Any:
    """Convert python values into JSON/SQL-friendly primitives (used for filter values)."""
    from datetime import date, datetime
    from enum import Enum
    from uuid import UUID

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    return value


@runtime_checkable
class TableStore(Protocol):
    """Minimal relational interface implemented by SQLite (local) and Supabase (PostgREST)."""

    def select(self, table: str, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None,
               offset: int = 0, columns: Sequence[str] | None = None) -> list[dict[str, Any]]: ...

    def count(self, table: str, filters: Sequence[Filter] = ()) -> int: ...

    def insert(self, table: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def upsert(self, table: str, rows: Sequence[dict[str, Any]], on_conflict: Sequence[str]) -> list[dict[str, Any]]: ...

    def update(self, table: str, values: dict[str, Any], filters: Sequence[Filter]) -> list[dict[str, Any]]: ...

    def delete(self, table: str, filters: Sequence[Filter]) -> int: ...

    def transaction(self) -> AbstractContextManager[None]: ...


@contextmanager
def no_transaction() -> Iterator[None]:
    """Supabase/PostgREST cannot span multiple statements in a transaction from the client.

    Multi-step writes therefore tag rows with ``analysis_run_id`` and compensate on failure
    (see ``AnalysisPersistence``). Server-side atomicity would need an RPC function.
    """
    yield
