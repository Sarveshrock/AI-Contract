"""Registry of all table models, in dependency (creation) order."""
from __future__ import annotations

from app.models import entities as e
from app.models.base import TableModel

ALL_MODELS: list[type[TableModel]] = [
    e.Organization,
    e.Profile,
    e.OrganizationMember,
    e.Contract,
    e.ContractVersion,
    e.Document,
    e.DocumentChunk,
    e.Party,
    e.ContractParty,
    e.AnalysisRun,
    e.Clause,
    e.Obligation,
    e.ObligationDependency,
    e.ObligationNote,
    e.Event,
    e.Deadline,
    e.Evidence,
    e.AnalysisFinding,
    e.ReviewCase,
    e.Alert,
    e.Amendment,
    e.PlaybookRule,
    e.Integration,
    e.AuditLog,
]

TABLES: dict[str, type[TableModel]] = {m.table_name: m for m in ALL_MODELS}


def creation_order() -> list[type[TableModel]]:
    """Topologically sort models by foreign-key dependencies (deferred and self references ignored)."""
    from app.models.base import column_specs

    remaining = {m.table_name: m for m in ALL_MODELS}
    ordered: list[type[TableModel]] = []
    done: set[str] = set()
    while remaining:
        progressed = False
        for name, model in list(remaining.items()):
            deps = {
                c.fk.table
                for c in column_specs(model)
                if c.fk and not c.fk.deferred and c.fk.table in TABLES and c.fk.table != name
            }
            if deps <= done:
                ordered.append(model)
                done.add(name)
                del remaining[name]
                progressed = True
        if not progressed:  # pragma: no cover - guards against modelling mistakes
            raise RuntimeError(f"Circular foreign keys among: {sorted(remaining)}")
    return ordered
