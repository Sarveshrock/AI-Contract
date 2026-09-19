"""SQL generation from the Pydantic table models (Postgres migrations + local SQLite DDL)."""
from __future__ import annotations

import json
from typing import Any

from app.models.base import ColumnSpec, Policy, TableModel, column_specs
from app.models.enums import Role
from app.models.registry import TABLES, creation_order

PG_TYPES = {"uuid": "uuid", "text": "text", "int": "integer", "float": "double precision", "bool": "boolean", "date": "date", "ts": "timestamptz", "json": "jsonb"}
SQLITE_TYPES = {"uuid": "TEXT", "text": "TEXT", "int": "INTEGER", "float": "REAL", "bool": "INTEGER", "date": "TEXT", "ts": "TEXT", "json": "TEXT"}


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pg_default(col: ColumnSpec) -> str | None:
    d = col.default
    if d is None:
        return None
    if d == "@uuid":
        return "gen_random_uuid()"
    if d == "@now":
        return "now()"
    if col.kind == "json":
        return f"{_q(json.dumps(d))}::jsonb"
    if col.kind == "bool":
        return "true" if d else "false"
    if col.kind in ("int", "float"):
        return str(d)
    return _q(str(d))


def _is_org_scoped(table: str) -> bool:
    model = TABLES.get(table)
    return bool(model and model.org_scoped)


# ---------------------------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------------------------
def pg_create_table(model: type[TableModel]) -> tuple[str, list[str]]:
    """Return (CREATE TABLE + indexes, deferred ALTER statements)."""
    t = model.table_name
    cols = column_specs(model)
    lines: list[str] = []
    constraints: list[str] = []
    deferred: list[str] = []
    index_cols: list[tuple[str, ...]] = []

    for c in cols:
        parts = [f"  {c.name} {PG_TYPES[c.kind]}"]
        if c.primary_key:
            parts.append("PRIMARY KEY")
        if not c.nullable:
            parts.append("NOT NULL")
        default = _pg_default(c)
        if default is not None:
            parts.append(f"DEFAULT {default}")
        single_fk = None
        if c.fk:
            target_scoped = _is_org_scoped(c.fk.table) and model.org_scoped
            if not target_scoped:
                on_delete = c.fk.on_delete
                ref_table = c.fk.table if "." in c.fk.table else f"public.{c.fk.table}"
                single_fk = f"REFERENCES {ref_table}({c.fk.column}) ON DELETE {on_delete}"
                if c.fk.deferred:
                    deferred.append(f"ALTER TABLE public.{t} ADD CONSTRAINT {t}_{c.name}_fkey FOREIGN KEY ({c.name}) {single_fk};")
                    single_fk = None
            else:
                fk_name = f"{t}_{c.name}_fkey"
                if c.fk.on_delete == "SET NULL":
                    action = f"ON DELETE SET NULL ({c.name})"
                else:
                    action = f"ON DELETE {c.fk.on_delete}"
                stmt = f"FOREIGN KEY ({c.name}, org_id) REFERENCES public.{c.fk.table} (id, org_id) {action}"
                if c.fk.deferred:
                    deferred.append(f"ALTER TABLE public.{t} ADD CONSTRAINT {fk_name} {stmt};")
                else:
                    constraints.append(f"  CONSTRAINT {fk_name} {stmt}")
            if not c.primary_key:
                index_cols.append((c.name,))
        if single_fk:
            parts.append(single_fk)
        lines.append(" ".join(parts))
        if c.enum_values:
            values = ", ".join(_q(v) for v in c.enum_values)
            constraints.append(f"  CONSTRAINT {t}_{c.name}_check CHECK ({c.name} IN ({values}))")

    if model.org_scoped:
        constraints.append(f"  CONSTRAINT {t}_id_org_key UNIQUE (id, org_id)")
    for i, uq in enumerate(model.table_unique):
        constraints.append(f"  CONSTRAINT {t}_{'_'.join(uq)}_key UNIQUE ({', '.join(uq)})")

    body = ",\n".join(lines + constraints)
    sql = [f"CREATE TABLE IF NOT EXISTS public.{t} (\n{body}\n);"]
    seen: set[tuple[str, ...]] = set()
    covered_by_unique = {uq[0] for uq in model.table_unique}
    for cols_tuple in list(model.table_indexes) + index_cols:
        if cols_tuple in seen or (len(cols_tuple) == 1 and cols_tuple[0] in covered_by_unique):
            continue
        seen.add(cols_tuple)
        sql.append(f"CREATE INDEX IF NOT EXISTS idx_{t}_{'_'.join(cols_tuple)} ON public.{t} ({', '.join(cols_tuple)});")
    sql.append(f"DROP TRIGGER IF EXISTS trg_{t}_updated_at ON public.{t};")
    sql.append(f"CREATE TRIGGER trg_{t}_updated_at BEFORE UPDATE ON public.{t} FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();")
    return "\n".join(sql), deferred


def _roles(roles: tuple[Role, ...]) -> str:
    return "ARRAY[" + ", ".join(_q(r.value) for r in roles) + "]"


