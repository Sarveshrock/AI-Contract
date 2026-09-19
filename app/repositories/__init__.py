"""Repositories: typed, organisation-scoped access to tables."""
from __future__ import annotations

from uuid import UUID

from app.database.store import TableStore
from app.database.store import F
from app.models import entities as e
from app.models.enums import PARTIAL_ROLES
from app.repositories.base import GlobalRepository, Repository


class Repositories:
    """Bundle of repositories bound to one organisation."""

    def __init__(self, store: TableStore, org_id: UUID | str) -> None:
        self.store = store
        self.org_id = str(org_id)
        r = lambda model: Repository(store, model, org_id)  # noqa: E731
        self.organizations = GlobalRepository(store, e.Organization)
        self.profiles = GlobalRepository(store, e.Profile)
        self.members = r(e.OrganizationMember)
        self.contracts = r(e.Contract)
        self.versions = r(e.ContractVersion)
        self.documents = r(e.Document)
        self.chunks = r(e.DocumentChunk)
        self.parties = r(e.Party)
        self.contract_parties = r(e.ContractParty)
        self.clauses = r(e.Clause)
        self.obligations = r(e.Obligation)
        self.dependencies = r(e.ObligationDependency)
        self.notes = r(e.ObligationNote)
        self.events = r(e.Event)
        self.deadlines = r(e.Deadline)
        self.evidence = r(e.Evidence)
        self.runs = r(e.AnalysisRun)
        self.findings = r(e.AnalysisFinding)
        self.reviews = r(e.ReviewCase)
        self.alerts = r(e.Alert)
        self.amendments = r(e.Amendment)
        self.playbooks = r(e.PlaybookRule)
        self.integrations = r(e.Integration)
        self.audit = r(e.AuditLog)

    def superseded_version_ids(self) -> set[UUID]:
        """Full versions replaced by a newer full version. Their obligations/deadlines are history, not live work."""
        return {v.id for v in self.versions.list([F.eq("is_current", False)]) if v.document_role not in PARTIAL_ROLES}

    def live_obligations(self, filters=(), **kw) -> list[e.Obligation]:
        sup = self.superseded_version_ids()
        return [o for o in self.obligations.list(filters, **kw) if o.contract_version_id not in sup]

    def live_deadlines(self, filters=(), **kw) -> list[e.Deadline]:
        sup = self.superseded_version_ids()
        return [d for d in self.deadlines.list(filters, **kw) if d.contract_version_id not in sup]


__all__ = ["Repositories", "Repository", "GlobalRepository"]
