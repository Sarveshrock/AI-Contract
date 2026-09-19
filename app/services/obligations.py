"""Obligation ledger: filtering, assignment, status changes, notes, disputes, exceptions, completion evidence.

Completion is *always* a human act with a recorded note; an AI prediction can never complete an obligation.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.errors import ValidationFailure
from app.database.store import F
from app.models.entities import Clause, Deadline, Evidence, Obligation, ObligationDependency, ObligationNote, Profile
from app.models.enums import (
    DeadlineKind,
    DeadlineStatus,
    DependencyType,
    NoteKind,
    ObligationCategory,
    ObligationStatus,
    ReviewStatus,
    SubjectType,
    ValidationStatus,
)
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService


@dataclass
class ObligationRow:
    obligation: Obligation
    contract_title: str
    owner_name: str | None
    next_due: date | None
    deadline_kind: DeadlineKind | None
    deadline_validation: ValidationStatus | None
    overdue: bool
    has_explicit_deadline: bool
    evidence_count: int
    blocked_by: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        if self.obligation.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS) and self.overdue:
            return "overdue"
        return self.obligation.status.value


class ObligationService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService) -> None:
        self._p, self._r, self._audit = principal, repos, audit

    # ------------------------------------------------------------------ queries
    def list(self, *, contract_id: UUID | str | None = None, status: str | None = None, category: ObligationCategory | None = None,
             owner_id: UUID | str | None = None, party: str | None = None, search: str | None = None, view: str = "all",
             today: date | None = None) -> list[ObligationRow]:
        """``view``: all | pending | completed | overdue | unassigned | recurring | no_deadline | needs_review"""
        today = today or date.today()
        filters = []
        if contract_id:
            filters.append(F.eq("contract_id", str(contract_id)))
        if category:
            filters.append(F.eq("category", category))
        if owner_id:
            filters.append(F.eq("owner_id", str(owner_id)))
        obligations = [o for o in self._r.live_obligations(filters, order_by=[("created_at", False)]) if o.review_status is not ReviewStatus.REJECTED]
        contracts = {c.id: c for c in self._r.contracts.list([F.is_null("deleted_at")])}
        members = self._member_names()
        deadlines: dict[UUID, list[Deadline]] = {}
        for d in self._r.live_deadlines([F.not_null("obligation_id")]):
            deadlines.setdefault(d.obligation_id, []).append(d)
        ev_counts: dict[UUID, int] = {}
        for e in self._r.evidence.list([F.eq("subject_type", SubjectType.OBLIGATION)]):
            ev_counts[e.subject_id] = ev_counts.get(e.subject_id, 0) + 1
        deps = self._dependency_map()
        titles = {o.id: o.title for o in obligations}
        rows: list[ObligationRow] = []
        for o in obligations:
            if o.contract_id not in contracts:
                continue
            dls = [d for d in deadlines.get(o.id, []) if d.status is DeadlineStatus.OPEN]
            dated = sorted((d for d in dls if d.due_date), key=lambda d: d.due_date)
            nxt = dated[0] if dated else (dls[0] if dls else None)
            due = nxt.due_date if nxt else None
            row = ObligationRow(
                o, contracts[o.contract_id].title, members.get(o.owner_id) if o.owner_id else None, due, nxt.kind if nxt else None,
                nxt.validation_status if nxt else None, bool(due and due < today and o.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS)),
                any(d.kind in (DeadlineKind.EXPLICIT, DeadlineKind.DERIVED) and d.due_date for d in dls), ev_counts.get(o.id, 0),
                [titles.get(x, "?") for x in deps.get(o.id, [])],
            )
            rows.append(row)
        return [r for r in rows if self._match(r, status, party, search, view)]

    @staticmethod
    def _match(r: ObligationRow, status: str | None, party: str | None, search: str | None, view: str) -> bool:
        o = r.obligation
        if status and status != "all" and r.status_label != status:
            return False
        if party and party.lower() not in f"{o.responsible_party_name or ''} {o.beneficiary_name or ''}".lower():
            return False
        if search and search.lower() not in f"{o.title} {o.description} {r.contract_title}".lower():
            return False
        open_ = o.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS)
        return {
            "all": True, "pending": open_, "completed": o.status is ObligationStatus.COMPLETED, "overdue": r.overdue,
            "unassigned": o.owner_id is None and open_, "recurring": o.is_recurring, "no_deadline": not r.has_explicit_deadline and open_,
            "needs_review": o.review_status is ReviewStatus.NEEDS_REVIEW, "disputed": o.status is ObligationStatus.DISPUTED,
        }.get(view, True)

    def _member_names(self) -> dict[UUID, str]:
        ids = [m.user_id for m in self._r.members.list()]
        if not ids:
            return {}
        profiles = self._r.store.select("profiles", [F.in_("id", [str(i) for i in ids])])
        return {UUID(p["id"]): (p.get("full_name") or p["email"]) for p in profiles}

    def members(self) -> list[tuple[UUID, str]]:
        return sorted(self._member_names().items(), key=lambda t: t[1].lower())

    def _dependency_map(self) -> dict[UUID, list[UUID]]:
        out: dict[UUID, list[UUID]] = {}
        for d in self._r.dependencies.list():
            out.setdefault(d.obligation_id, []).append(d.depends_on_obligation_id)
        return out

    def notes(self, obligation_id: UUID | str) -> list[ObligationNote]:
        return self._r.notes.list([F.eq("obligation_id", str(obligation_id))], order_by=[("created_at", True)])

    def source(self, obligation_id: UUID | str) -> tuple[Clause | None, list[Evidence]]:
        o = self._r.obligations.require(obligation_id)
        ev = self._r.evidence.list([F.eq("subject_type", SubjectType.OBLIGATION), F.eq("subject_id", str(o.id))])
        return (self._r.clauses.get(o.clause_id) if o.clause_id else None), ev

    # ------------------------------------------------------------------ mutations
    def _mark_human(self, o: Obligation, **values: Any) -> Obligation:
        return self._r.obligations.update(o.id, human_modified=True, **values)

    def assign_owner(self, obligation_id: UUID | str, user_id: UUID | str | None) -> Obligation:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        o = self._r.obligations.require(obligation_id)
        if user_id is not None and UUID(str(user_id)) not in self._member_names():
            raise ValidationFailure("owner not a member", user_message="The selected owner is not a member of this workspace.")
        o = self._mark_human(o, owner_id=str(user_id) if user_id else None)
        self._audit.record("obligation.assign", "obligation", o.id, owner=str(user_id) if user_id else None)
        return o

    def set_status(self, obligation_id: UUID | str, status: ObligationStatus, note: str | None = None) -> Obligation:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if status is ObligationStatus.COMPLETED:
            raise ValidationFailure("use complete()", user_message="Completing an obligation requires completion evidence. Use 'Record completion'.")
        o = self._r.obligations.require(obligation_id)
        o = self._mark_human(o, status=status, completed_at=None, completed_by=None)
        if note:
            self.add_note(o.id, note)
        self._audit.record("obligation.status", "obligation", o.id, status=status.value)
        return o

    def complete(self, obligation_id: UUID | str, evidence_note: str, attachment_path: str | None = None) -> Obligation:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if len(evidence_note.strip()) < 5:
            raise ValidationFailure("completion evidence required", user_message="Describe the evidence that this obligation was completed (for example an invoice number, e-mail date or document reference).")
        o = self._r.obligations.require(obligation_id)
        self._r.notes.add(ObligationNote(org_id=self._p.org_id, obligation_id=o.id, kind=NoteKind.COMPLETION_EVIDENCE, body=evidence_note.strip(),
                                         attachment_path=attachment_path, author_id=self._p.user_id))
        now = datetime.now(timezone.utc)
        o = self._mark_human(o, status=ObligationStatus.COMPLETED, completed_at=now, completed_by=str(self._p.user_id))
        for d in self._r.deadlines.list([F.eq("obligation_id", str(o.id)), F.eq("status", DeadlineStatus.OPEN)]):
            nxt = next((date.fromisoformat(x) for x in (d.recurrence or {}).get("occurrences", []) if d.due_date and date.fromisoformat(x) > d.due_date), None)
            if d.kind is DeadlineKind.RECURRING and nxt:
                # a recurring obligation stays open: only this occurrence is done
                self._r.deadlines.update(d.id, due_date=nxt, calculation_trace=list(d.calculation_trace) + [f"Occurrence completed; next {nxt.isoformat()}"])
                self._r.obligations.update(o.id, status=ObligationStatus.PENDING, completed_at=None, completed_by=None)
            else:
                self._r.deadlines.update(d.id, status=DeadlineStatus.DONE)
        self._audit.record("obligation.complete", "obligation", o.id)
        return self._r.obligations.require(o.id)

    def add_note(self, obligation_id: UUID | str, body: str, kind: NoteKind = NoteKind.NOTE) -> ObligationNote:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if not body.strip():
            raise ValidationFailure("empty note", user_message="The note is empty.")
        n = self._r.notes.add(ObligationNote(org_id=self._p.org_id, obligation_id=UUID(str(obligation_id)), kind=kind, body=body.strip(), author_id=self._p.user_id))
        self._audit.record(f"obligation.{kind.value}", "obligation", obligation_id)
        return n

    def flag_dispute(self, obligation_id: UUID | str, reason: str) -> Obligation:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if not reason.strip():
            raise ValidationFailure("reason required", user_message="Please describe the dispute.")
        o = self._r.obligations.require(obligation_id)
        self._r.notes.add(ObligationNote(org_id=self._p.org_id, obligation_id=o.id, kind=NoteKind.DISPUTE, body=reason.strip(), author_id=self._p.user_id))
        o = self._mark_human(o, status=ObligationStatus.DISPUTED, dispute_flag=True)
        self._audit.record("obligation.dispute", "obligation", o.id)
        return o

    def create_exception(self, obligation_id: UUID | str, reason: str) -> Obligation:
        self._p.require(Permission.REVIEW_DECIDE)
        if not reason.strip():
            raise ValidationFailure("reason required", user_message="Please record the reason and approval for this exception.")
        o = self._r.obligations.require(obligation_id)
        self._r.notes.add(ObligationNote(org_id=self._p.org_id, obligation_id=o.id, kind=NoteKind.EXCEPTION, body=reason.strip(), author_id=self._p.user_id))
        o = self._mark_human(o, status=ObligationStatus.WAIVED)
        for d in self._r.deadlines.list([F.eq("obligation_id", str(o.id)), F.eq("status", DeadlineStatus.OPEN)]):
            self._r.deadlines.update(d.id, status=DeadlineStatus.WAIVED)
        self._audit.record("obligation.exception", "obligation", o.id)
        return o

    def mark_reviewed(self, obligation_id: UUID | str, approved: bool = True) -> Obligation:
        self._p.require(Permission.REVIEW_DECIDE)
        o = self._mark_human(self._r.obligations.require(obligation_id), review_status=ReviewStatus.APPROVED if approved else ReviewStatus.REJECTED)
        self._audit.record("obligation.review", "obligation", o.id, approved=approved)
        return o

    def add_dependency(self, obligation_id: UUID | str, depends_on: UUID | str, kind: DependencyType = DependencyType.FINISH_TO_START, notes: str | None = None) -> ObligationDependency:
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if str(obligation_id) == str(depends_on):
            raise ValidationFailure("self dependency", user_message="An obligation cannot depend on itself.")
        # reject cycles: depends_on must not (transitively) depend on obligation_id
        graph = self._dependency_map()
        stack, seen = [UUID(str(depends_on))], set()
        while stack:
            cur = stack.pop()
            if cur == UUID(str(obligation_id)):
                raise ValidationFailure("dependency cycle", user_message="That dependency would create a cycle.")
            if cur not in seen:
                seen.add(cur)
                stack.extend(graph.get(cur, []))
        dep = self._r.dependencies.add(ObligationDependency(org_id=self._p.org_id, obligation_id=UUID(str(obligation_id)),
                                                            depends_on_obligation_id=UUID(str(depends_on)), dependency_type=kind, notes=notes))
        self._audit.record("obligation.dependency", "obligation", obligation_id, depends_on=str(depends_on))
        return dep

    # ------------------------------------------------------------------ export
    def export_csv(self, path: str | Path, rows: list[ObligationRow]) -> int:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Contract", "Obligation", "Category", "Responsible", "Beneficiary", "Owner", "Status", "Next due", "Deadline validation", "Review", "Description"])
            for r in rows:
                o = r.obligation
                w.writerow([r.contract_title, o.title, o.category.value, o.responsible_party_name or "", o.beneficiary_name or "", r.owner_name or "", r.status_label,
                            r.next_due.isoformat() if r.next_due else "", r.deadline_validation.value if r.deadline_validation else "", o.review_status.value, o.description])
        self._audit.record("obligation.export", "obligation", None, rows=len(rows))
        return len(rows)


_ = Profile
