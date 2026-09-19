"""Renewal Radar: expiration horizon, notice deadlines, auto-renewal and escalation status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from app.database.store import F
from app.models.entities import Contract, Deadline
from app.models.enums import ContractStatus, DeadlineCategory, DeadlineStatus, ValidationStatus
from app.repositories import Repositories
from app.tools.temporal import roll_term_end

HORIZON_BUCKETS = ((30, "0–30 days"), (60, "31–60 days"), (90, "61–90 days"), (180, "91–180 days"), (10_000, "180+ days"))


@dataclass
class RadarRow:
    contract: Contract
    initial_expiration: date | None
    current_term_end: date | None
    renewed_terms_assumed: int
    auto_renews: bool | None
    notice_days: int | None
    notice_deadline: Deadline | None
    days_to_notice: int | None
    days_to_end: int | None
    escalation: int
    unverified: bool

    @property
    def urgency(self) -> str:
        d = self.days_to_notice if self.days_to_notice is not None else self.days_to_end
        if d is None:
            return "unknown"
        return "overdue" if d < 0 else "critical" if d <= 14 else "high" if d <= 30 else "medium" if d <= 60 else "low"


class RenewalService:
    def __init__(self, repos: Repositories) -> None:
        self._r = repos

    def radar(self, today: date | None = None) -> list[RadarRow]:
        today = today or date.today()
        contracts = [c for c in self._r.contracts.list([F.is_null("deleted_at")]) if c.status not in (ContractStatus.ARCHIVED, ContractStatus.TERMINATED)]
        notice: dict[UUID, Deadline] = {}
        for d in self._r.live_deadlines([F.eq("category", DeadlineCategory.RENEWAL_NOTICE), F.eq("status", DeadlineStatus.OPEN)]):
            if d.validation_status is not ValidationStatus.REJECTED:
                notice.setdefault(d.contract_id, d)
        rows: list[RadarRow] = []
        for c in contracts:
            if not c.expiration_date and c.id not in notice:
                continue
            end, n = c.expiration_date, 0
            if end and c.auto_renews and c.renewal_term_months:
                end, n = roll_term_end(end, c.renewal_term_months, today)
            nd = notice.get(c.id)
            rows.append(RadarRow(
                c, c.expiration_date, end, n, c.auto_renews, c.renewal_notice_days, nd, (nd.due_date - today).days if nd and nd.due_date else None,
                (end - today).days if end else None, nd.escalation_level if nd else 0,
                bool(nd and nd.validation_status is not ValidationStatus.CONFIRMED) or n > 0,
            ))
        key = lambda r: (r.days_to_notice if r.days_to_notice is not None else (r.days_to_end if r.days_to_end is not None else 10**6))  # noqa: E731
        return sorted(rows, key=key)

    def horizon(self, today: date | None = None) -> dict[str, int]:
        """Number of contracts whose next decision date (notice, else term end) falls in each bucket."""
        today = today or date.today()
        counts = {label: 0 for _, label in HORIZON_BUCKETS}
        for r in self.radar(today):
            d = r.days_to_notice if r.days_to_notice is not None else r.days_to_end
            if d is None or d < 0:
                continue
            for limit, label in HORIZON_BUCKETS:
                if d <= limit:
                    counts[label] += 1
                    break
        return counts

    def calendar(self, year: int, month: int) -> dict[date, list[tuple[str, str, str]]]:
        """Deadlines in a month keyed by date: (label, contract title, category)."""
        first = date(year, month, 1)
        last = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1))
        titles = {c.id: c.title for c in self._r.contracts.list([F.is_null("deleted_at")])}
        out: dict[date, list[tuple[str, str, str]]] = {}
        for d in self._r.live_deadlines([F.eq("status", DeadlineStatus.OPEN), F.gte("due_date", first), F.lte("due_date", last)]):
            if d.contract_id in titles and d.validation_status is not ValidationStatus.REJECTED and d.due_date:
                out.setdefault(d.due_date, []).append((d.label, titles[d.contract_id], d.category.value))
        return out
