"""Alert generation (idempotent), escalation and lifecycle."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import Alert
from app.models.enums import (
    AlertStatus,
    AlertType,
    ContractStatus,
    DeadlineCategory,
    DeadlineKind,
    DeadlineStatus,
    ReviewCaseStatus,
    Severity,
    ValidationStatus,
)
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService

log = get_logger(__name__)
DEFAULT_OFFSETS = (90, 60, 30, 14, 7, 1)


def severity_for_offset(offset: int) -> Severity:
    return Severity.CRITICAL if offset <= 1 else Severity.HIGH if offset <= 7 else Severity.MEDIUM if offset <= 30 else Severity.LOW


class AlertService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService) -> None:
        self._p, self._r, self._audit = principal, repos, audit

    def offsets(self) -> tuple[int, ...]:
        org = self._r.organizations.get(self._p.org_id)
        raw = (org.settings if org else {}).get("alert_offsets_days")
        try:
            vals = sorted({int(x) for x in raw}, reverse=True) if raw else []
        except (TypeError, ValueError):
            vals = []
        return tuple(vals) or DEFAULT_OFFSETS

    def scan(self, today: date | None = None) -> list[Alert]:
        """Create alerts for approaching/overdue deadlines, expiring contracts and aging reviews. Safe to call repeatedly."""
        today = today or date.today()
        offsets = self.offsets()
        existing = {a.dedupe_key for a in self._r.alerts.list()}
        new: list[Alert] = []

        def emit(key: str, **kw) -> None:
            if key in existing:
                return
            existing.add(key)
            new.append(Alert(org_id=self._p.org_id, dedupe_key=key, **kw))

        contracts = {c.id: c for c in self._r.contracts.list([F.is_null("deleted_at")])}
        for d in self._r.live_deadlines([F.eq("status", DeadlineStatus.OPEN)]):
            c = contracts.get(d.contract_id)
            if c is None or d.validation_status is ValidationStatus.REJECTED:
                continue
            cname = c.title
            if d.due_date is None:
                if d.validation_status is ValidationStatus.UNRESOLVED and d.kind is not DeadlineKind.EVENT_TRIGGERED:
                    emit(f"{d.id}:unresolved", contract_id=d.contract_id, obligation_id=d.obligation_id, deadline_id=d.id, alert_type=AlertType.UNRESOLVED_DEADLINE,
                         severity=Severity.LOW, title=f"Deadline could not be calculated: {d.label}", message=f"{cname}: required information is missing ({', '.join(map(str, d.missing_anchors)) or 'unknown'}).")
                continue
            days = (d.due_date - today).days
            if d.category is DeadlineCategory.EXPIRATION and days < 0:
                if c.auto_renews and c.renewal_term_months:
                    continue  # the initial term ended but the contract auto-renews: the renewal notice deadline is what matters
                emit(f"{d.id}:term-ended", contract_id=d.contract_id, deadline_id=d.id, alert_type=AlertType.CONTRACT_EXPIRING, severity=Severity.HIGH,
                     title=f"Contract term ended {d.due_date.isoformat()}", due_on=d.due_date, escalation_level=len(offsets),
                     message=f"{cname}: the stated term has ended with no renewal recorded. Confirm whether it was renewed, extended or terminated.")
                continue
            unverified = " (unverified AI-derived date)" if d.validation_status is not ValidationStatus.CONFIRMED else ""
            if days < 0:
                emit(f"{d.id}:overdue", contract_id=d.contract_id, obligation_id=d.obligation_id, deadline_id=d.id, alert_type=AlertType.DEADLINE_OVERDUE,
                     severity=Severity.CRITICAL if -days > 14 else Severity.HIGH, title=f"Overdue: {d.label}", due_on=d.due_date, escalation_level=len(offsets),
                     message=f"{cname}: due {d.due_date.isoformat()} ({-days} days ago){unverified}.")
                self._r.deadlines.update(d.id, escalation_level=len(offsets))
                continue
            hit = [o for o in offsets if days <= o]
            if not hit:
                continue
            o = min(hit)
            level = offsets.index(o) + 1
            atype = AlertType.RENEWAL_WINDOW if d.category is DeadlineCategory.RENEWAL_NOTICE else AlertType.DEADLINE_APPROACHING
            emit(f"{d.id}:{o}:{d.due_date.isoformat()}", contract_id=d.contract_id, obligation_id=d.obligation_id, deadline_id=d.id, alert_type=atype,
                 severity=severity_for_offset(o), title=f"{d.label} in {days} day{'s' if days != 1 else ''}", due_on=d.due_date, escalation_level=level,
                 message=f"{cname}: {d.label} is due {d.due_date.isoformat()}{unverified}.")
            if level > d.escalation_level:
                self._r.deadlines.update(d.id, escalation_level=level)
        for c in contracts.values():
            if c.expiration_date and c.status in (ContractStatus.ACTIVE, ContractStatus.EXPIRING, ContractStatus.IN_REVIEW):
                days = (c.expiration_date - today).days
                hit = [o for o in offsets if 0 <= days <= o]
                if hit:
                    o = min(hit)
                    emit(f"{c.id}:expiring:{o}", contract_id=c.id, alert_type=AlertType.CONTRACT_EXPIRING, severity=severity_for_offset(o), due_on=c.expiration_date,
                         title=f"Contract expires in {days} days", message=f"{c.title} expires {c.expiration_date.isoformat()}.", escalation_level=offsets.index(o) + 1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        for rc in self._r.reviews.list([F.eq("status", ReviewCaseStatus.OPEN)]):
            created = rc.created_at if rc.created_at.tzinfo else rc.created_at.replace(tzinfo=timezone.utc)
            if created < cutoff:
                emit(f"{rc.id}:aging", contract_id=rc.contract_id, alert_type=AlertType.REVIEW_AGING, severity=Severity.LOW, title=f"Review waiting over 7 days: {rc.title}",
                     message="An AI finding is still waiting for human review.")
        if new:
            self._r.alerts.add_many(new)
            log.info("created %d alert(s)", len(new))
        self._resolve_finished()
        return new

    def _resolve_finished(self) -> None:
        """Alerts for deadlines that are done/waived no longer need attention."""
        active = self._r.alerts.list([F.in_("status", [AlertStatus.UNREAD, AlertStatus.ACKNOWLEDGED]), F.not_null("deadline_id")])
        if not active:
            return
        open_ids = {str(d.id) for d in self._r.deadlines.list([F.eq("status", DeadlineStatus.OPEN)])}
        for a in active:
            if str(a.deadline_id) not in open_ids:
                self._r.alerts.update(a.id, status=AlertStatus.RESOLVED)

    # -- lifecycle
    def list(self, *, statuses=(AlertStatus.UNREAD, AlertStatus.ACKNOWLEDGED), limit: int = 200) -> list[Alert]:
        return self._r.alerts.list([F.in_("status", list(statuses))], order_by=[("created_at", True)], limit=limit)

    def unread_count(self) -> int:
        return self._r.alerts.count([F.eq("status", AlertStatus.UNREAD)])

    def acknowledge(self, alert_id: UUID | str) -> Alert:
        self._p.require(Permission.ALERTS_MANAGE)
        a = self._r.alerts.update(alert_id, status=AlertStatus.ACKNOWLEDGED, acknowledged_by=self._p.user_id, acknowledged_at=datetime.now(timezone.utc))
        self._audit.record("alert.acknowledge", "alert", a.id)
        return a

    def dismiss(self, alert_id: UUID | str) -> Alert:
        self._p.require(Permission.ALERTS_MANAGE)
        a = self._r.alerts.update(alert_id, status=AlertStatus.DISMISSED, acknowledged_by=self._p.user_id, acknowledged_at=datetime.now(timezone.utc))
        self._audit.record("alert.dismiss", "alert", a.id)
        return a
