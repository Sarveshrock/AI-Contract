"""Deterministic deadline engine.

LLMs *extract* temporal terms (text, anchor names, offsets); **this module computes dates**. Every
result carries a calculation trace, the assumptions that materially affect the answer, missing anchor
names, and a status. Missing anchor dates are never invented: the result is UNRESOLVED instead.

Conventions (all surfaced as assumptions when they apply):
* Calendar-day offsets exclude the anchor day (anchor + N days).
* "N months" uses calendar-month arithmetic, clamping to the last day of shorter months.
* A term that "continues for N months from D" ends on the day *before* the N-month anniversary.
* Business days exclude Saturdays/Sundays; public holidays only if a holiday calendar is supplied.
* No weekend roll-forward unless the rule requests one.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.enums import DeadlineKind, ValidationStatus


class Unit(StrEnum):
    DAY = "day"
    BUSINESS_DAY = "business_day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    HOUR = "hour"


class Direction(StrEnum):
    AFTER = "after"
    BEFORE = "before"


class Frequency(StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


class Roll(StrEnum):
    NONE = "none"
    FOLLOWING = "following"
    PRECEDING = "preceding"


class ComputationStatus(StrEnum):
    COMPUTED = "computed"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


# Well-known anchor names produced by the extraction agents.
ANCHOR_EFFECTIVE = "effective_date"
ANCHOR_EXPIRATION = "expiration_date"
ANCHOR_TERM_END = "term_end"
ANCHOR_AMENDMENT = "amendment_effective_date"


# ---------------------------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------------------------
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_ORD = r"(?:st|nd|rd|th)"
_MONTH_RX = "|".join(sorted(_MONTHS, key=len, reverse=True))
_P_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_P_MDY_TEXT = re.compile(rf"\b({_MONTH_RX})\.?\s+(\d{{1,2}})(?:{_ORD})?,?\s+(\d{{4}})\b", re.IGNORECASE)
_P_DMY_TEXT = re.compile(rf"\b(\d{{1,2}})(?:{_ORD})?\s+(?:day\s+of\s+)?({_MONTH_RX})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_P_NUMERIC = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")


@dataclass(frozen=True)
class ParsedDate:
    value: date | None
    ambiguous: bool = False
    candidates: tuple[date, ...] = ()
    note: str = ""


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date_text(text: str, *, day_first: bool | None = None) -> ParsedDate:
    """Parse a date phrase. Numeric dates like 03/04/2025 are *ambiguous* unless ``day_first`` is given
    or one component exceeds 12. Ambiguity is reported, never silently resolved."""
    t = text.strip()
    m = _P_ISO.search(t)
    if m:
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        return ParsedDate(d, note="ISO date") if d else ParsedDate(None, note="invalid calendar date")
    m = _P_MDY_TEXT.search(t)
    if m:
        d = _safe_date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
        return ParsedDate(d) if d else ParsedDate(None, note="invalid calendar date")
    m = _P_DMY_TEXT.search(t)
    if m:
        d = _safe_date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
        return ParsedDate(d) if d else ParsedDate(None, note="invalid calendar date")
    m = _P_NUMERIC.search(t)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        mdy, dmy = _safe_date(y, a, b), _safe_date(y, b, a)
        if a > 12 and dmy:
            return ParsedDate(dmy, note="day-first (first number > 12)")
        if b > 12 and mdy:
            return ParsedDate(mdy, note="month-first (second number > 12)")
        if a == b and mdy:
            return ParsedDate(mdy)
        if day_first is True and dmy:
            return ParsedDate(dmy, note="day-first per locale hint")
        if day_first is False and mdy:
            return ParsedDate(mdy, note="month-first per locale hint")
        cands = tuple(d for d in (mdy, dmy) if d)
        return ParsedDate(None, ambiguous=len(cands) > 1, candidates=cands, note="ambiguous numeric date (MM/DD vs DD/MM)")
    return ParsedDate(None, note="no date found")


# ---------------------------------------------------------------------------------------------
# Calendar arithmetic
# ---------------------------------------------------------------------------------------------
def add_months(d: date, months: int) -> date:
    """Calendar-month arithmetic; clamps to the last day of shorter months (Jan 31 + 1m = Feb 28/29)."""
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def is_business_day(d: date, holidays: frozenset[date] = frozenset()) -> bool:
    return d.weekday() < 5 and d not in holidays


def add_business_days(d: date, n: int, holidays: frozenset[date] = frozenset()) -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    cur = d
    while remaining:
        cur += timedelta(days=step)
        if is_business_day(cur, holidays):
            remaining -= 1
    return cur


def nth_business_day(year: int, month: int, n: int, holidays: frozenset[date] = frozenset()) -> date:
    """The n-th (1-based) business day of the month."""
    cur = date(year, month, 1)
    count = 0
    while True:
        if is_business_day(cur, holidays):
            count += 1
            if count == n:
                return cur
        cur += timedelta(days=1)


def roll_date(d: date, roll: Roll, holidays: frozenset[date] = frozenset()) -> date:
    if roll is Roll.NONE:
        return d
    step = 1 if roll is Roll.FOLLOWING else -1
    while not is_business_day(d, holidays):
        d += timedelta(days=step)
    return d


def apply_offset(anchor: date, amount: int, unit: Unit, direction: Direction, holidays: frozenset[date] = frozenset()) -> date:
    sign = 1 if direction is Direction.AFTER else -1
    n = sign * amount
    if unit is Unit.DAY:
        return anchor + timedelta(days=n)
    if unit is Unit.WEEK:
        return anchor + timedelta(weeks=n)
    if unit is Unit.MONTH:
        return add_months(anchor, n)
    if unit is Unit.YEAR:
        return add_months(anchor, 12 * n)
    if unit is Unit.BUSINESS_DAY:
        return add_business_days(anchor, n, holidays)
    if unit is Unit.HOUR:
        return anchor + timedelta(hours=n)  # date arithmetic only; callers use due_at for hour precision
    raise ValueError(unit)


# ---------------------------------------------------------------------------------------------
# Rules and results
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TemporalRule:
    kind: DeadlineKind
    label: str
    source_ref: str | None = None
    # explicit
    explicit_text: str | None = None
    explicit_date: date | None = None
    day_first: bool | None = None
    # offset / event-triggered
    anchor: str | None = None
    offset: int | None = None
    unit: Unit | None = None
    direction: Direction = Direction.AFTER
    # recurrence
    frequency: Frequency | None = None
    nth_business_day: int | None = None
    day_of_month: int | None = None
    until: date | None = None
    count: int | None = None
    # options
    roll: Roll = Roll.NONE
    time_of_day: time | None = None
    timezone: str | None = None
    #: term arithmetic: the result is a term *end* (day before the anniversary)
    term_end: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, (date, time)):
                d[k] = v.isoformat()
            elif hasattr(v, "value"):
                d[k] = v.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TemporalRule":
        """Inverse of :meth:`to_dict` (used to recompute stored deadlines when anchors change)."""
        def opt(key: str, conv):
            v = d.get(key)
            return conv(v) if v not in (None, "") else None

        return cls(
            kind=DeadlineKind(d["kind"]), label=d.get("label", ""), source_ref=d.get("source_ref"), explicit_text=d.get("explicit_text"),
            explicit_date=opt("explicit_date", date.fromisoformat), day_first=d.get("day_first"), anchor=d.get("anchor"), offset=d.get("offset"),
            unit=opt("unit", Unit), direction=Direction(d.get("direction") or "after"), frequency=opt("frequency", Frequency),
            nth_business_day=d.get("nth_business_day"), day_of_month=d.get("day_of_month"), until=opt("until", date.fromisoformat), count=d.get("count"),
            roll=Roll(d.get("roll") or "none"), time_of_day=opt("time_of_day", time.fromisoformat), timezone=d.get("timezone"), term_end=bool(d.get("term_end", False)),
        )


@dataclass
class DeadlineComputation:
    rule: TemporalRule
    status: ComputationStatus
    due_date: date | None = None
    due_at: datetime | None = None
    anchor_name: str | None = None
    anchor_date: date | None = None
    trace: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing_anchors: list[str] = field(default_factory=list)
    occurrences: list[date] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        """Fully determined with no material assumptions."""
        return self.status is ComputationStatus.COMPUTED and not self.assumptions

    @property
    def validation_status(self) -> ValidationStatus:
        """Stored status. Nothing computed is 'confirmed' until a human confirms it."""
        return ValidationStatus.UNRESOLVED if self.status is ComputationStatus.UNRESOLVED else ValidationStatus.PENDING_REVIEW

    @property
    def kind(self) -> DeadlineKind:
        if self.status is ComputationStatus.UNRESOLVED and self.rule.kind is not DeadlineKind.EVENT_TRIGGERED:
            return DeadlineKind.UNRESOLVED
        return self.rule.kind


Anchors = dict[str, date | None]


def _resolve_tz(rule: TemporalRule, res: DeadlineComputation) -> ZoneInfo | None:
    if rule.time_of_day is None:
        if rule.timezone:
            res.notes.append(f"Date-only deadline; interpreted as end of day in {rule.timezone}.")
        else:
            res.notes.append("Date-only deadline; no timezone stated, so the date is shown without a timezone.")
        return None
    if not rule.timezone:
        res.assumptions.append("A time of day is stated but no timezone; UTC assumed.")
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(rule.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        res.assumptions.append(f"Timezone '{rule.timezone}' is not recognised; UTC assumed.")
        return ZoneInfo("UTC")


def _finish(res: DeadlineComputation, due: date) -> DeadlineComputation:
    rule = res.rule
    tz = _resolve_tz(rule, res)
    res.due_date = due
    if rule.time_of_day is not None and tz is not None:
        res.due_at = datetime.combine(due, rule.time_of_day, tzinfo=tz)
        res.trace.append(f"Time of day {rule.time_of_day.isoformat()} in {tz.key} -> {res.due_at.isoformat()}")
    return res


def compute_deadline(rule: TemporalRule, anchors: Anchors | None = None, *, holidays: frozenset[date] | None = None,
                     today: date | None = None) -> DeadlineComputation:
    """Compute one deadline. ``anchors`` maps anchor names to *known* dates (None/absent = unknown)."""
    anchors = anchors or {}
    res = DeadlineComputation(rule=rule, status=ComputationStatus.COMPUTED)
    cal = holidays if holidays is not None else frozenset()

    if rule.kind is DeadlineKind.EXPLICIT:
        return _explicit(rule, res)
    if rule.kind is DeadlineKind.RECURRING:
        return _recurring(rule, res, anchors, cal, today)
    if rule.kind in (DeadlineKind.DERIVED, DeadlineKind.EVENT_TRIGGERED):
        return _offset(rule, res, anchors, cal)
    res.status = ComputationStatus.UNRESOLVED
    res.trace.append("No computable temporal term was provided.")
    return res


def _explicit(rule: TemporalRule, res: DeadlineComputation) -> DeadlineComputation:
    if rule.explicit_date is not None:
        res.trace.append(f"Explicit date stated in contract: {rule.explicit_date.isoformat()}")
        return _finish(res, rule.explicit_date)
    if rule.explicit_text:
        parsed = parse_date_text(rule.explicit_text, day_first=rule.day_first)
        if parsed.value:
            res.trace.append(f"Parsed '{rule.explicit_text}' -> {parsed.value.isoformat()} ({parsed.note or 'unambiguous'})")
            if parsed.note.startswith(("day-first per", "month-first per")):
                res.assumptions.append(f"Numeric date interpreted using a locale hint ({parsed.note}).")
                res.status = ComputationStatus.AMBIGUOUS
            return _finish(res, parsed.value)
        res.status = ComputationStatus.UNRESOLVED
        if parsed.ambiguous:
            res.status = ComputationStatus.UNRESOLVED
            res.assumptions.append("Ambiguous numeric date; candidates: " + " or ".join(d.isoformat() for d in parsed.candidates) + ". Human confirmation required.")
            res.trace.append(f"'{rule.explicit_text}' is ambiguous and was NOT converted to a deadline.")
        else:
            res.trace.append(f"Could not read a date from '{rule.explicit_text}' ({parsed.note}).")
        return res
    res.status = ComputationStatus.UNRESOLVED
    res.trace.append("Explicit deadline has neither a date nor text.")
    return res


def _offset(rule: TemporalRule, res: DeadlineComputation, anchors: Anchors, cal: frozenset[date]) -> DeadlineComputation:
    if not rule.anchor or rule.offset is None or rule.unit is None:
        res.status = ComputationStatus.UNRESOLVED
        res.trace.append("Incomplete rule: anchor, offset and unit are all required.")
        return res
    res.anchor_name = rule.anchor
    anchor_date = anchors.get(rule.anchor)
    if anchor_date is None:
        res.status = ComputationStatus.UNRESOLVED
        res.missing_anchors.append(rule.anchor)
        res.trace.append(f"Anchor '{rule.anchor}' has no recorded date; deadline cannot be calculated until it is known.")
        return res
    res.anchor_date = anchor_date
    res.trace.append(f"Anchor {rule.anchor} = {anchor_date.isoformat()}")
    unit_txt = rule.unit.value.replace("_", " ")
    res.trace.append(f"{rule.offset} {unit_txt}{'s' if rule.offset != 1 else ''} {rule.direction.value} anchor")
    due = apply_offset(anchor_date, rule.offset, rule.unit, rule.direction, cal)
    res.trace.append(f"-> {due.isoformat()}")
    if rule.unit is Unit.BUSINESS_DAY:
        if not cal:
            res.assumptions.append("Business days exclude Saturdays and Sundays only; no public-holiday calendar was applied.")
    if rule.term_end:
        due = due - timedelta(days=1)
        res.trace.append(f"Term end = day before the anniversary -> {due.isoformat()}")
        res.assumptions.append("A term of N months ends on the day before the N-month anniversary; confirm against the contract wording.")
    if rule.unit is Unit.MONTH or rule.unit is Unit.YEAR:
        if anchor_date.day > 28 and add_months(anchor_date, 1).day != anchor_date.day:
            res.notes.append("Month-end clamping applied (shorter month).")
    if rule.roll is not Roll.NONE:
        rolled = roll_date(due, rule.roll, cal)
        if rolled != due:
            res.trace.append(f"Rolled {rule.roll.value} to business day -> {rolled.isoformat()}")
            due = rolled
    elif due.weekday() >= 5:
        res.notes.append(f"Result falls on a {due.strftime('%A')}; no business-day roll was applied.")
    return _finish(res, due)


def _period_months(freq: Frequency) -> int:
    return {Frequency.MONTHLY: 1, Frequency.QUARTERLY: 3, Frequency.SEMIANNUAL: 6, Frequency.ANNUAL: 12}[freq]


def expand_recurrence(rule: TemporalRule, start: date, *, until: date | None = None, limit: int = 60,
                      holidays: frozenset[date] = frozenset()) -> list[date]:
    """All occurrences from ``start`` (inclusive) up to ``until``/``rule.until``/``rule.count``.

    Each occurrence is derived from ``start`` directly (never iteratively) to avoid month-end drift."""
    if rule.frequency is None:
        return []
    end = rule.until or until
    out: list[date] = []
    i = 0
    while len(out) < min(limit, rule.count or limit):
        if rule.frequency is Frequency.WEEKLY:
            d = start + timedelta(weeks=i)
        else:
            base = add_months(date(start.year, start.month, 1), _period_months(rule.frequency) * i)
            if rule.nth_business_day:
                d = nth_business_day(base.year, base.month, rule.nth_business_day, holidays)
            else:
                dom = rule.day_of_month or start.day
                d = date(base.year, base.month, min(dom, calendar.monthrange(base.year, base.month)[1]))
        i += 1
        if d < start:
            continue
        if end and d > end:
            break
        out.append(roll_date(d, rule.roll, holidays))
        if i > limit * 3:
            break
    return out


def _recurring(rule: TemporalRule, res: DeadlineComputation, anchors: Anchors, cal: frozenset[date], today: date | None) -> DeadlineComputation:
    if rule.frequency is None:
        res.status = ComputationStatus.UNRESOLVED
        res.trace.append("Recurring rule without a frequency.")
        return res
    start_anchor = anchors.get(rule.anchor) if rule.anchor else anchors.get(ANCHOR_EFFECTIVE)
    name = rule.anchor or ANCHOR_EFFECTIVE
    res.anchor_name = name
    if start_anchor is None:
        res.status = ComputationStatus.UNRESOLVED
        res.missing_anchors.append(name)
        res.trace.append(f"Recurrence start anchor '{name}' has no recorded date; schedule cannot be generated.")
        return res
    res.anchor_date = start_anchor
    end = anchors.get(ANCHOR_EXPIRATION)
    occ = expand_recurrence(rule, start_anchor, until=end, holidays=cal)
    if not occ:
        res.status = ComputationStatus.UNRESOLVED
        res.trace.append("Recurrence produced no occurrences in the contract window.")
        return res
    res.occurrences = occ
    ref = today or date.today()
    upcoming = next((d for d in occ if d >= ref), occ[-1])
    desc = f"{rule.frequency.value}" + (f", {rule.nth_business_day}th business day" if rule.nth_business_day else (f", day {rule.day_of_month}" if rule.day_of_month else ""))
    res.trace.append(f"Recurring ({desc}) starting {start_anchor.isoformat()}; {len(occ)} occurrence(s) through {occ[-1].isoformat()}.")
    res.trace.append(f"Next occurrence on/after {ref.isoformat()}: {upcoming.isoformat()}")
    if rule.nth_business_day and not cal:
        res.assumptions.append("Business days exclude Saturdays and Sundays only; no public-holiday calendar was applied.")
    if end is None:
        res.assumptions.append("No expiration date is known, so the schedule is limited to the first occurrences.")
    return _finish(res, upcoming)


def compare_computations(old: DeadlineComputation, new: DeadlineComputation) -> dict[str, Any]:
    """Describe how an amendment changed a deadline (used by the amendment workflow)."""
    return {
        "label": new.rule.label,
        "old_due": old.due_date.isoformat() if old.due_date else None,
        "new_due": new.due_date.isoformat() if new.due_date else None,
        "shift_days": (new.due_date - old.due_date).days if old.due_date and new.due_date else None,
        "old_status": old.status.value,
        "new_status": new.status.value,
    }


def days_until(d: date | None, today: date | None = None) -> int | None:
    return None if d is None else (d - (today or date.today())).days


ANCHOR_CURRENT_TERM_END = "current_term_end"


def roll_term_end(term_end: date, renewal_months: int | None, today: date) -> tuple[date, int]:
    """End of the *current* term for an auto-renewing contract, assuming every term renewed.

    Returns ``(current_term_end, renewals_applied)``. The caller must record this as an assumption:
    the contract may in fact have been terminated or renegotiated.
    """
    if not renewal_months or renewal_months <= 0 or term_end >= today:
        return term_end, 0
    n = 0
    cur = term_end
    while cur < today and n < 600:
        n += 1
        cur = add_months(term_end + timedelta(days=1), renewal_months * n) - timedelta(days=1)
    return cur, n
