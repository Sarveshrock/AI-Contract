"""SQLite implementation of :class:`TableStore` for local/demo mode and tests."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.errors import DatabaseError
from app.core.logging import get_logger
from app.database.sqlgen import sqlite_schema_statements
from app.database.store import Filter, OrderBy, normalize_value
from app.models.base import bool_columns, json_columns
from app.models.registry import TABLES

log = get_logger(__name__)
_IDENT_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _ident(name: str) -> str:
    if not name or not set(name.lower()) <= _IDENT_OK:
        raise DatabaseError(f"Illegal identifier: {name!r}")
    return f'"{name}"'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteTableStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._tx_depth = 0
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            if self._path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            for stmt in sqlite_schema_statements():
                self._conn.execute(stmt)

    # ------------------------------------------------------------------ helpers
    def _model(self, table: str):
        model = TABLES.get(table)
        if model is None:
            raise DatabaseError(f"Unknown table: {table}")
        return model

    def _encode(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        jcols, bcols = json_columns(self._model(table)), bool_columns(self._model(table))
        out: dict[str, Any] = {}
        for k, v in row.items():
            if k in jcols and v is not None:
                out[k] = json.dumps(v, default=str)
            elif k in bcols and v is not None:
                out[k] = 1 if v else 0
            elif isinstance(v, bool):
                out[k] = 1 if v else 0
            elif isinstance(v, (dict, list)):
                out[k] = json.dumps(v, default=str)
            else:
                out[k] = v
        return out

    def _decode(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        jcols, bcols = json_columns(self._model(table)), bool_columns(self._model(table))
        out: dict[str, Any] = {}
        for k in row.keys():
            v = row[k]
            if v is not None and k in jcols:
                try:
                    v = json.loads(v)
                except (TypeError, ValueError):
                    pass
            elif v is not None and k in bcols:
                v = bool(v)
            out[k] = v
        return out

    def _where(self, table: str, filters: Sequence[Filter]) -> tuple[str, list[Any]]:
        bcols = bool_columns(self._model(table))
        clauses: list[str] = []
        params: list[Any] = []
        for f in filters:
            col = _ident(f.column)
            v = normalize_value(f.value)
            if f.column in bcols and isinstance(v, bool):
                v = int(v)
            if f.op == "eq":
                if v is None:
                    clauses.append(f"{col} IS NULL")
                else:
                    clauses.append(f"{col} = ?")
                    params.append(v)
            elif f.op == "neq":
                clauses.append(f"({col} IS NULL OR {col} != ?)" if v is not None else f"{col} IS NOT NULL")
                if v is not None:
                    params.append(v)
            elif f.op in ("gt", "gte", "lt", "lte"):
                sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.op]
                clauses.append(f"{col} {sym} ?")
                params.append(v)
            elif f.op == "in":
                vals = list(v or [])
                if not vals:
                    clauses.append("1 = 0")
                else:
                    clauses.append(f"{col} IN ({','.join('?' * len(vals))})")
                    params.extend(vals)
            elif f.op == "is_null":
                clauses.append(f"{col} IS NULL")
            elif f.op == "not_null":
                clauses.append(f"{col} IS NOT NULL")
            elif f.op == "ilike":
                clauses.append(f"{col} LIKE ? ESCAPE '\\'")
                params.append(v)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _run(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(f"Integrity error: {exc}", user_message="The change violates a data integrity rule (duplicate or missing reference).") from exc
        except sqlite3.Error as exc:
            raise DatabaseError(f"SQLite error: {exc}", user_message="A local database error occurred.") from exc

    # ------------------------------------------------------------------ protocol
    def select(self, table: str, filters: Sequence[Filter] = (), *, order_by: OrderBy = (), limit: int | None = None,
               offset: int = 0, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
        cols = ", ".join(_ident(c) for c in columns) if columns else "*"
        where, params = self._where(table, filters)
        sql = f"SELECT {cols} FROM {_ident(table)}{where}"
        if order_by:
            sql += " ORDER BY " + ", ".join(f"{_ident(c)} {'DESC' if desc else 'ASC'}" for c, desc in order_by)
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        elif offset:
            sql += f" LIMIT -1 OFFSET {int(offset)}"
        with self._lock:
            return [self._decode(table, r) for r in self._run(sql, params).fetchall()]

    def count(self, table: str, filters: Sequence[Filter] = ()) -> int:
        where, params = self._where(table, filters)
        with self._lock:
            return int(self._run(f"SELECT COUNT(*) FROM {_ident(table)}{where}", params).fetchone()[0])

    def insert(self, table: str, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._write(table, rows, on_conflict=None)

    def upsert(self, table: str, rows: Sequence[dict[str, Any]], on_conflict: Sequence[str]) -> list[dict[str, Any]]:
        return self._write(table, rows, on_conflict=list(on_conflict))

    def _write(self, table: str, rows: Sequence[dict[str, Any]], on_conflict: list[str] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not rows:
            return out
        with self._lock, self.transaction():
            for row in rows:
                enc = self._encode(table, row)
                cols = list(enc.keys())
                sql = f"INSERT INTO {_ident(table)} ({', '.join(_ident(c) for c in cols)}) VALUES ({', '.join('?' * len(cols))})"
                if on_conflict:
                    updates = [c for c in cols if c not in on_conflict and c not in ("id", "created_at")]
                    if updates:
                        sql += f" ON CONFLICT ({', '.join(_ident(c) for c in on_conflict)}) DO UPDATE SET " + ", ".join(f"{_ident(c)} = excluded.{_ident(c)}" for c in updates)
                    else:
                        sql += f" ON CONFLICT ({', '.join(_ident(c) for c in on_conflict)}) DO NOTHING"
                self._run(sql, [enc[c] for c in cols])
                if on_conflict:
                    where = " AND ".join(f"{_ident(c)} = ?" for c in on_conflict)
                    r = self._run(f"SELECT * FROM {_ident(table)} WHERE {where}", [enc[c] for c in on_conflict]).fetchone()
                else:
                    r = self._run(f"SELECT * FROM {_ident(table)} WHERE id = ?", [enc["id"]]).fetchone()
                if r is not None:
                    out.append(self._decode(table, r))
        return out

    def update(self, table: str, values: dict[str, Any], filters: Sequence[Filter]) -> list[dict[str, Any]]:
        if not values:
            return []
        vals = dict(values)
        vals.setdefault("updated_at", _now())
        enc = self._encode(table, vals)
        where, wparams = self._where(table, filters)
        with self._lock, self.transaction():
            ids = [r["id"] for r in self._run(f"SELECT id FROM {_ident(table)}{where}", wparams).fetchall()]
            if not ids:
                return []
            sets = ", ".join(f"{_ident(c)} = ?" for c in enc)
            self._run(f"UPDATE {_ident(table)} SET {sets} WHERE id IN ({','.join('?' * len(ids))})", [*enc.values(), *ids])
            rows = self._run(f"SELECT * FROM {_ident(table)} WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
            return [self._decode(table, r) for r in rows]

    def delete(self, table: str, filters: Sequence[Filter]) -> int:
        where, params = self._where(table, filters)
        with self._lock:
            return self._run(f"DELETE FROM {_ident(table)}{where}", params).rowcount

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            outermost = self._tx_depth == 0
            if outermost:
                self._conn.execute("BEGIN")
            self._tx_depth += 1
            try:
                yield
            except BaseException:
                self._tx_depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
