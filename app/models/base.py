"""Base class for table-backed models.

The Pydantic models are the *single source of truth* for the schema: the Postgres migrations and the
local SQLite DDL are generated from them (``scripts/generate_migrations.py``), and a test fails if the
committed migrations drift from the models.
"""
from __future__ import annotations

import types
import typing
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Self, Union, get_args, get_origin
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import PydanticUndefined

from app.models.enums import Role


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FK:
    """Foreign-key annotation used inside ``Annotated[...]``."""

    table: str
    column: str = "id"
    on_delete: str = "CASCADE"  # CASCADE | SET NULL | RESTRICT
    deferred: bool = False  # add via ALTER TABLE (circular references)


OPERATOR_ROLES = (Role.OWNER, Role.ADMIN, Role.LEGAL_REVIEWER, Role.CONTRACT_MANAGER, Role.MEMBER)
MANAGER_ROLES = (Role.OWNER, Role.ADMIN, Role.LEGAL_REVIEWER, Role.CONTRACT_MANAGER)
ADMIN_ROLES = (Role.OWNER, Role.ADMIN)
ALL_ROLES = tuple(Role)


@dataclass(frozen=True)
class Policy:
    """Row Level Security configuration for a table."""

    kind: str = "org"  # org | organizations | profiles | audit
    read: tuple[Role, ...] = ALL_ROLES
    write: tuple[Role, ...] = OPERATOR_ROLES
    delete: tuple[Role, ...] = MANAGER_ROLES
    insert: tuple[Role, ...] | None = None  # defaults to ``write``
    audited: bool = False  # row-level audit trigger

    @property
    def insert_roles(self) -> tuple[Role, ...]:
        return self.insert if self.insert is not None else self.write


class TableModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    table_name: ClassVar[str] = ""
    table_unique: ClassVar[tuple[tuple[str, ...], ...]] = ()
    table_indexes: ClassVar[tuple[tuple[str, ...], ...]] = ()
    table_policy: ClassVar[Policy] = Policy()
    org_scoped: ClassVar[bool] = True

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def to_row(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Self:
        return cls.model_validate(row)


class OrgScopedModel(TableModel):
    org_id: typing.Annotated[UUID, FK("organizations")]


# ---------------------------------------------------------------------------------------------
# Column introspection (used by SQL generators and the SQLite store)
# ---------------------------------------------------------------------------------------------
@dataclass
class ColumnSpec:
    name: str
    kind: str  # uuid | text | int | float | bool | date | ts | json
    nullable: bool
    default: Any = None  # python literal, or the sentinels "@uuid" / "@now"
    fk: FK | None = None
    enum_values: tuple[str, ...] = ()
    primary_key: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        nullable = len(args) != len(get_args(tp))
        if len(args) == 1:
            return args[0], nullable
        return Any, nullable
    return tp, False


def _kind_of(tp: Any) -> tuple[str, tuple[str, ...]]:
    origin = get_origin(tp)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return "text", tuple(str(m.value) for m in tp)
    if tp is UUID:
        return "uuid", ()
    if tp is bool:
        return "bool", ()
    if tp is int:
        return "int", ()
    if tp is float:
        return "float", ()
    if tp is str:
        return "text", ()
    if tp is datetime:
        return "ts", ()
    if tp is date:
        return "date", ()
    if tp in (dict, list, Any) or origin in (dict, list):
        return "json", ()
    raise TypeError(f"Unsupported column type: {tp!r}")


def column_specs(model: type[TableModel]) -> list[ColumnSpec]:
    specs: list[ColumnSpec] = []
    for name, info in model.model_fields.items():
        tp, nullable = _unwrap_optional(info.annotation)
        kind, enums = _kind_of(tp)
        fk = next((m for m in info.metadata if isinstance(m, FK)), None)
        default: Any = None
        if info.default is not PydanticUndefined and info.default is not None:
            default = info.default.value if isinstance(info.default, Enum) else info.default
        elif info.default_factory is not None:
            if info.default_factory is uuid4:
                default = "@uuid"
            elif info.default_factory is utcnow:
                default = "@now"
            elif info.default_factory in (dict, list):
                default = info.default_factory()
        specs.append(ColumnSpec(name=name, kind=kind, nullable=nullable, default=default, fk=fk, enum_values=enums, primary_key=(name == "id")))
    return specs


def json_columns(model: type[TableModel]) -> set[str]:
    return {c.name for c in column_specs(model) if c.kind == "json"}


def bool_columns(model: type[TableModel]) -> set[str]:
    return {c.name for c in column_specs(model) if c.kind == "bool"}
