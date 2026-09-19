"""Deadline engine service: recompute from stored rules, record events, human confirmation/override."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.core.errors import ValidationFailure
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import Deadline, Event
from app.models.enums import DeadlineCategory, DeadlineKind, DeadlineStatus, EventType, ValidationStatus
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService
from app.tools.temporal import (
    ANCHOR_CURRENT_TERM_END,
    ANCHOR_EFFECTIVE,
    ANCHOR_EXPIRATION,
    ANCHOR_TERM_END,
    ComputationStatus,
    TemporalRule,
    compute_deadline,
    roll_term_end,
)

log = get_logger(__name__)


@dataclass
class DeadlineRow:
    deadline: Deadline
    contract_title: str
    obligation_title: str | None
    days_until: int | None
    overdue: bool


class DeadlineService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService) -> None:
        self._p, self._r, self._audit = principal, repos, audit

    # ------------------------------------------------------------------ queries
    def list(self, *, contract_id: UUID | str | None = None, status: DeadlineStatus | None = DeadlineStatus.OPEN, today: date | None = None) -> list[DeadlineRow]:
        today = today or date.today()
        filters = [F.eq("contract_id", str(contract_id))] if contract_id else []
        if status:
            filters.append(F.eq("status", status))
        titles = {c.id: c.title for c in self._r.contracts.list([F.is_null("deleted_at")])}
        obs = {o.id: o.title for o in self._r.obligations.list()}
        rows = []
        for d in self._r.live_deadlines(filters, order_by=[("due_date", False)]):
            if d.contract_id not in titles or d.validation_status is ValidationStatus.REJECTED:
                continue
            du = (d.due_date - today).days if d.due_date else None
            rows.append(DeadlineRow(d, titles[d.contract_id], obs.get(d.obligation_id) if d.obligation_id else None, du, du is not None and du < 0 and d.status is DeadlineStatus.OPEN))
        return rows

    # ------------------------------------------------------------------ anchors
    def anchors_for(self, contract_id: UUID | str, today: date | None = None) -> dict[str, date | None]:
        today = today or date.today()
        c = self._r.contracts.require(contract_id)
        anchors: dict[str, date | None] = {ANCHOR_EFFECTIVE: c.effective_date, ANCHOR_EXPIRATION: c.expiration_date, ANCHOR_TERM_END: c.expiration_date,
                                           ANCHOR_CURRENT_TERM_END: c.expiration_date}
        if c.expiration_date and c.auto_renews and c.renewal_term_months:
            anchors[ANCHOR_CURRENT_TERM_END] = roll_term_end(c.expiration_date, c.renewal_term_months, today)[0]
        for e in self._r.events.list([F.eq("contract_id", str(contract_id))]):
            if e.occurred_on:
                anchors[e.event_type.value] = e.occurred_on
                anchors[e.name.strip().lower().replace(" ", "_")] = e.occurred_on
        return anchors

    # ------------------------------------------------------------------ recompute
    def recompute_contract(self, contract_id: UUID | str, *, today: date | None = None) -> int:
        """Recompute every non-human-confirmed deadline from its stored rule and the current anchors."""
        anchors = self.anchors_for(contract_id, today)
        org = self._r.organizations.get(self._p.org_id)
        holidays = frozenset(date.fromisoformat(h) for h in (org.settings if org else {}).get("holidays", []) if h)
        changed = 0
        for d in self._r.deadlines.list([F.eq("contract_id", str(contract_id))]):
            if d.human_modified or d.validation_status in (ValidationStatus.CONFIRMED, ValidationStatus.REJECTED) or not d.calculation_rule:
                continue
            try:
                rule = TemporalRule.from_dict(d.calculation_rule)
            except (KeyError, ValueError):
                continue
            comp = compute_deadline(rule, anchors, holidays=holidays, today=today)
            new_kind = comp.kind
            values: dict[str, Any] = dict(
                due_date=comp.due_date, due_at=comp.due_at, anchor_date=comp.anchor_date, anchor_event=comp.anchor_name, calculation_trace=list(comp.trace),
                assumptions=list(comp.assumptions) + [f"Note: {n}" for n in comp.notes], missing_anchors=list(comp.missing_anchors), kind=new_kind,
                validation_status=comp.validation_status,
            )
            if (d.due_date, d.calculation_trace, d.kind) != (comp.due_date, list(comp.trace), new_kind):
                self._r.deadlines.update(d.id, **values)
                changed += 1
        return changed

    def record_event(self, contract_id: UUID | str, event_type: EventType, name: str, occurred_on: date, description: str | None = None) -> Event:
        """Record a real-world event (e.g. invoice received). Event-triggered deadlines resolve from it."""
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if occurred_on > date.today().replace(year=date.today().year + 5):
            raise ValidationFailure("implausible event date", user_message="That event date is implausibly far in the future.")
        ev = self._r.events.add(Event(org_id=self._p.org_id, contract_id=UUID(str(contract_id)), event_type=event_type, name=name.strip() or event_type.value,
                                      occurred_on=occurred_on, description=description, recorded_by=self._p.user_id))
        self._audit.record("event.record", "event", ev.id, event_type=event_type.value, occurred_on=occurred_on.isoformat())
        n = self.recompute_contract(contract_id)
        log.info("event recorded; %d deadline(s) recomputed", n)
        return ev

    # ------------------------------------------------------------------ human decisions
    def confirm(self, deadline_id: UUID | str, note: str | None = None) -> Deadline:
        self._p.require(Permission.REVIEW_DECIDE)
        d = self._r.deadlines.require(deadline_id)
        if d.due_date is None:
            raise ValidationFailure("cannot confirm unresolved deadline", user_message="This deadline has no date yet, so it cannot be confirmed.")
        d = self._r.deadlines.update(d.id, validation_status=ValidationStatus.CONFIRMED, human_modified=True)
        self._audit.record("deadline.confirm", "deadline", d.id, due=d.due_date.isoformat() if d.due_date else None, note=note)
        return d

    def reject(self, deadline_id: UUID | str, reason: str) -> Deadline:
        self._p.require(Permission.REVIEW_DECIDE)
        d = self._r.deadlines.update(deadline_id, validation_status=ValidationStatus.REJECTED, human_modified=True)
        self._audit.record("deadline.reject", "deadline", d.id, reason=reason)
        return d

    def override_date(self, deadline_id: UUID | str, new_due: date, reason: str) -> Deadline:
        self._p.require(Permission.REVIEW_DECIDE)
        if not reason.strip():
            raise ValidationFailure("reason required", user_message="Please state why the date is being changed.")
        d = self._r.deadlines.require(deadline_id)
        trace = list(d.calculation_trace) + [f"Manually set to {new_due.isoformat()} by {self._p.email}: {reason.strip()}"]
        d = self._r.deadlines.update(d.id, due_date=new_due, calculation_trace=trace, validation_status=ValidationStatus.CONFIRMED, human_modified=True,
                                     kind=DeadlineKind.EXPLICIT if d.kind is DeadlineKind.UNRESOLVED else d.kind)
        self._audit.record("deadline.override", "deadline", d.id, new_due=new_due.isoformat(), reason=reason)
        return d

    def mark_done(self, deadline_id: UUID | str, note: str) -> Deadline:
        """Close a deadline. Requires a human note; recurring deadlines roll to their next occurrence."""
        self._p.require(Permission.OBLIGATIONS_WRITE)
        if not note.strip():
            raise ValidationFailure("completion note required", user_message="Record how this was completed before closing the deadline.")
        d = self._r.deadlines.require(deadline_id)
        nxt = self._next_occurrence(d)
        if nxt is not None:
            d = self._r.deadlines.update(d.id, due_date=nxt, calculation_trace=list(d.calculation_trace) + [f"Occurrence completed by {self._p.email}: {note.strip()}. Next: {nxt.isoformat()}"])
        else:
            d = self._r.deadlines.update(d.id, status=DeadlineStatus.DONE)
        self._audit.record("deadline.done", "deadline", d.id, note=note)
        return d

    @staticmethod
    def _next_occurrence(d: Deadline) -> date | None:
        occ = [date.fromisoformat(x) for x in (d.recurrence or {}).get("occurrences", [])]
        return next((o for o in occ if d.due_date and o > d.due_date), None) if d.kind is DeadlineKind.RECURRING else None

    # ------------------------------------------------------------------ amendments applied by humans
    def apply_term_override(self, contract_id: UUID | str, field: str, value: int) -> int:
        """After a human approves an amendment, patch the affected deadline rules and recompute."""
        patched = 0
        for d in self._r.deadlines.list([F.eq("contract_id", str(contract_id))]):
            rule = dict(d.calculation_rule or {})
            hit = (field == "renewal_notice_days" and d.category is DeadlineCategory.RENEWAL_NOTICE) or \
                  (field == "payment_days" and d.category is DeadlineCategory.PAYMENT and rule.get("anchor") == "invoice_received")
            if hit and rule.get("offset") is not None:
                rule["offset"] = int(value)
                self._r.deadlines.update(d.id, calculation_rule=rule, human_modified=False, validation_status=ValidationStatus.PENDING_REVIEW)
                patched += 1
        self.recompute_contract(contract_id)
        return patched

    def confirmed_only(self, deadline_ids: list[UUID | str]) -> list[Deadline]:
        rows = [self._r.deadlines.require(i) for i in deadline_ids]
        return [d for d in rows if d.validation_status is ValidationStatus.CONFIRMED and d.due_date]


_ = (ComputationStatus, datetime, timezone)