def _org_check(policy_roles: tuple[Role, ...], col: str = "org_id") -> str:
    if set(policy_roles) == set(Role):
        return f"public.is_org_member({col})"
    return f"public.has_org_role({col}, {_roles(policy_roles)})"


def pg_policies(model: type[TableModel]) -> str:
    t = model.table_name
    p: Policy = model.table_policy
    out = [f"ALTER TABLE public.{t} ENABLE ROW LEVEL SECURITY;"]

    def policy(name: str, op: str, using: str | None = None, check: str | None = None) -> None:
        out.append(f"DROP POLICY IF EXISTS {t}_{name} ON public.{t};")
        clause = f"CREATE POLICY {t}_{name} ON public.{t} FOR {op} TO authenticated"
        if using:
            clause += f"\n  USING ({using})"
        if check:
            clause += f"\n  WITH CHECK ({check})"
        out.append(clause + ";")

    if p.kind == "organizations":
        policy("select", "SELECT", using="public.is_org_member(id)")
        policy("update", "UPDATE", using=_org_check(p.write, "id"), check=_org_check(p.write, "id"))
        policy("delete", "DELETE", using=_org_check(p.delete, "id"))
    elif p.kind == "profiles":
        policy("select", "SELECT", using="id = auth.uid() OR public.shares_org_with(id)")
        policy("insert", "INSERT", check="id = auth.uid()")
        policy("update", "UPDATE", using="id = auth.uid()", check="id = auth.uid()")
    elif p.kind == "audit":
        policy("select", "SELECT", using=_org_check(p.read))
        # No INSERT/UPDATE/DELETE policies: rows are written only by SECURITY DEFINER functions.
    else:
        policy("select", "SELECT", using=_org_check(p.read))
        policy("insert", "INSERT", check=_org_check(p.insert_roles))
        policy("update", "UPDATE", using=_org_check(p.write), check=_org_check(p.write))
        policy("delete", "DELETE", using=_org_check(p.delete))
    if p.audited:
        out.append(f"DROP TRIGGER IF EXISTS trg_{t}_audit ON public.{t};")
        out.append(f"CREATE TRIGGER trg_{t}_audit AFTER INSERT OR UPDATE OR DELETE ON public.{t} FOR EACH ROW EXECUTE FUNCTION public.audit_row_change();")
    return "\n".join(out)


HEADER = "-- GENERATED by scripts/generate_migrations.py from app/models. DO NOT EDIT BY HAND.\n"


def generate_tables_migration() -> str:
    chunks = [HEADER, "-- Tables, constraints, indexes and updated_at triggers.\n"]
    deferred_all: list[str] = []
    for model in creation_order():
        sql, deferred = pg_create_table(model)
        chunks.append(f"-- {model.table_name}\n{sql}\n")
        deferred_all.extend(deferred)
    if deferred_all:
        chunks.append("-- circular references added after all tables exist\n")
        for stmt in deferred_all:
            name = stmt.split("ADD CONSTRAINT ")[1].split(" ")[0]
            table = stmt.split("ALTER TABLE ")[1].split(" ")[0]
            chunks.append(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name};\n{stmt}\n")
    return "\n".join(chunks)


def generate_rls_migration() -> str:
    chunks = [HEADER, "-- Row Level Security policies and audit triggers.\n"]
    for model in creation_order():
        chunks.append(f"-- {model.table_name}\n{pg_policies(model)}\n")
    return "\n".join(chunks)


# ---------------------------------------------------------------------------------------------
# SQLite (local demo mode and tests)
# ---------------------------------------------------------------------------------------------
def sqlite_create_table(model: type[TableModel]) -> list[str]:
    t = model.table_name
    lines: list[str] = []
    fks: list[str] = []
    for c in column_specs(model):
        parts = [f'"{c.name}" {SQLITE_TYPES[c.kind]}']
        if c.primary_key:
            parts.append("PRIMARY KEY")
        if not c.nullable:
            parts.append("NOT NULL")
        if c.enum_values:
            values = ", ".join(_q(v) for v in c.enum_values)
            parts.append(f'CHECK ("{c.name}" IN ({values}))')
        lines.append(" ".join(parts))
        if c.fk and not c.fk.deferred and c.fk.table in TABLES:
            fks.append(f'FOREIGN KEY ("{c.name}") REFERENCES "{c.fk.table}"("{c.fk.column}") ON DELETE {c.fk.on_delete}')
    for uq in model.table_unique:
        lines.append("UNIQUE (" + ", ".join(f'"{c}"' for c in uq) + ")")
    stmts = [f'CREATE TABLE IF NOT EXISTS "{t}" (\n  ' + ",\n  ".join(lines + fks) + "\n)"]
    seen: set[tuple[str, ...]] = set()
    for cols in model.table_indexes:
        if cols not in seen:
            seen.add(cols)
            stmts.append(f'CREATE INDEX IF NOT EXISTS "idx_{t}_{"_".join(cols)}" ON "{t}" (' + ", ".join(f'"{c}"' for c in cols) + ")")
    return stmts


def sqlite_schema_statements() -> list[str]:
    out: list[str] = []
    for model in creation_order():
        out.extend(sqlite_create_table(model))
    return out


def json_default(value: Any) -> str:  # pragma: no cover - helper for debugging
    return json.dumps(value, default=str)
