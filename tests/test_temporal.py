from __future__ import annotations

from datetime import date, time

import pytest

from app.models.enums import DeadlineKind, ValidationStatus
from app.tools.temporal import (
    ANCHOR_EFFECTIVE,
    ANCHOR_EXPIRATION,
    ComputationStatus,
    Direction,
    Frequency,
    Roll,
    TemporalRule,
    Unit,
    add_business_days,
    add_months,
    compare_computations,
    compute_deadline,
    expand_recurrence,
    nth_business_day,
    parse_date_text,
)


# ---------------------------------------------------------------- date parsing
@pytest.mark.parametrize("text,expected", [
    ("January 15, 2025", date(2025, 1, 15)),
    ("Jan 5 2025", date(2025, 1, 5)),
    ("15 January 2025", date(2025, 1, 15)),
    ("the 1st day of March, 2025", date(2025, 3, 1)),
    ("2025-09-01", date(2025, 9, 1)),
    ("25/12/2025", date(2025, 12, 25)),
    ("12/25/2025", date(2025, 12, 25)),
])
def test_parse_unambiguous_dates(text, expected):
    p = parse_date_text(text)
    assert p.value == expected and not p.ambiguous


def test_ambiguous_numeric_date_is_never_resolved_silently():
    p = parse_date_text("03/04/2025")
    assert p.value is None and p.ambiguous
    assert set(p.candidates) == {date(2025, 3, 4), date(2025, 4, 3)}
    assert parse_date_text("03/04/2025", day_first=True).value == date(2025, 4, 3)
    assert parse_date_text("03/04/2025", day_first=False).value == date(2025, 3, 4)


def test_invalid_dates():
    assert parse_date_text("February 30, 2025").value is None
    assert parse_date_text("no date here").value is None


# ---------------------------------------------------------------- arithmetic
def test_month_arithmetic_clamps():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 11, 30), 3) == date(2026, 2, 28)
    assert add_months(date(2025, 3, 15), -3) == date(2024, 12, 15)


def test_business_days_and_holidays():
    fri = date(2025, 1, 3)
    assert add_business_days(fri, 1) == date(2025, 1, 6)
    assert add_business_days(fri, 1, frozenset({date(2025, 1, 6)})) == date(2025, 1, 7)
    assert add_business_days(date(2025, 1, 6), -1) == fri
    assert nth_business_day(2025, 2, 5) == date(2025, 2, 7)  # Feb 2025 starts on Saturday


# ---------------------------------------------------------------- explicit
def test_explicit_date():
    r = compute_deadline(TemporalRule(DeadlineKind.EXPLICIT, "Delivery", explicit_text="March 1, 2026"))
    assert r.status is ComputationStatus.COMPUTED and r.due_date == date(2026, 3, 1) and r.is_reliable
    assert r.validation_status is ValidationStatus.PENDING_REVIEW  # nothing is confirmed without a human


def test_ambiguous_explicit_date_becomes_unresolved():
    r = compute_deadline(TemporalRule(DeadlineKind.EXPLICIT, "Delivery", explicit_text="03/04/2026"))
    assert r.status is ComputationStatus.UNRESOLVED and r.due_date is None
    assert r.assumptions and "Ambiguous" in r.assumptions[0]
    assert r.validation_status is ValidationStatus.UNRESOLVED and r.kind is DeadlineKind.UNRESOLVED


# ---------------------------------------------------------------- notice windows / derived
def test_renewal_notice_window_from_term_end():
    # MSA: term 24 months from 2025-01-15; notice at least 90 days before end of term
    term = compute_deadline(
        TemporalRule(DeadlineKind.DERIVED, "Term end", anchor=ANCHOR_EFFECTIVE, offset=24, unit=Unit.MONTH, direction=Direction.AFTER, term_end=True),
        {ANCHOR_EFFECTIVE: date(2025, 1, 15)},
    )
    assert term.due_date == date(2027, 1, 14)
    assert any("day before the N-month anniversary" in a for a in term.assumptions) and not term.is_reliable
    notice = compute_deadline(
        TemporalRule(DeadlineKind.DERIVED, "Non-renewal notice", anchor=ANCHOR_EXPIRATION, offset=90, unit=Unit.DAY, direction=Direction.BEFORE),
        {ANCHOR_EXPIRATION: term.due_date},
    )
    assert notice.due_date == date(2026, 10, 16)
    assert notice.anchor_date == date(2027, 1, 14) and notice.is_reliable
    assert any("Anchor expiration_date = 2027-01-14" in t for t in notice.trace)


