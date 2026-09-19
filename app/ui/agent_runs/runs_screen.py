"""Agent Runs: live agent registry, run history, workflow plan and step timeline."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QSplitter, QVBoxLayout, QWidget

from app.database.store import F
from app.models.entities import AnalysisRun
from app.ui.components.agents_and_timeline import AGENTS, AgentStatusPanel, TimelineView
from app.ui.components.base import clear_layout, label
from app.ui.components.data_table import Column, DataTable
from app.ui.components.primitives import GlassPanel, NeonButton, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, status_tone

TITLES = {k: t for k, t, _d in AGENTS}


def _dur(r: AnalysisRun) -> str:
    if r.started_at and r.finished_at:
        return f"{(r.finished_at - r.started_at).total_seconds():.1f}s"
    return "running" if r.status.value == "running" else "—"


class AgentRunsScreen(BaseScreen):
    nav_id = "runs"
    title = "Agent Runs"
    eyebrow = "Orchestration log"
    icon_name = "runs"
    empty_title = "No agent runs yet"
    empty_message = "Every analysis, comparison and question is logged here with its workflow plan, steps, warnings and token usage. Contract text is never stored in run logs."

    def build(self) -> QWidget:
        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = DataTable([
            Column("Started", lambda r: r[0].created_at, lambda v, r: v.strftime("%d %b %H:%M"), width=110),
            Column("Type", lambda r: r[0].run_type.value, lambda v, r: v.replace("_", " "), width=130),
            Column("Contract", lambda r: r[1], stretch=True),
            Column("Status", lambda r: r[0].status.value, kind="badge", tone=lambda v, r: status_tone(v), width=115),
            Column("Quality", lambda r: r[0].quality_score, lambda v, r: f"{v:.0%}" if v is not None else "—", kind="number", width=70),
            Column("Time", lambda r: r[0], lambda v, r: _dur(v), width=75),
            Column("Tokens", lambda r: r[0].prompt_tokens + r[0].completion_tokens, kind="number", width=80),
        ], empty_text="No runs.")
        self.table.rowSelected.connect(self._selected)
        tp = GlassPanel("Run history", "Newest first")
        tp.body.addWidget(self.table)
        ll.addWidget(tp, 3)
        self.agent_panel = AgentStatusPanel()
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.agent_panel)
        ap = GlassPanel("Agents", "Capabilities are enforced per agent; none can write data or take external actions")
        ap.body.addWidget(sc)
        ll.addWidget(ap, 4)
        split.addWidget(left)
        self.detail = GlassPanel("Run detail")
        self.d_head = label("Select a run", "h3", wrap=True)
        self.d_badges = QHBoxLayout()
        self.plan_row = QVBoxLayout()
        self.steps = TimelineView()
        self.result = label("", "muted", wrap=True, selectable=True)
        for w in (self.d_head,):
            self.detail.body.addWidget(w)
        self.detail.body.addLayout(self.d_badges)
        self.detail.body.addWidget(label("WORKFLOW PLAN", "faint"))
        self.detail.body.addLayout(self.plan_row)
        self.detail.body.addWidget(label("STEPS", "faint"))
        self.detail.body.addWidget(self.steps, 1)
        self.detail.body.addWidget(self.result)
        split.addWidget(self.detail)
        split.setSizes([700, 560])
        lay.addWidget(split)
        refresh = NeonButton("Refresh", "default", "refresh")
        refresh.clicked.connect(lambda: (self.mark_stale(), self.load()))
        self.actions.addWidget(refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._poll)
        return root

    def showEvent(self, e) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _poll(self) -> None:
        if any(r[0].status.value == "running" for r in self.table.rows()) or self.ctx.runner.active > 1:
            self.mark_stale()
            self.load()

    def fetch(self) -> list[tuple[AnalysisRun, str]]:
        ws = self.ctx.ws
        titles = {c.id: c.title for c in ws.repos.contracts.list()}
        return [(r, titles.get(r.contract_id, "—")) for r in ws.repos.runs.list(order_by=[("created_at", True)], limit=200)]

    def is_empty(self, data) -> bool:
        return not data

    def render(self, data) -> None:
        self.table.set_rows(data)
        self.agent_panel.reset()
        for r, _t in data[:1]:
            if r.status.value == "running":
                for s in r.steps:
                    self.agent_panel.set_state(s["agent"], s["status"])
        running = [r for r, _t in data if r.status.value == "running"]
        if running:
            plan = [s["agent"] for s in running[0].workflow_plan.get("steps", [])]
            done = {s["agent"] for s in running[0].steps}
            nxt = next((a for a in plan if a not in done), None)
            if nxt:
                self.agent_panel.set_state(nxt, "running", "working…")

    def _selected(self, row) -> None:
        clear_layout(self.d_badges)
        clear_layout(self.plan_row)
        if row is None:
            return
        r, title = row
        self.d_head.setText(f"{r.run_type.value.replace('_', ' ').title()} — {title}")
        self.d_badges.addWidget(StatusBadge(r.status.value, status_tone(r.status.value)))
        if r.model:
            self.d_badges.addWidget(StatusBadge(r.model, "violet"))
        self.d_badges.addStretch(1)
        self.plan_row.addWidget(label((r.workflow_plan or {}).get("rationale", ""), "muted", wrap=True))
        chain = QHBoxLayout()
        for s in (r.workflow_plan or {}).get("steps", []):
            done = next((x for x in r.steps if x["agent"] == s["agent"]), None)
            tone = status_tone(done["status"]) if done else "neutral"
            chain.addWidget(StatusBadge(TITLES.get(s["agent"], s["agent"]) + ("" if s["required"] else " (optional)"), tone))
        chain.addStretch(1)
        self.plan_row.addLayout(chain)
        items = []
        for s in r.steps:
            sub = "; ".join(f"{k}: {v}" for k, v in list(s.get("summary", {}).items())[:6] if not isinstance(v, (dict, list)))
            if s.get("warnings"):
                sub += "\n⚠ " + "\n⚠ ".join(s["warnings"][:3])
            if s.get("error"):
                sub += "\n✖ " + s["error"]
            items.append((f"{TITLES.get(s['agent'], s['agent'])} — {s['status']} · {s['seconds']}s" + (f" · {s['attempts']} attempts" if s.get("attempts", 1) > 1 else ""), sub, "", status_tone(s["status"])))
        self.steps.set_items(items, "No steps recorded.")
        rs = r.result_summary or {}
        lines = [f"Input: {r.input_summary or '—'}", f"Tokens: {r.prompt_tokens} prompt / {r.completion_tokens} completion"]
        if r.quality_score is not None:
            lines.append(f"Quality score: {r.quality_score:.2f}")
        if r.error:
            lines.append(f"Error: {r.error}")
        if rs.get("persisted"):
            p = rs["persisted"]
            lines.append(f"Saved: {p.get('obligations', 0)} obligations, {p.get('deadlines', 0)} deadlines, {p.get('clauses', 0)} clauses, {p.get('evidence', 0)} evidence, {p.get('review_cases', 0)} review cases")
        for i in rs.get("qa_issues", [])[:6]:
            lines.append(f"QA [{i['severity']}] {i['subject']}: {i['message']}")
        self.result.setText("\n".join(lines))


_ = (F, SPACE)
