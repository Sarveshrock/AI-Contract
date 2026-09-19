"""Renewal Radar and deadline engine UI."""
from __future__ import annotations

from datetime import date
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDateEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget
from PyQt6.QtCore import QDate

from app.core.errors import ContractLensError
from app.models.enums import EventType, ValidationStatus
from app.services.renewals import RadarRow
from app.ui.components.base import days_text, fmt_date, label
from app.ui.components.calendar import MonthCalendar
from app.ui.components.charts import BarChart
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog, TextPromptDialog
from app.ui.components.primitives import GlassPanel, NeonButton, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, status_tone


def _urgency_tone(u: str) -> str:
    return {"overdue": "danger", "critical": "danger", "high": "warning", "medium": "info", "low": "success"}.get(u, "neutral")


class RenewalsScreen(BaseScreen):
    nav_id = "renewals"
    title = "Renewal Radar"
    eyebrow = "Deadline engine"
    icon_name = "radar"
    empty_title = "No renewal data yet"
    empty_message = "Contracts with an established term or notice period appear here after analysis."

    def build(self) -> QWidget:
        self._row: RadarRow | None = None
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])
        top = QHBoxLayout()
        self.horizon = BarChart(color="warning")
        hp = GlassPanel("Renewal horizon", "Contracts by days until the next decision date (notice deadline, else term end)")
        hp.body.addWidget(self.horizon)
        hp.setMinimumHeight(220)
        hp.setMaximumHeight(240)
        self.alert_panel = GlassPanel("Alert schedule", "Alerts are created when a deadline crosses these thresholds")
        self.alert_lbl = label("", "muted", wrap=True)
        self.alert_panel.body.addWidget(self.alert_lbl)
        self.alert_panel.setMaximumHeight(210)
        top.addWidget(hp, 3)
        top.addWidget(self.alert_panel, 2)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.table = DataTable([
            Column("Contract", lambda r: r.contract.title, stretch=True),
            Column("Term end", lambda r: r.current_term_end, lambda v, r: fmt_date(v) + (" *" if r.renewed_terms_assumed else ""), width=105),
            Column("Auto-renew", lambda r: "yes" if r.auto_renews else "no" if r.auto_renews is False else "unknown", width=75),
            Column("Notice", lambda r: r.notice_days, lambda v, r: f"{v} d" if v else "—", width=60, kind="number"),
            Column("Notice by", lambda r: r.notice_deadline.due_date if r.notice_deadline else None, lambda v, r: fmt_date(v), width=105),
            Column("When", lambda r: r.days_to_notice if r.days_to_notice is not None else r.days_to_end, lambda v, r: days_text(v), width=90, kind="number"),
            Column("Urgency", lambda r: r.urgency, kind="badge", tone=lambda v, r: _urgency_tone(v), width=90),
            Column("Level", lambda r: r.escalation, lambda v, r: f"L{v}", width=45, kind="number"),
        ], empty_text="No contracts with renewal information.")
        self.table.rowSelected.connect(self._selected)
        lp = GlassPanel("Renewal radar", "* = assumes automatic renewal occurred; confirm status")
        lp.body.addWidget(self.table)
        split.addWidget(lp)
        tabs = QTabWidget()
        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        self.d_title = label("Select a contract", "h3", wrap=True)
        self.d_badges = QHBoxLayout()
        self.trace = QPlainTextEdit()
        self.trace.setReadOnly(True)
        self.trace.setPlaceholderText("The calculation trace for the notice deadline appears here: source clause, anchor date, rule, result, assumptions and validation status.")
        dl.addWidget(self.d_title)
        dl.addLayout(self.d_badges)
        dl.addWidget(self.trace, 1)
        row = QHBoxLayout()
        self.b_confirm = NeonButton("Confirm date", "success", "check")
        self.b_override = NeonButton("Set date…", "default", "edit")
        self.b_done = NeonButton("Mark handled…", "default")
        self.b_event = NeonButton("Record event…", "default", "plus")
        for b in (self.b_confirm, self.b_override, self.b_done, self.b_event):
            row.addWidget(b)
        row.addStretch(1)
        dl.addLayout(row)
        self.b_confirm.clicked.connect(self._confirm)
        self.b_override.clicked.connect(self._override)
        self.b_done.clicked.connect(self._done)
        self.b_event.clicked.connect(self._event)
        cal_w = QWidget()
        cl = QVBoxLayout(cal_w)
        self.calendar = MonthCalendar()
        self.calendar.monthChanged.connect(self._month)
        self.calendar.daySelected.connect(self._day)
        self.day_lbl = label("Select a day to list its deadlines.", "muted", wrap=True)
        cl.addWidget(self.calendar, 1)
        cl.addWidget(self.day_lbl)
        exp = NeonButton("Export confirmed deadlines to calendar (.ics)…", "default", "download")
        exp.clicked.connect(self._export)
        cl.addWidget(exp)
        tabs.addTab(self.detail, "Deadline detail")
        tabs.addTab(cal_w, "Calendar")
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([640, 460])
        lay.addWidget(split, 1)
        self._set_actions(False)
        return root

    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        return {"rows": ws.renewals.radar(), "horizon": ws.renewals.horizon(), "offsets": ws.alerts.offsets(), "cal": ws.renewals.calendar(self.calendar.year, self.calendar.month)}

    def is_empty(self, data) -> bool:
        return not data["rows"]

    def render(self, data: dict[str, Any]) -> None:
        self.table.set_rows(data["rows"])
        self.horizon.set_data(list(data["horizon"].keys()), list(data["horizon"].values()))
        self.alert_lbl.setText("Thresholds: " + ", ".join(f"{o} days" for o in data["offsets"]) + " before each deadline; overdue deadlines escalate to the highest level.\n"
                               "Deadlines that are not yet human-confirmed are labelled as unverified in alerts. Administrators can change thresholds under Administration.")
        self.calendar.set_events(data["cal"])
        self._events = data["cal"]

    def _set_actions(self, on: bool) -> None:
        for b in (self.b_confirm, self.b_override, self.b_done, self.b_event):
            b.setEnabled(on)

    def _selected(self, r: RadarRow | None) -> None:
        self._row = r
        while self.d_badges.count():
            it = self.d_badges.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if r is None:
            self._set_actions(False)
            return
        d = r.notice_deadline
        self.d_title.setText(r.contract.title)
        self.d_badges.addWidget(StatusBadge(r.urgency, _urgency_tone(r.urgency)))
        if d:
            self.d_badges.addWidget(StatusBadge(d.validation_status.value, status_tone(d.validation_status.value)))
        if r.auto_renews:
            self.d_badges.addWidget(StatusBadge("auto-renews", "info"))
        self.d_badges.addStretch(1)
        lines = [f"Initial term end: {fmt_date(r.initial_expiration)}", f"Current term end: {fmt_date(r.current_term_end)}" + (f"  (assumes {r.renewed_terms_assumed} automatic renewal(s))" if r.renewed_terms_assumed else "")]
        if d:
            lines += ["", f"{d.label}: {fmt_date(d.due_date)} — {days_text(r.days_to_notice)}", f"Validation: {d.validation_status.value.replace('_', ' ')}   Escalation level: {d.escalation_level}", "", "Calculation trace:"]
            lines += [f"  {i}. {t}" for i, t in enumerate(d.calculation_trace, 1)]
            if d.assumptions:
                lines += ["", "Assumptions:"] + [f"  • {a}" for a in d.assumptions]
            if d.missing_anchors:
                lines += ["", "Missing: " + ", ".join(map(str, d.missing_anchors))]
        else:
            lines += ["", "No notice deadline was derived (no auto-renewal notice period found)."]
        self.trace.setPlainText("\n".join(lines))
        self._set_actions(d is not None)
        self.b_confirm.setEnabled(bool(d and d.due_date and d.validation_status is not ValidationStatus.CONFIRMED))

    def _act(self, fn, ok: str) -> None:
        self.ctx.run(fn, lambda _r: (self.ctx.toast(ok, "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="deadline update")

    def _confirm(self) -> None:
        if self._row and self._row.notice_deadline and ConfirmationDialog.ask(
                self, "Confirm deadline", f"Confirm {fmt_date(self._row.notice_deadline.due_date)} as the notice deadline? Only confirmed deadlines can be exported to calendars or sent to other systems.",
                confirm_text="Confirm", verify_text="I checked the source clause and the calculation trace."):
            did = self._row.notice_deadline.id
            self._act(lambda: self.ctx.ws.deadlines.confirm(did), "Deadline confirmed.")

    def _override(self) -> None:
        if not (self._row and self._row.notice_deadline):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Set deadline date")
        form = QFormLayout(dlg)
        date_edit = QDateEdit(QDate.currentDate())
        date_edit.setCalendarPopup(True)
        reason = QLineEdit()
        reason.setPlaceholderText("Reason (required)")
        ok = NeonButton("Save", "primary")
        ok.clicked.connect(dlg.accept)
        form.addRow("New date", date_edit)
        form.addRow("Reason", reason)
        form.addRow(ok)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = date_edit.date().toPyDate()
            did, why = self._row.notice_deadline.id, reason.text()
            self._act(lambda: self.ctx.ws.deadlines.override_date(did, d, why), "Deadline date updated and confirmed.")

    def _done(self) -> None:
        if self._row and self._row.notice_deadline:
            note = TextPromptDialog.ask(self, "Mark handled", "Record how this deadline was handled (for example: notice of non-renewal sent 2026-09-02).", min_len=5, confirm_text="Mark handled")
            if note:
                did = self._row.notice_deadline.id
                self._act(lambda: self.ctx.ws.deadlines.mark_done(did, note), "Deadline closed.")

    def _event(self) -> None:
        if not self._row:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Record event")
        form = QFormLayout(dlg)
        kind = QComboBox()
        for e in EventType:
            kind.addItem(e.value.replace("_", " ").title(), e)
        name = QLineEdit()
        name.setPlaceholderText("e.g. Invoice INV-2026-0912")
        when = QDateEdit(QDate.currentDate())
        when.setCalendarPopup(True)
        ok = NeonButton("Record", "primary")
        ok.clicked.connect(dlg.accept)
        form.addRow("Event", kind)
        form.addRow("Reference", name)
        form.addRow("Occurred on", when)
        form.addRow(ok)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cid = self._row.contract.id
            self._act(lambda: self.ctx.ws.deadlines.record_event(cid, kind.currentData(), name.text() or kind.currentText(), when.date().toPyDate()), "Event recorded; dependent deadlines recalculated.")

    def _month(self, y: int, m: int) -> None:
        self.ctx.run(lambda: self.ctx.ws.renewals.calendar(y, m), lambda cal: (self.calendar.set_events(cal), setattr(self, "_events", cal)), name="calendar")

    def _day(self, d: date) -> None:
        evs = getattr(self, "_events", {}).get(d, [])
        self.day_lbl.setText(f"{d.strftime('%A %d %B %Y')}\n" + ("\n".join(f"• {lab} — {title}" for lab, title, _c in evs) if evs else "No deadlines on this day."))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export calendar", "contractlens-deadlines.ics", "iCalendar (*.ics)")
        if not path:
            return
        ws = self.ctx.ws

        def work():
            ids = [r.deadline.id for r in ws.deadlines.list() if r.deadline.validation_status is ValidationStatus.CONFIRMED and r.deadline.due_date]
            if not ids:
                raise ContractLensError("no confirmed deadlines", user_message="There are no human-confirmed deadlines to export. Confirm deadlines first.")
            return ws.integrations.export_calendar(ids, path, confirmed=True)

        if ConfirmationDialog.ask(self, "Export to calendar file", "Only human-confirmed deadlines are exported, and the action is recorded in the audit log.", confirm_text="Export"):
            self.ctx.run(work, lambda n: self.ctx.toast(f"Exported {n} deadline(s).", "success"), name="export ics")
