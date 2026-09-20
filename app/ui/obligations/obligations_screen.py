"""Module 8 — Obligation Operations Center."""
from __future__ import annotations

from datetime import date
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QMenu, QScrollArea, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from app.models.enums import ObligationStatus
from app.services.obligations import ObligationRow
from app.ui.components.base import clear_layout, fmt_date, label, days_text
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog, TextPromptDialog
from app.ui.components.evidence_panel import EvidencePanel, EvidenceVM
from app.ui.components.polish import AccentCard, Avatar, FactTile, StatTile, initials
from app.ui.components.primitives import GlassPanel, NeonButton, SearchBar, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, status_tone, theme

VIEWS = [("all", "All"), ("pending", "Pending"), ("completed", "Completed"), ("overdue", "Overdue"), ("unassigned", "Unassigned"), ("recurring", "Recurring"),
         ("no_deadline", "No explicit deadline"), ("needs_review", "Needs review"), ("disputed", "Disputed")]
CHIP_VIEWS = ("all", "recurring", "no_deadline", "disputed")  # the KPI tiles already act as filters for the others
KPI_TILES = [("open", "Open", "obligations", "cyan", "pending"), ("overdue", "Overdue", "alert", "danger", "overdue"), ("unassigned", "Unassigned", "user", "warning", "unassigned"),
             ("review", "Needs review", "eye", "violet", "needs_review"), ("done", "Completed", "check", "success", "completed")]
GROUPS = [("none", "No grouping"), ("contract", "By contract"), ("party", "By responsible party"), ("owner", "By business owner"), ("category", "By category")]


