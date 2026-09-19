"""iCalendar (.ics) generation for confirmed deadlines (RFC 5545 all-day events)."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class CalendarItem:
    uid_seed: str
    summary: str
    due: date
    description: str


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\r", "").replace("\n", r"\n")


def _fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b
        else:
            cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def build_ics(items: Iterable[CalendarItem], *, prodid: str = "-//ContractLens//Deadlines//EN", alarm_days_before: int | None = 7) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{prodid}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for it in items:
        uid = hashlib.sha1(it.uid_seed.encode()).hexdigest()[:24] + "@contractlens"
        end = date.fromordinal(it.due.toordinal() + 1)
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{it.due.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
                  f"SUMMARY:{_escape(it.summary)}", f"DESCRIPTION:{_escape(it.description)}"]
        if alarm_days_before:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(it.summary)}", f"TRIGGER:-P{alarm_days_before}D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
