"""Module 1 — Command Center: portfolio KPIs, charts, alerts, review queue, runs and processing status."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.models.enums import AlertStatus
from app.ui.components.agents_and_timeline import TimelineView
from app.ui.components.base import elide, fmt_date, label
from app.ui.components.calendar import MonthCalendar
from app.ui.components.charts import BarChart, DonutChart
from app.ui.components.primitives import GlassPanel, MetricCard, NeonButton, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, severity_tone, status_tone, theme

LIFECYCLE_COLORS = {"draft": "text_faint", "in_review": "warning", "active": "success", "expiring": "danger", "expired": "danger", "terminated": "text_faint", "archived": "text_faint"}


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    s = int((now - dt).total_seconds())
    return "just now" if s < 60 else f"{s // 60}m ago" if s < 3600 else f"{s // 3600}h ago" if s < 86400 else f"{s // 86400}d ago"


class CommandCenterScreen(BaseScreen):
    nav_id = "command"
    title = "Command Center"
    eyebrow = "Portfolio overview"
    icon_name = "command"
    empty_title = "No contracts in this workspace yet"

    def build(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        scroll.setWidget(host)
        self._root = QVBoxLayout(host)
        self._root.setContentsMargins(0, 0, 8, 0)
        self._root.setSpacing(SPACE["lg"])
        # KPI row
        self._cards: dict[str, MetricCard] = {}
        krow = QHBoxLayout()
        krow.setSpacing(SPACE["md"])
        for key, title, icon, tone in [
            ("active", "Active contracts", "contract", "cyan"), ("renewals", "Renewals ≤ 90 days", "radar", "warning"), ("pending", "Pending obligations", "obligations", "indigo"),
            ("overdue", "Overdue items", "clock", "danger"), ("findings", "Unresolved AI findings", "risk", "violet"), ("review", "Review queue", "eye", "warning"),
            ("processing", "Documents processing", "layers", "info"),
        ]:
            c = MetricCard(title, icon, tone)
            c.clicked.connect(lambda k=key: self._card_clicked(k))
            self._cards[key] = c
            krow.addWidget(c, 1)
        self._root.addLayout(krow)
        # main grid
        grid = QHBoxLayout()
        grid.setSpacing(SPACE["lg"])
        left = QVBoxLayout()
        left.setSpacing(SPACE["lg"])
        right = QVBoxLayout()
        right.setSpacing(SPACE["lg"])
        grid.addLayout(left, 3)
        grid.addLayout(right, 2)
        self._root.addLayout(grid)

        self.donut_lifecycle = DonutChart()
        p1 = GlassPanel("Contract lifecycle", "Distribution by status")
        p1.body.addWidget(self.donut_lifecycle)
        self.donut_review = DonutChart()
        p2 = GlassPanel("Review status", "Obligations by review state")
        p2.body.addWidget(self.donut_review)
        r1 = QHBoxLayout()
        r1.setSpacing(SPACE["lg"])
        r1.addWidget(p1, 1)
        r1.addWidget(p2, 1)
        left.addLayout(r1)
        self.bars_category = BarChart(horizontal=True, color="violet")
        p3 = GlassPanel("Obligations by category")
        p3.body.addWidget(self.bars_category)
        self.bars_month = BarChart(color="cyan")
        p4 = GlassPanel("Deadlines by month", "Next 12 months · open deadlines")
        p4.body.addWidget(self.bars_month)
        r2 = QHBoxLayout()
        r2.setSpacing(SPACE["lg"])
        r2.addWidget(p3, 1)
        r2.addWidget(p4, 1)
        left.addLayout(r2)
        self.calendar = MonthCalendar()
        self.calendar.monthChanged.connect(self._calendar_month)
        p5 = GlassPanel("Deadline calendar")
        open_radar = NeonButton("Open Renewal Radar", "ghost", "radar")
        open_radar.clicked.connect(lambda: self.ctx.navigate("renewals", None))
        p5.add_action(open_radar)
        p5.body.addWidget(self.calendar)
        self.bars_activity = BarChart(color="indigo")
        p6 = GlassPanel("Contract activity", "Audited events per day · last 30 days")
        p6.body.addWidget(self.bars_activity)
        r3 = QHBoxLayout()
        r3.setSpacing(SPACE["lg"])
        r3.addWidget(p5, 1)
        r3.addWidget(p6, 1)
        left.addLayout(r3)

        self.alerts_panel = GlassPanel("Alert center", "Escalating deadline and contract alerts")
        self.alerts_list = QVBoxLayout()
        self.alerts_list.setSpacing(6)
        self.alerts_panel.body.addLayout(self.alerts_list)
        right.addWidget(self.alerts_panel)
        self.queue_panel = GlassPanel("High-priority review queue")
        qb = NeonButton("Open workbench", "ghost", "eye")
        qb.clicked.connect(lambda: self.ctx.navigate("risk", {"tab": "review"}))
        self.queue_panel.add_action(qb)
        self.queue_list = QVBoxLayout()
        self.queue_list.setSpacing(6)
        self.queue_panel.body.addLayout(self.queue_list)
        right.addWidget(self.queue_panel)
        self.runs_panel = GlassPanel("Recent AI analysis runs")
        self.runs_view = TimelineView()
        self.runs_view.setMinimumHeight(170)
        self.runs_panel.body.addWidget(self.runs_view)
        right.addWidget(self.runs_panel)
        self.pipeline_panel = GlassPanel("Processing pipeline")
        self.pipeline_row = QVBoxLayout()
        self.pipeline_panel.body.addLayout(self.pipeline_row)
        right.addWidget(self.pipeline_panel)
        self.changes_panel = GlassPanel("Recent contract changes")
        self.changes_list = QVBoxLayout()
        self.changes_panel.body.addLayout(self.changes_list)
        right.addWidget(self.changes_panel)
        self.insight_panel = GlassPanel("Portfolio insights", "Derived only from your data")
        self.insight_list = QVBoxLayout()
        self.insight_panel.body.addLayout(self.insight_list)
        right.addWidget(self.insight_panel)
        right.addStretch(1)
        left.addStretch(1)

        refresh = NeonButton("Refresh", "default", "refresh")
        refresh.clicked.connect(self._refresh)
        scan = NeonButton("Run alert scan", "default", "bell")
        scan.setToolTip("Create alerts for approaching and overdue deadlines (safe to repeat)")
        scan.clicked.connect(self._scan)
        self.actions.addWidget(scan)
        self.actions.addWidget(refresh)
        self.view_empty_bound = False
        return scroll

    # ------------------------------------------------------------------ data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        today = date.today()
        data = ws.analytics.dashboard(today)
        alerts = ws.alerts.list(limit=6)
        queue = ws.reviews.queue()[:6]
        titles = {c.id: c.title for c in ws.repos.contracts.list()}
        cal = ws.renewals.calendar(self.calendar.year, self.calendar.month)
        return {"data": data, "alerts": alerts, "queue": queue, "titles": titles, "calendar": cal}

    def is_empty(self, payload: dict[str, Any]) -> bool:
        return payload["data"].is_empty

    def empty_action(self) -> str | None:
        return "Load demo data" if self.ctx.ws.settings.is_demo else "Upload a contract"

    def on_empty_action(self) -> None:
        if self.ctx.ws.settings.is_demo:
            from app.ui.demo_actions import load_demo_data

            load_demo_data(self.ctx)
        else:
            super().on_empty_action()

    def render(self, payload: dict[str, Any]) -> None:
        d, titles = payload["data"], payload["titles"]
        c = self._cards
        c["active"].set_value(d.active_contracts, f"of {d.total_contracts} contracts")
        c["renewals"].set_value(d.renewals_90d, "notice or term end soon", "warning" if d.renewals_90d else "success")
        c["pending"].set_value(d.pending_obligations, "open obligations")
        c["overdue"].set_value(d.overdue_items, "past their due date", "danger" if d.overdue_items else "success")
        c["findings"].set_value(d.unresolved_findings, "open review signals", "violet")
        c["review"].set_value(d.review_queue, "awaiting a human decision", "warning" if d.review_queue else "success")
        proc = sum(v for k, v in d.processing.items() if k in ("pending", "extracting", "ocr", "chunking", "embedding"))
        c["processing"].set_value(proc, f"{d.processing.get('indexed', 0)} indexed · {d.processing.get('failed', 0)} failed", "info" if not d.processing.get("failed") else "danger")
        t = theme()
        self.donut_lifecycle.set_data({k.replace("_", " "): v for k, v in d.lifecycle.items()}, "contracts")
        self.donut_lifecycle._colors = {k.replace("_", " "): getattr(t, LIFECYCLE_COLORS.get(k, "info")) for k in d.lifecycle}
        self.donut_review.set_data(d.review_status, "obligations")
        self.donut_review._colors = {"unreviewed": t.text_faint, "needs_review": t.warning, "approved": t.success, "corrected": t.info, "rejected": t.danger}
        cats = sorted(d.obligations_by_category.items(), key=lambda kv: -kv[1])[:8]
        self.bars_category.set_data([k for k, _ in cats], [v for _, v in cats])
        self.bars_month.set_data([m[5:] + "/" + m[2:4] for m in d.deadlines_by_month], list(d.deadlines_by_month.values()))
        days = list(d.activity.items())
        self.bars_activity.set_data([k[8:] if i % 5 == 0 else "" for i, (k, _) in enumerate(days)], [v for _, v in days])
        self.calendar.set_events(payload["calendar"])
        self._fill_alerts(payload["alerts"])
        self._fill_queue(payload["queue"], titles)
        self._fill_runs(d.recent_runs, titles)
        self._fill_pipeline(d.processing)
        self._fill_changes(d.recent_changes, titles)
        self._fill_list(self.insight_list, [(i, "info") for i in d.insights] or [("No open portfolio issues right now.", "success")])

    # ------------------------------------------------------------------ fillers
    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                CommandCenterScreen._clear(it.layout())  # type: ignore[arg-type]

    def _fill_list(self, layout: QVBoxLayout, rows: list[tuple[str, str]]) -> None:
        self._clear(layout)
        for text, tone in rows:
            r = QHBoxLayout()
            r.addWidget(StatusBadge("•", tone))
            r.addWidget(label(text, "muted", wrap=True), 1)
            layout.addLayout(r)

    def _fill_alerts(self, alerts) -> None:
        self._clear(self.alerts_list)
        if not alerts:
            self.alerts_list.addWidget(label("No active alerts. Deadlines are monitored automatically.", "muted", wrap=True))
            return
        for a in alerts:
            row = QHBoxLayout()
            row.addWidget(StatusBadge(a.severity.value, severity_tone(a.severity.value)))
            txt = QVBoxLayout()
            txt.setSpacing(0)
            txt.addWidget(label(elide(a.title, 60)))
            txt.addWidget(label(elide(a.message, 90), "faint"))
            row.addLayout(txt, 1)
            b = NeonButton("Acknowledge" if a.status is AlertStatus.UNREAD else "Seen", "ghost")
            b.setEnabled(a.status is AlertStatus.UNREAD)
            b.clicked.connect(lambda _=False, aid=a.id: self._ack(aid))
            row.addWidget(b)
            self.alerts_list.addLayout(row)

    def _fill_queue(self, queue, titles) -> None:
        self._clear(self.queue_list)
        if not queue:
            self.queue_list.addWidget(label("Nothing is waiting for review.", "muted"))
            return
        for c in queue:
            row = QHBoxLayout()
            row.addWidget(StatusBadge(c.priority.value, severity_tone(c.priority.value)))
            txt = QVBoxLayout()
            txt.setSpacing(0)
            txt.addWidget(label(elide(c.title, 58)))
            txt.addWidget(label(titles.get(c.contract_id, ""), "faint"))
            row.addLayout(txt, 1)
            self.queue_list.addLayout(row)

    def _fill_runs(self, runs, titles) -> None:
        items = []
        for r in runs:
            when = _ago(r.finished_at or r.created_at)
            sub = titles.get(r.contract_id, "") + (f" · quality {r.quality_score:.0%}" if r.quality_score is not None else "")
            items.append((f"{r.run_type.value.replace('_', ' ').title()} — {r.status.value.replace('_', ' ')}", sub, when, status_tone(r.status.value)))
        self.runs_view.set_items(items, "No analysis runs yet.")

    def _fill_pipeline(self, processing: dict[str, int]) -> None:
        self._clear(self.pipeline_row)
        docs = {k: v for k, v in processing.items() if not k.startswith("analysis:")}
        analysis = {k[9:]: v for k, v in processing.items() if k.startswith("analysis:")}
        self.pipeline_row.addWidget(label("Document indexing", "faint"))
        row = QHBoxLayout()
        for k, v in docs.items():
            row.addWidget(StatusBadge(f"{k} · {v}", status_tone(k)))
        row.addStretch(1)
        self.pipeline_row.addLayout(row)
        self.pipeline_row.addWidget(label("Contract analysis", "faint"))
        row2 = QHBoxLayout()
        for k, v in analysis.items():
            row2.addWidget(StatusBadge(f"{k} · {v}", status_tone(k)))
        row2.addStretch(1)
        self.pipeline_row.addLayout(row2)

    def _fill_changes(self, changes, titles) -> None:
        self._clear(self.changes_list)
        if not changes:
            self.changes_list.addWidget(label("No version comparisons recorded yet.", "muted"))
            return
        for ch in changes:
            self.changes_list.addWidget(label(f"{titles.get(UUIDish(ch['contract_id']), 'Contract')}: {elide(ch['summary'], 90)}", "muted", wrap=True))

    # ------------------------------------------------------------------ actions
    def _refresh(self) -> None:
        self._stale = True
        self.load()

    def _scan(self) -> None:
        self.ctx.run(lambda: self.ctx.ws.alerts.scan(), lambda new: (self.ctx.toast(f"{len(new)} new alert(s) created." if new else "No new alerts. Everything is already up to date.", "success" if new else "info"), self._refresh(), self.ctx.refresh_badges()), name="alert scan")

    def _ack(self, alert_id) -> None:
        self.ctx.run(lambda: self.ctx.ws.alerts.acknowledge(alert_id), lambda _a: (self._refresh(), self.ctx.refresh_badges()), name="acknowledge alert")

    def _card_clicked(self, key: str) -> None:
        target = {"active": "contracts", "renewals": "renewals", "pending": "obligations", "overdue": "obligations", "findings": "risk", "review": "risk", "processing": "runs"}[key]
        params = {"view": "overdue"} if key == "overdue" else {"tab": "review"} if key == "review" else None
        self.ctx.navigate(target, params)

    def _calendar_month(self, year: int, month: int) -> None:
        self.ctx.run(lambda: self.ctx.ws.renewals.calendar(year, month), self.calendar.set_events, name="calendar")


def UUIDish(v: str):  # noqa: N802
    from uuid import UUID

    return UUID(v)


_ = (fmt_date, Qt)