def test_missing_anchor_is_not_invented():
    r = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "Notice", anchor=ANCHOR_EXPIRATION, offset=60, unit=Unit.DAY, direction=Direction.BEFORE), {})
    assert r.status is ComputationStatus.UNRESOLVED and r.due_date is None
    assert r.missing_anchors == [ANCHOR_EXPIRATION]
    r2 = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "Notice", anchor=ANCHOR_EXPIRATION, offset=60, unit=Unit.DAY), {ANCHOR_EXPIRATION: None})
    assert r2.due_date is None


def test_incomplete_rule_unresolved():
    assert compute_deadline(TemporalRule(DeadlineKind.DERIVED, "x", anchor="a")).status is ComputationStatus.UNRESOLVED


def test_event_triggered_waits_for_event_then_computes():
    rule = TemporalRule(DeadlineKind.EVENT_TRIGGERED, "Pay invoice", anchor="invoice_received", offset=30, unit=Unit.DAY, direction=Direction.AFTER)
    waiting = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 1, 15)})
    assert waiting.status is ComputationStatus.UNRESOLVED and waiting.missing_anchors == ["invoice_received"]
    assert waiting.kind is DeadlineKind.EVENT_TRIGGERED
    done = compute_deadline(rule, {"invoice_received": date(2025, 3, 10)})
    assert done.due_date == date(2025, 4, 9) and done.is_reliable


def test_business_day_offset_flags_holiday_assumption():
    rule = TemporalRule(DeadlineKind.DERIVED, "Onboarding", anchor=ANCHOR_EFFECTIVE, offset=10, unit=Unit.BUSINESS_DAY)
    r = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 3, 1)})
    assert r.due_date == date(2025, 3, 14) and any("holiday" in a for a in r.assumptions)
    with_cal = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 3, 1)}, holidays=frozenset({date(2025, 3, 10)}))
    assert with_cal.due_date == date(2025, 3, 17) and not with_cal.assumptions


def test_weekend_result_not_silently_rolled_but_can_be():
    rule = TemporalRule(DeadlineKind.DERIVED, "Notice", anchor=ANCHOR_EFFECTIVE, offset=7, unit=Unit.DAY)
    r = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 1, 1)})  # Jan 8 is a Wednesday
    assert r.due_date == date(2025, 1, 8)
    sat = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "N", anchor="a", offset=3, unit=Unit.DAY), {"a": date(2025, 1, 4)})  # Jan 7? Sat+3
    weekend = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "N", anchor="a", offset=1, unit=Unit.DAY), {"a": date(2025, 1, 3)})  # Sat Jan 4
    assert weekend.due_date == date(2025, 1, 4) and any("Saturday" in n for n in weekend.notes)
    rolled = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "N", anchor="a", offset=1, unit=Unit.DAY, roll=Roll.FOLLOWING), {"a": date(2025, 1, 3)})
    assert rolled.due_date == date(2025, 1, 6)
    assert sat.due_date is not None


def test_timezone_aware_due_at():
    rule = TemporalRule(DeadlineKind.DERIVED, "Notice by 5pm", anchor=ANCHOR_EFFECTIVE, offset=10, unit=Unit.DAY, time_of_day=time(17, 0), timezone="America/New_York")
    r = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 3, 1)})
    assert r.due_date == date(2025, 3, 11)
    assert r.due_at.isoformat() == "2025-03-11T17:00:00-04:00"  # DST began March 9
    no_tz = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "x", anchor="a", offset=1, unit=Unit.DAY, time_of_day=time(9, 0)), {"a": date(2025, 1, 1)})
    assert any("UTC assumed" in a for a in no_tz.assumptions)
    bad_tz = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "x", anchor="a", offset=1, unit=Unit.DAY, time_of_day=time(9, 0), timezone="Mars/Olympus"), {"a": date(2025, 1, 1)})
    assert any("not recognised" in a for a in bad_tz.assumptions)


