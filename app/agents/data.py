"""Read-only data facade for agents (no write methods exist on purpose)."""
from __future__ import annotations

from uuid import UUID

from app.agents.evidence import ChunkView
from app.database.store import F
from app.models.entities import (
    Clause,
    Contract,
    ContractVersion,
    Deadline,
    Document,
    Event,
    Obligation,
    PlaybookRule,
)
from app.models.enums import ContractStatus
from app.repositories import Repositories


class AgentData:
    def __init__(self, repos: Repositories) -> None:
        self._r = repos

    def contract(self, contract_id: UUID | str) -> Contract:
        return self._r.contracts.require(contract_id)

    def version(self, version_id: UUID | str) -> ContractVersion:
        return self._r.versions.require(version_id)

    def versions(self, contract_id: UUID | str) -> list[ContractVersion]:
        return self._r.versions.list([F.eq("contract_id", str(contract_id))], order_by=[("version_number", False)])

    def document_for_version(self, version_id: UUID | str) -> Document | None:
        return self._r.documents.first([F.eq("contract_version_id", str(version_id))])

    def chunks(self, version_id: UUID | str) -> list[ChunkView]:
        rows = self._r.chunks.list([F.eq("contract_version_id", str(version_id))], order_by=[("document_id", False), ("chunk_index", False)])
        return [ChunkView(r.id, r.document_id, r.contract_id, r.contract_version_id, r.chunk_index, r.text, r.page_number, r.section_reference,
                          r.section_title, r.clause_type.value, r.char_start, r.char_end) for r in rows]

    def events(self, contract_id: UUID | str) -> list[Event]:
        return self._r.events.list([F.eq("contract_id", str(contract_id))])

    def playbook_rules(self) -> list[PlaybookRule]:
        return self._r.playbooks.list([F.eq("enabled", True)])

    def obligations(self, version_id: UUID | str) -> list[Obligation]:
        return self._r.obligations.list([F.eq("contract_version_id", str(version_id))])

    def clauses(self, version_id: UUID | str) -> list[Clause]:
        return self._r.clauses.list([F.eq("contract_version_id", str(version_id))])

    def deadlines(self, version_id: UUID | str) -> list[Deadline]:
        return self._r.deadlines.list([F.eq("contract_version_id", str(version_id))])

    def contract_titles(self) -> dict[str, str]:
        return {str(c.id): c.title for c in self._r.contracts.list([F.is_null("deleted_at")], limit=2000)}

    def active_contracts(self) -> list[Contract]:
        return [c for c in self._r.contracts.list([F.is_null("deleted_at")], limit=5000) if c.status not in (ContractStatus.ARCHIVED, ContractStatus.TERMINATED)]