class DependencyGraph(QWidget):
    """Layered diagram of obligations and their dependencies (event nodes shown for event-triggered deadlines)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._nodes: list[tuple[str, int, str]] = []  # title, layer, tone
        self._edges: list[tuple[int, int]] = []

    def set_data(self, rows: list[ObligationRow], deps: list[tuple[Any, Any]]) -> None:
        idx = {r.obligation.id: i for i, r in enumerate(rows)}
        layer = {r.obligation.id: 0 for r in rows}
        for _ in range(len(rows)):
            for a, b in deps:
                if a in layer and b in layer:
                    layer[a] = max(layer[a], layer[b] + 1)
        self._nodes = [(r.obligation.title, layer[r.obligation.id], status_tone(r.status_label)) for r in rows]
        self._edges = [(idx[b], idx[a]) for a, b in deps if a in idx and b in idx]
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        if not self._nodes:
            p.setPen(QColor(t.text_faint))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No dependencies recorded between obligations.")
            return
        layers = max(n[1] for n in self._nodes) + 1
        cols: dict[int, list[int]] = {}
        for i, n in enumerate(self._nodes):
            cols.setdefault(n[1], []).append(i)
        w = self.width() / layers
        pos: dict[int, QRectF] = {}
        for lay, ids in cols.items():
            h = self.height() / max(1, len(ids))
            for k, i in enumerate(ids):
                pos[i] = QRectF(lay * w + 10, k * h + 6, w - 44, min(h - 12, 44))
        p.setPen(QPen(QColor(t.border_hi), 1.4))
        for a, b in self._edges:
            if a in pos and b in pos:
                p.drawLine(QPointF(pos[a].right(), pos[a].center().y()), QPointF(pos[b].left(), pos[b].center().y()))
        for i, (title, _l, tone) in enumerate(self._nodes):
            if i not in pos:
                continue
            from app.ui.theme.tokens import tone_color

            c = QColor(tone_color(tone))
            p.setPen(QPen(c, 1.2))
            fill = QColor(c)
            fill.setAlphaF(0.12)
            p.setBrush(fill)
            p.drawRoundedRect(pos[i], 8, 8)
            p.setPen(QColor(t.text))
            p.drawText(pos[i].adjusted(8, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(pos[i].width() - 14)))
        p.end()


class ObligationsScreen(BaseScreen):
    nav_id = "obligations"
    title = "Obligation Operations"
    eyebrow = "Ledger"
    icon_name = "obligations"
    empty_title = "No obligations yet"
    empty_message = "Obligations appear after a contract has been analysed. Completion is always a human action with recorded evidence."

    def build(self) -> QWidget:
        self._view = "all"
        self._rows: list[ObligationRow] = []
        self._current: ObligationRow | None = None
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])
        kpis = QHBoxLayout()
        kpis.setSpacing(SPACE["md"])
        self._kpis: dict[str, StatTile] = {}
        for key, title, icon, tone, view in KPI_TILES:
            tile = StatTile(title, icon, tone)
            tile.clicked.connect(lambda v=view: self._set_view('all' if self._view == v else v))
            self._kpis[key] = tile
            kpis.addWidget(tile)
        lay.addLayout(kpis)
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["sm"])
        self._chips: dict[str, NeonButton] = {}
        for key, name in VIEWS:
            if key not in CHIP_VIEWS:
                continue
            b = NeonButton(name, "chip")
            b.setChecked(key == "all")
            b.clicked.connect(lambda _=False, k=key: self._set_view(k))
            self._chips[key] = b
            bar.addWidget(b)
        self.search = SearchBar("Search obligations, contracts or parties")
        self.search.queryChanged.connect(lambda _q: self._apply())
        self.group = QComboBox()
        for k, n in GROUPS:
            self.group.addItem(n, k)
        self.group.currentIndexChanged.connect(lambda _i: self._apply())
        export = NeonButton("Export CSV", "default", "download")
        export.clicked.connect(self._export)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.group)
        bar.addWidget(export)
        lay.addLayout(bar)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.table = DataTable([
            Column("Obligation", lambda r: r.obligation.title, stretch=True),
            Column("Contract", lambda r: r.contract_title, width=130),
            Column("Owner", lambda r: r.owner_name or "unassigned", width=92),
            Column("Due", lambda r: r.next_due, lambda v, r: fmt_date(v) if v else ("awaiting event" if r.deadline_kind and r.deadline_kind.value == "event_triggered" else "—"), width=104),
            Column("Status", lambda r: r.status_label, kind="badge", tone=lambda v, r: status_tone(v), width=104),
        ], empty_text="No obligations match this view.")
        self.table.rowSelected.connect(self._selected)
        left = GlassPanel()
        left.body.addWidget(self.table)
        split.addWidget(left)
        self.detail = self._build_detail()
        split.addWidget(self.detail)
        self.detail.setMinimumWidth(400)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([680, 470])
        lay.addWidget(split, 1)
        return root

    def _build_detail(self) -> QWidget:
        panel = GlassPanel(padding=SPACE["md"])
        self.detail_stack = QStackedWidget()
        panel.body.addWidget(self.detail_stack)

        # -- placeholder page
        ph = QWidget()
        pl = QVBoxLayout(ph)
        pl.addStretch(1)
        pl.addWidget(Avatar("obligations", size=56), 0, Qt.AlignmentFlag.AlignHCenter)
        self.d_title = label("Select an obligation", "h3", align=Qt.AlignmentFlag.AlignHCenter)
        pl.addWidget(self.d_title)
        pl.addWidget(label("See who owes what, when it is due and the contract text behind it. Completion is always recorded by a person, with evidence.", "muted", wrap=True, align=Qt.AlignmentFlag.AlignHCenter))
        pl.addStretch(2)
        self.detail_stack.addWidget(ph)

        # -- detail page (scrolls: the panel is narrow on small screens)
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 4, 10, 4)
        v.setSpacing(SPACE["md"])
        self.d_name = label("", "h3", wrap=True)
        self.d_badges = QHBoxLayout()
        self.d_badges.setSpacing(6)
        self.d_text = label("", "muted", wrap=True)
        v.addWidget(self.d_name)
        v.addLayout(self.d_badges)
        v.addWidget(self.d_text)
        self.d_grid = QGridLayout()
        self.d_grid.setHorizontalSpacing(SPACE["sm"])
        self.d_grid.setVerticalSpacing(SPACE["sm"])
        v.addLayout(self.d_grid)
        self.d_callouts = QVBoxLayout()
        self.d_callouts.setSpacing(SPACE["sm"])
        v.addLayout(self.d_callouts)

        own = QHBoxLayout()
        own.setSpacing(SPACE["sm"])
        self.d_avatar = Avatar(text="?", size=30, tone="violet")
        own.addWidget(self.d_avatar)
        own.addWidget(label("Business owner", "faint"))
        self.owner = QComboBox()
        self.owner.setMinimumWidth(150)
        self.owner.activated.connect(self._owner_changed)
        own.addWidget(self.owner, 1)
        v.addLayout(own)

        row = QHBoxLayout()
        row.setSpacing(SPACE["sm"])
        self.b_done = NeonButton("Record completion…", "success", "check")
        self.b_start = NeonButton("Start", "default", "play")
        more = NeonButton("More", "default", "dots")
        menu = QMenu(more)
        self.b_dispute = QAction("Flag dispute…", menu)
        self.b_exception = QAction("Create exception…", menu)
        self.b_review = QAction("Mark reviewed", menu)
        for a in (self.b_dispute, self.b_exception, self.b_review):
            menu.addAction(a)
        more.setMenu(menu)
        self.b_more = more
        for b in (self.b_done, self.b_start, more):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.b_start.clicked.connect(lambda: self._status(ObligationStatus.IN_PROGRESS))
        self.b_done.clicked.connect(self._complete)
        self.b_dispute.triggered.connect(self._dispute)
        self.b_exception.triggered.connect(self._exception)
        self.b_review.triggered.connect(self._reviewed)

        tabs = QTabWidget()
        tabs.setMinimumHeight(300)
        ev_w = QWidget()
        ev_l = QVBoxLayout(ev_w)
        self.evidence = EvidencePanel("Evidence")
        self.evidence.openRequested.connect(self.ctx.open_evidence)
        ev_l.addWidget(self.evidence)
        self.open_clause = NeonButton("Open original clause", "default", "external")
        self.open_clause.clicked.connect(self._open_clause)
        ev_l.addWidget(self.open_clause)
        notes_w = QWidget()
        n_l = QVBoxLayout(notes_w)
        self.notes_box = QVBoxLayout()
        self.notes_box.setSpacing(SPACE["sm"])
        n_l.addLayout(self.notes_box, 1)
        add = NeonButton("Add note…", "default", "plus")
        add.clicked.connect(self._note)
        n_l.addWidget(add)
        dep_w = QWidget()
        d_l = QVBoxLayout(dep_w)
        self.graph = DependencyGraph()
        d_l.addWidget(self.graph)
        self.dep_list = label("", "faint", wrap=True)
        d_l.addWidget(self.dep_list)
        add_dep = NeonButton("Add dependency…", "default", "link")
        add_dep.clicked.connect(self._add_dependency)
        d_l.addWidget(add_dep)
        tabs.addTab(ev_w, "Evidence")
        tabs.addTab(notes_w, "Notes and history")
        tabs.addTab(dep_w, "Dependencies")
        v.addWidget(tabs)
        page.setWidget(inner)
        self.detail_stack.addWidget(page)
        self._set_actions(False)
        return panel

    # ------------------------------------------------------------------ data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        rows = ws.obligations.list(view="all")
        deps = [(d.obligation_id, d.depends_on_obligation_id) for d in ws.repos.dependencies.list()]
        return {"rows": rows, "members": ws.obligations.members(), "deps": deps}

    def is_empty(self, data) -> bool:
        return not data["rows"]

    def render(self, data: dict[str, Any]) -> None:
        self._all = data["rows"]
        self._deps = data["deps"]
        self._members = data["members"]
        self._update_kpis()
        self.owner.clear()
        self.owner.addItem("— unassigned —", None)
        for uid, name in self._members:
            self.owner.addItem(name, uid)
        self._apply()

    def on_show(self, params: dict[str, Any] | None = None) -> None:
        if params and params.get("view") in dict(VIEWS):
            self._set_view(params["view"], reload=False)
        super().on_show(params)

    def _set_view(self, key: str, reload: bool = True) -> None:
        self._view = key
        for k, b in self._chips.items():
            b.setChecked(k == key)
        self._sync_tiles()
        if hasattr(self, "_all"):
            self._apply()

    def _update_kpis(self) -> None:
        rows = self._all
        open_ = [r for r in rows if r.obligation.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS)]
        self._kpis["open"].set_value(len(open_))
        self._kpis["overdue"].set_value(sum(1 for r in rows if r.overdue))
        self._kpis["unassigned"].set_value(sum(1 for r in open_ if r.obligation.owner_id is None))
        self._kpis["review"].set_value(sum(1 for r in rows if r.obligation.review_status.value == "needs_review"))
        self._kpis["done"].set_value(sum(1 for r in rows if r.obligation.status is ObligationStatus.COMPLETED))
        self._sync_tiles()

    def _sync_tiles(self) -> None:
        active = {view: key for key, _t, _i, _c, view in KPI_TILES}
        for key, tile in self._kpis.items():
            tile.set_active(active.get(self._view) == key)

    def _apply(self) -> None:
        rows = [r for r in getattr(self, "_all", []) if self._match(r)]
        g = self.group.currentData()
        if g != "none":
            key = {"contract": lambda r: r.contract_title, "party": lambda r: r.obligation.responsible_party_name or "", "owner": lambda r: r.owner_name or "unassigned",
                   "category": lambda r: r.obligation.category.value}[g]
            rows.sort(key=lambda r: (key(r).lower(), r.obligation.title))
        self._rows = rows
        self.table.set_rows(rows)
        self.table.view.setSortingEnabled(g == "none")
        self.table.set_empty_text("No obligations match this view.")

    def _match(self, r: ObligationRow) -> bool:
        q = self.search.text().strip().lower()
        if q and q not in f"{r.obligation.title} {r.obligation.description} {r.contract_title} {r.obligation.responsible_party_name or ''}".lower():
            return False
        o = r.obligation
        open_ = o.status in (ObligationStatus.PENDING, ObligationStatus.IN_PROGRESS)
        return {"all": True, "pending": open_, "completed": o.status is ObligationStatus.COMPLETED, "overdue": r.overdue, "unassigned": o.owner_id is None and open_,
                "recurring": o.is_recurring, "no_deadline": not r.has_explicit_deadline and open_, "needs_review": o.review_status.value == "needs_review",
                "disputed": o.status is ObligationStatus.DISPUTED}.get(self._view, True)

    # ------------------------------------------------------------------ detail
    def _set_actions(self, on: bool) -> None:
        for b in (self.b_start, self.b_done, self.b_more, self.b_dispute, self.b_exception, self.b_review, self.owner, self.open_clause):
            b.setEnabled(on)

    def _selected(self, r: ObligationRow | None) -> None:
        self._current = r
        clear_layout(self.d_badges)
        clear_layout(self.d_grid)
        clear_layout(self.d_callouts)
        if r is None:
            self.detail_stack.setCurrentIndex(0)
            self._set_actions(False)
            return
        o = r.obligation
        self.detail_stack.setCurrentIndex(1)
        self._set_actions(True)
        self.d_name.setText(o.title)
        self.d_badges.addWidget(StatusBadge(r.status_label, status_tone(r.status_label)))
        self.d_badges.addWidget(StatusBadge(o.category.value, "info"))
        self.d_badges.addWidget(StatusBadge(o.review_status.value, status_tone(o.review_status.value)))
        if o.dispute_flag:
            self.d_badges.addWidget(StatusBadge("dispute", "danger"))
        if o.is_recurring:
            self.d_badges.addWidget(StatusBadge("recurring", "violet"))
        self.d_badges.addStretch(1)
        self.d_text.setText(o.description)
        self.d_text.setVisible(bool(o.description))

        tiles: list[FactTile] = [
            FactTile("Contract", r.contract_title, "contract", "cyan"),
            FactTile("Parties", f"{o.responsible_party_name or '—'}  →  {o.beneficiary_name or '—'}", "user", "violet"),
        ]
        if r.next_due:
            days = (r.next_due - date.today()).days
            state = r.deadline_validation.value.replace("_", " ") if r.deadline_validation else ""
            tiles.append(FactTile("Next due", f"{fmt_date(r.next_due)} · {days_text(days)}" + (f"\n{state}" if state else ""), "calendar", "danger" if days < 0 else "warning" if days <= 14 else "success"))
        else:
            tiles.append(FactTile("Next due", "No calculated date yet", "calendar", "warning", dim=True))
        tiles.append(FactTile("Confidence", f"{o.confidence:.0%}", "shield_check", "success" if o.confidence >= 0.8 else "warning"))
        if o.trigger:
            tiles.append(FactTile("Trigger", o.trigger, "flag", "info"))
        if o.conditions:
            tiles.append(FactTile("Conditions", o.conditions, "filter", "info"))
        for i, t in enumerate(tiles):
            self.d_grid.addWidget(t, i // 2, i % 2)
        self.d_grid.setColumnStretch(0, 1)
        self.d_grid.setColumnStretch(1, 1)
        if o.deadline_text:
            c = AccentCard("cyan")
            c.body.addWidget(label("DEADLINE AS WRITTEN", "faint"))
            c.body.addWidget(label(f"“{o.deadline_text}”", "quote", wrap=True, selectable=True))
            self.d_callouts.addWidget(c)
        if o.uncertainty:
            c = AccentCard("warning")
            c.body.addWidget(label("UNCERTAINTY", "faint"))
            c.body.addWidget(label(o.uncertainty, "muted", wrap=True))
            self.d_callouts.addWidget(c)

        self.d_avatar.set_text(initials(r.owner_name) if r.owner_name else "?", "violet" if r.owner_name else "warning")
        i = self.owner.findData(o.owner_id)
        self.owner.setCurrentIndex(max(0, i))
        self.b_done.setEnabled(o.status is not ObligationStatus.COMPLETED)
        self.ctx.run(lambda: (self.ctx.ws.obligations.source(o.id), self.ctx.ws.obligations.notes(o.id)), lambda res, oid=o.id: self._detail_loaded(oid, res), name="obligation detail")
        deps = [(a, b) for a, b in self._deps if a == o.id or b == o.id]
        ids = {x for pair in deps for x in pair} | {o.id}
        self.graph.set_data([x for x in self._all if x.obligation.id in ids], deps)
        self.dep_list.setText("Blocked by: " + ", ".join(r.blocked_by) if r.blocked_by else "No upstream dependencies.")

    def _detail_loaded(self, oid, res) -> None:
        if not self._current or self._current.obligation.id != oid:
            return
        (clause, ev), notes = res
        self._clause = clause
        self.evidence.set_evidence([EvidenceVM.from_row(e, self._current.contract_title, "obligation") for e in ev])
        self.open_clause.setEnabled(bool(ev))
        clear_layout(self.notes_box)
        if not notes:
            self.notes_box.addWidget(label("No notes yet. Notes, completions, disputes and exceptions appear here.", "muted", wrap=True))
        for n in notes:
            tone = "info" if n.kind.value == "note" else "warning"
            card = AccentCard(tone)
            head = QHBoxLayout()
            head.addWidget(StatusBadge(n.kind.value, tone))
            head.addStretch(1)
            head.addWidget(label(n.created_at.strftime("%d %b %Y %H:%M"), "faint"))
            card.body.addLayout(head)
            card.body.addWidget(label(n.body, "muted", wrap=True, selectable=True))
            self.notes_box.addWidget(card)
        self.notes_box.addStretch(1)

    # ------------------------------------------------------------------ actions
    def _run(self, fn, ok: str) -> None:
        self.ctx.run(fn, lambda _r: (self.ctx.toast(ok, "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="obligation update")

    def _owner_changed(self, _i: int) -> None:
        if self._current:
            oid, uid = self._current.obligation.id, self.owner.currentData()
            self._run(lambda: self.ctx.ws.obligations.assign_owner(oid, uid), "Owner updated.")

    def _status(self, status: ObligationStatus) -> None:
        if self._current:
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.set_status(oid, status), f"Status set to {status.value.replace('_', ' ')}.")

    def _complete(self) -> None:
        if not self._current:
            return
        text = TextPromptDialog.ask(self, "Record completion", "Describe the evidence that this obligation was fulfilled (document reference, date, who confirmed). The AI never completes obligations on its own.",
                                    placeholder="e.g. Invoice INV-104 paid 2026-03-02, bank ref 88213", min_len=5, confirm_text="Mark completed")
        if text:
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.complete(oid, text), "Completion recorded.")

    def _dispute(self) -> None:
        if not self._current:
            return
        text = TextPromptDialog.ask(self, "Flag dispute", "Describe the disagreement about this obligation.", min_len=5, confirm_text="Flag dispute")
        if text:
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.flag_dispute(oid, text), "Dispute flagged.")

    def _exception(self) -> None:
        if not self._current:
            return
        text = TextPromptDialog.ask(self, "Create exception", "Record who approved excusing this obligation, and why. Open deadlines for it will be waived.", min_len=5, confirm_text="Create exception")
        if text and ConfirmationDialog.ask(self, "Confirm exception", "This waives the obligation and its open deadlines.", confirm_text="Waive obligation", danger=True):
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.create_exception(oid, text), "Exception recorded.")

    def _reviewed(self) -> None:
        if self._current:
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.mark_reviewed(oid, True), "Marked as reviewed.")

    def _note(self) -> None:
        if not self._current:
            return
        text = TextPromptDialog.ask(self, "Add note", "Notes are visible to your team.", min_len=1)
        if text:
            oid = self._current.obligation.id
            self._run(lambda: self.ctx.ws.obligations.add_note(oid, text), "Note added.")

    def _add_dependency(self) -> None:
        if not self._current:
            return
        from PyQt6.QtWidgets import QDialog, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Add dependency")
        form = QFormLayout(dlg)
        combo = QComboBox()
        for r in self._all:
            if r.obligation.id != self._current.obligation.id and r.obligation.contract_id == self._current.obligation.contract_id:
                combo.addItem(r.obligation.title, r.obligation.id)
        form.addRow("Depends on", combo)
        ok = NeonButton("Add", "primary")
        ok.clicked.connect(dlg.accept)
        form.addRow(ok)
        if combo.count() and dlg.exec() == QDialog.DialogCode.Accepted:
            oid, dep = self._current.obligation.id, combo.currentData()
            self._run(lambda: self.ctx.ws.obligations.add_dependency(oid, dep), "Dependency added.")

    def _open_clause(self) -> None:
        if self._current:
            r = self._current
            evs = self.evidence
            first = evs._list.itemAt(0).widget() if evs._list.count() > 1 else None  # noqa: SLF001
            vm = getattr(first, "vm", None)
            self.ctx.open_contract(r.obligation.contract_id, page=vm.page if vm else None, quote=vm.quote if vm else None, version_id=str(r.obligation.contract_version_id))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export obligations", "obligations.csv", "CSV (*.csv)")
        if path:
            rows = self._rows
            self.ctx.run(lambda: self.ctx.ws.obligations.export_csv(path, rows), lambda n: self.ctx.toast(f"Exported {n} obligations.", "success"), name="export csv")