# ---------------------------------------------------------------- recurring
def test_recurring_monthly_nth_business_day():
    rule = TemporalRule(DeadlineKind.RECURRING, "Monthly report", anchor=ANCHOR_EFFECTIVE, frequency=Frequency.MONTHLY, nth_business_day=5)
    occ = expand_recurrence(rule, date(2025, 1, 15), until=date(2025, 6, 30))
    assert occ == [date(2025, 2, 7), date(2025, 3, 7), date(2025, 4, 7), date(2025, 5, 7), date(2025, 6, 6)]
    r = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 1, 15), ANCHOR_EXPIRATION: date(2027, 1, 14)}, today=date(2025, 3, 20))
    assert r.due_date == date(2025, 4, 7) and len(r.occurrences) == 24 and r.kind is DeadlineKind.RECURRING


def test_recurring_month_end_does_not_drift():
    rule = TemporalRule(DeadlineKind.RECURRING, "Month end", frequency=Frequency.MONTHLY)
    occ = expand_recurrence(rule, date(2025, 1, 31), until=date(2025, 5, 31))
    assert occ == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31)]


def test_recurring_quarterly_annual_count():
    q = expand_recurrence(TemporalRule(DeadlineKind.RECURRING, "Q", frequency=Frequency.QUARTERLY, count=4), date(2025, 2, 10))
    assert q == [date(2025, 2, 10), date(2025, 5, 10), date(2025, 8, 10), date(2025, 11, 10)]
    a = expand_recurrence(TemporalRule(DeadlineKind.RECURRING, "A", frequency=Frequency.ANNUAL, count=3), date(2024, 2, 29))
    assert a == [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28)]


def test_recurring_without_anchor_is_unresolved():
    r = compute_deadline(TemporalRule(DeadlineKind.RECURRING, "Report", frequency=Frequency.MONTHLY, nth_business_day=5), {})
    assert r.status is ComputationStatus.UNRESOLVED and r.missing_anchors == [ANCHOR_EFFECTIVE]


def test_recurring_no_expiration_flags_assumption():
    rule = TemporalRule(DeadlineKind.RECURRING, "R", frequency=Frequency.MONTHLY, day_of_month=1)
    r = compute_deadline(rule, {ANCHOR_EFFECTIVE: date(2025, 1, 1)}, today=date(2025, 1, 2))
    assert any("No expiration" in a for a in r.assumptions)


# ---------------------------------------------------------------- amendment-related
def test_amendment_shifts_notice_deadline():
    anchors = {ANCHOR_EXPIRATION: date(2027, 1, 14)}
    old = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "Non-renewal notice", anchor=ANCHOR_EXPIRATION, offset=90, unit=Unit.DAY, direction=Direction.BEFORE), anchors)
    new = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "Non-renewal notice", anchor=ANCHOR_EXPIRATION, offset=60, unit=Unit.DAY, direction=Direction.BEFORE), anchors)
    diff = compare_computations(old, new)
    assert diff == {"label": "Non-renewal notice", "old_due": "2026-10-16", "new_due": "2026-11-15", "shift_days": 30, "old_status": "computed", "new_status": "computed"}


def test_rule_serialisation_roundtrip_is_json_safe():
    import json

    rule = TemporalRule(DeadlineKind.RECURRING, "R", frequency=Frequency.MONTHLY, nth_business_day=5, until=date(2026, 1, 1), time_of_day=time(9, 30))
    d = rule.to_dict()
    assert json.dumps(d) and d["kind"] == "recurring" and d["until"] == "2026-01-01" and d["time_of_day"] == "09:30:00"
