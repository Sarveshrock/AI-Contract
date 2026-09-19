"""Module 2 — Contract Intelligence: portfolio list, upload, and the five-pane contract workspace."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QProgressBar,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import DuplicateDocumentError
from app.models.enums import ContractStatus, DocumentRole, SubjectType
from app.services.contracts import ContractSummary, ContractTwin
from app.ui.components.base import clear_layout, elide, fmt_date, label
from app.ui.components.cards import ContractCard, ObligationCard
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog
from app.ui.components.evidence_panel import EvidencePanel, EvidenceVM
from app.ui.components.primitives import GlassPanel, NeonButton, SearchBar, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, severity_tone, status_tone, theme

ROLE_CHOICES = [("Amendment", DocumentRole.AMENDMENT), ("Addendum", DocumentRole.ADDENDUM), ("Schedule / exhibit", DocumentRole.SCHEDULE), ("Revised / restated version", DocumentRole.REVISED)]
FACT_LABELS = [("payment_terms", "Payment terms"), ("renewal", "Renewal terms"), ("termination", "Termination"), ("sla_summary", "Service levels"), ("governing_law", "Governing law")]
CLAUSE_GROUPS = ["payment", "renewal", "term", "termination", "sla", "confidentiality", "liability", "indemnity", "governing_law", "data_protection", "insurance", "audit", "assignment"]


class ContractsScreen(BaseScreen):
    nav_id = "contracts"
    title = "Contract Intelligence"
    eyebrow = "Workspace"
    icon_name = "contract"
    empty_title = "No contracts yet"
    empty_message = "Upload a PDF or DOCX. It is validated, parsed (OCR when needed), indexed and analysed with source evidence for every insight."

    def build(self) -> QWidget:
        self.stack = QStackedWidget()
        self._selected: UUID | None = None
        self._twin: ContractTwin | None = None
        self._pages: list[tuple[int, str]] = []
        self._page_idx = 0
        self._highlight: str | None = None
        self.setAcceptDrops(True)
        self.stack.addWidget(self._build_list())
        self.stack.addWidget(self._build_workspace())
        up = NeonButton("Upload contract", "primary", "upload")
        up.clicked.connect(self.pick_files)
        self.actions.addWidget(up)
        return self.stack

    # ------------------------------------------------------------------ list page
    def _build_list(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])
        bar = QHBoxLayout()
        self.search = SearchBar("Filter by title, party or type")
        self.search.queryChanged.connect(lambda _q: self._render_list())
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", None)
        for s in ContractStatus:
            self.status_filter.addItem(s.value.replace("_", " ").title(), s)
        self.status_filter.currentIndexChanged.connect(lambda _i: self._render_list())
        bar.addWidget(self.search, 1)
        bar.addWidget(self.status_filter)
        lay.addLayout(bar)
        self.progress_box = QVBoxLayout()
        self.progress_box.setSpacing(6)
        lay.addLayout(self.progress_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.cards = QVBoxLayout(host)
        self.cards.setSpacing(SPACE["sm"])
        self.cards.setContentsMargins(0, 0, 8, 0)
        self.cards.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        lay.addWidget(label("Tip: drag PDF or DOCX files anywhere on this screen to upload them.", "faint"))
        return page

    def fetch(self) -> list[ContractSummary]:
        return self.ctx.ws.contracts.list(include_archived=True)

    def is_empty(self, data) -> bool:
        return not data and self.stack.currentIndex() == 0

    def empty_action(self) -> str | None:
        return "Upload a contract"

    def on_empty_action(self) -> None:
        self.pick_files()

    def render(self, data: list[ContractSummary]) -> None:
        self._all = data
        self._render_list()

    def _render_list(self) -> None:
        clear_layout(self.cards)
        q = self.search.text().strip().lower()
        status = self.status_filter.currentData()
        rows = [s for s in getattr(self, "_all", []) if (not status or s.contract.status is status)
                and (not q or q in f"{s.contract.title} {' '.join(s.parties)} {s.contract.contract_type.value}".lower())]
        for s in rows:
            card = ContractCard(s)
            card.clicked.connect(self.open_contract)
            self.cards.addWidget(card)
        if not rows:
            self.cards.addWidget(label("No contracts match the current filter.", "muted"))
        self.cards.addStretch(1)

    def on_show(self, params: dict[str, Any] | None = None) -> None:
        super().on_show(params)
        if params:
            if params.get("upload"):
                self.pick_files()
            if params.get("contract_id"):
                self.open_contract(UUID(params["contract_id"]), page=params.get("page"), quote=params.get("quote"), version_id=params.get("version_id"), tab=params.get("tab"))
        elif self.stack.currentIndex() == 1 and self._stale:
            self.open_contract(self._selected)

    # ------------------------------------------------------------------ upload
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.toLocalFile()]
        self.upload(paths)

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Upload contracts", "", "Contracts (*.pdf *.docx)")
        if files:
            self.upload([Path(f) for f in files])

    def upload(self, paths: list[Path], *, contract_id: UUID | None = None, role: DocumentRole = DocumentRole.ORIGINAL, label_text: str | None = None) -> None:
        for path in paths:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            name = label(path.name, "muted")
            bar = QProgressBar()
            bar.setRange(0, 100)
            msg = label("Queued", "faint")
            h.addWidget(name, 1)
            h.addWidget(bar, 2)
            h.addWidget(msg, 1)
            self.progress_box.addWidget(row)
            self.stack.setCurrentIndex(0) if contract_id is None else None

            def work(task, p=path):
                return self.ctx.ws.contracts.upload(p, contract_id=contract_id, role=role, label=label_text, on_progress=task.progress,
                                                    on_event=lambda kind, payload: task.progress("analyze", 0.97, payload.get("message") or payload.get("agent", "")))

            def progress(stage, frac, message, b=bar, m=msg):
                b.setValue(int(frac * 100))
                m.setText(message or stage)

            def done(out, r=row, p=path):
                r.deleteLater()
                extra = f" {out.analysis_skipped_reason}" if out.analysis_skipped_reason else ""
                status = out.analysis.status.value.replace("_", " ") if out.analysis else "analysis not run"
                self.ctx.toast(f"{p.name}: indexed ({out.ingestion.chunk_count} passages, {out.ingestion.page_count} pages); {status}.{extra}",
                               "warning" if out.analysis is None or out.analysis.needs_review else "success")
                for w in out.ingestion.warnings[:2]:
                    self.ctx.toast(w, "info")
                self.ctx.invalidate_all()
                self.mark_stale()
                if self.stack.currentIndex() == 1:
                    self.open_contract(self._selected)
                else:
                    self.load()

            def failed(exc, r=row, p=path):
                r.deleteLater()
                if isinstance(exc, DuplicateDocumentError):
                    if ConfirmationDialog.ask(self, "Already uploaded", f"'{p.name}' is identical to a document that is already in this workspace.", confirm_text="Open existing contract"):
                        self.open_contract(UUID(exc.existing_contract_id))
                else:
                    self.ctx.error(exc)

            self.ctx.run(work, done, name=f"upload {path.name}", on_progress=progress, on_error=failed)

    # ------------------------------------------------------------------ workspace page
    def _build_workspace(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])
        top = QHBoxLayout()
        back = NeonButton("All contracts", "ghost", "chevron_right")
        back.clicked.connect(self._back)
        self.ws_title = label("", "h2")
        self.ws_badges = QHBoxLayout()
        top.addWidget(back)
        top.addWidget(self.ws_title, 1)
        top.addLayout(self.ws_badges)
        self.btn_approve = NeonButton("Approve analysis", "success", "check")
        self.btn_approve.clicked.connect(self._approve)
        self.btn_version = NeonButton("Add version…", "default", "layers")
        self.btn_version.clicked.connect(self._add_version)
        self.btn_rerun = NeonButton("Re-run", "default", "refresh")
        self.btn_rerun.setToolTip("Re-run the agent analysis on the current version")
        self.btn_rerun.clicked.connect(self._rerun)
        self.btn_delete = NeonButton("Delete", "danger", "trash")
        self.btn_delete.clicked.connect(self._delete)
        for b in (self.btn_approve, self.btn_version, self.btn_rerun, self.btn_delete):
            top.addWidget(b)
        lay.addLayout(top)

        self.meta_panel = GlassPanel("Contract metadata")
        self.meta_grid = QVBoxLayout()
        self.meta_panel.body.addLayout(self.meta_grid)
        self.meta_panel.setMinimumHeight(170)
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self.meta_panel)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        # clause explorer
        clause_panel = GlassPanel("Clause explorer", "Grouped by type")
        self.clause_tree = QTreeWidget()
        self.clause_tree.setHeaderHidden(True)
        self.clause_tree.itemSelectionChanged.connect(self._clause_selected)
        clause_panel.body.addWidget(self.clause_tree)
        clause_panel.setMinimumWidth(230)
        # viewer
        viewer = GlassPanel("Document viewer")
        vb = QHBoxLayout()
        vb2 = QHBoxLayout()
        self.version_combo = QComboBox()
        self.version_combo.setMinimumContentsLength(18)
        self.version_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.version_combo.currentIndexChanged.connect(self._version_changed)
        self.doc_search = SearchBar("Search in document", debounce_ms=350)
        self.doc_search.queryChanged.connect(self._doc_search)
        self.page_label = label("", "faint")
        prev, nxt = NeonButton("‹", "ghost"), NeonButton("›", "ghost")
        prev.setToolTip("Previous page")
        nxt.setToolTip("Next page")
        prev.clicked.connect(lambda: self._goto(self._page_idx - 1))
        nxt.clicked.connect(lambda: self._goto(self._page_idx + 1))
        vb.addWidget(self.version_combo, 1)
        vb.addWidget(prev)
        vb.addWidget(self.page_label)
        vb.addWidget(nxt)
        viewer.body.addLayout(vb)
        vb2.addWidget(self.doc_search, 1)
        viewer.body.addLayout(vb2)
        self.doc_view = QTextEdit()
        self.doc_view.setReadOnly(True)
        self.doc_view.setAccessibleName("Contract text")
        self.doc_view.setStyleSheet(f"QTextEdit {{ font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12.5px; line-height: 150%; padding: 14px; }}")
        viewer.body.addWidget(self.doc_view, 1)
        self.search_hits = label("", "faint")
        viewer.body.addWidget(self.search_hits)
        # insights
        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_insights, self.tab_obl, self.tab_dl, self.tab_ver, self.tab_ask = (QWidget() for _ in range(5))
        for w, name in ((self.tab_insights, "AI insights"), (self.tab_obl, "Obligations"), (self.tab_dl, "Deadlines"), (self.tab_ver, "Versions"), (self.tab_ask, "Ask")):
            self.tabs.addTab(w, name)
        self.insights_host = QWidget()
        self.insights_lay = QVBoxLayout(self.insights_host)
        ins_scroll = QScrollArea()
        ins_scroll.setWidgetResizable(True)
        ins_scroll.setWidget(self.insights_host)
        QVBoxLayout(self.tab_insights).addWidget(ins_scroll)
        self.obl_lay = QVBoxLayout(self.tab_obl)
        self.dl_table = DataTable([
            Column("Deadline", lambda d: d.label, stretch=True),
            Column("Due", lambda d: d.due_date, lambda v, r: fmt_date(v), width=100),
            Column("Kind", lambda d: d.kind.value, kind="badge", tone=lambda v, r: "info", width=110),
            Column("Status", lambda d: d.validation_status.value, kind="badge", tone=lambda v, r: status_tone(v), width=120),
        ], empty_text="No deadlines were derived for this contract.")
        self.dl_table.rowSelected.connect(self._deadline_selected)
        dl_lay = QVBoxLayout(self.tab_dl)
        dl_lay.addWidget(self.dl_table)
        self.dl_trace = QPlainTextEdit()
        self.dl_trace.setReadOnly(True)
        self.dl_trace.setPlaceholderText("Select a deadline to see its calculation trace, anchor, assumptions and source clause.")
        self.dl_trace.setMaximumHeight(150)
        dl_lay.addWidget(self.dl_trace)
        self.ver_host = QWidget()
        self.ver_lay = QVBoxLayout(self.ver_host)
        ver_scroll = QScrollArea()
        ver_scroll.setWidgetResizable(True)
        ver_scroll.setWidget(self.ver_host)
        QVBoxLayout(self.tab_ver).addWidget(ver_scroll)
        ask_lay = QVBoxLayout(self.tab_ask)
        self.ask_box = SearchBar("Ask a question about this contract…")
        self.ask_box.setProperty("search", True)
        self.ask_box.submitted.connect(self._ask)
        self.ask_out = QPlainTextEdit()
        self.ask_out.setReadOnly(True)
        ask_lay.addWidget(self.ask_box)
        ask_lay.addWidget(self.ask_out, 1)
        self.evidence = EvidencePanel("Source evidence")
        self.evidence.openRequested.connect(self._open_source)
        ev_panel = GlassPanel()
        ev_panel.body.addWidget(self.evidence)
        ins_panel = GlassPanel("AI insights")
        ins_panel.body.addWidget(self.tabs)
        right.addWidget(ins_panel)
        right.addWidget(ev_panel)
        right.setSizes([520, 300])
        split.addWidget(clause_panel)
        split.addWidget(viewer)
        split.addWidget(right)
        clause_panel.setMinimumWidth(200)
        right.setMinimumWidth(430)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 4)
        split.setSizes([230, 470, 440])
        vsplit.addWidget(split)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([185, 480])
        lay.addWidget(vsplit, 1)
        return page

    def _back(self) -> None:
        self.header.setVisible(True)
        self.stack.setCurrentIndex(0)
        self._selected = None
        self.mark_stale()
        self.load()

    def open_contract(self, contract_id, *, page: int | None = None, quote: str | None = None, version_id: str | None = None, tab: str | None = None) -> None:
        if contract_id is None:
            return
        self._selected = UUID(str(contract_id))
        self.header.setVisible(False)  # the workspace has its own title row; reclaim the vertical space
        self.stack.setCurrentIndex(1)
        self.view.show_content()
        self._pending_focus = (page, quote, version_id, tab)
        cid = self._selected

        def work():
            twin = self.ctx.ws.contracts.twin(cid)
            ver_id = UUID(version_id) if version_id else (twin.contract.current_version_id or (twin.versions[-1].id if twin.versions else None))
            pages = self.ctx.ws.contracts.document_pages(ver_id) if ver_id else []
            return twin, ver_id, pages

        self.ctx.run(work, self._workspace_loaded, name="open contract")

    def _workspace_loaded(self, res) -> None:
        twin, ver_id, pages = res
        self._twin, self._pages, self._version_id = twin, pages, ver_id
        self._stale = False
        c = twin.contract
        self.ws_title.setText(elide(c.title, 70))
        self.ws_title.setToolTip(c.title)
        clear_layout(self.ws_badges)
        self.ws_badges.addWidget(StatusBadge(c.status.value, status_tone(c.status.value)))
        self.ws_badges.addWidget(StatusBadge(c.contract_type.value, "info"))
        self.ws_badges.addWidget(StatusBadge(f"analysis {c.analysis_status.value}", status_tone(c.analysis_status.value)))
        if c.is_demo:
            self.ws_badges.addWidget(StatusBadge("demo", "violet"))
        self.btn_approve.setVisible(c.status in (ContractStatus.DRAFT, ContractStatus.IN_REVIEW))
        self._fill_meta(twin)
        self._fill_versions(twin)
        self._fill_clauses(twin)
        self._fill_insights(twin)
        self._fill_obligations(twin)
        self.dl_table.set_rows(twin.deadlines)
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for v in twin.versions:
            self.version_combo.addItem(f"v{v.version_number} · {v.label} ({v.document_role.value})", v.id)
        self.version_combo.setCurrentIndex(max(0, next((i for i, v in enumerate(twin.versions) if v.id == ver_id), 0)))
        self.version_combo.blockSignals(False)
        page, quote, _v, tab = getattr(self, "_pending_focus", (None, None, None, None))
        self._highlight = quote
        idx = next((i for i, (n, _t) in enumerate(pages) if n == page), 0) if page else 0
        self._goto(idx)
        if tab:
            names = {"insights": 0, "obligations": 1, "deadlines": 2, "versions": 3, "ask": 4}
            self.tabs.setCurrentIndex(names.get(tab, 0))
        self.evidence.clear()

    # -- metadata & panels ------------------------------------------------------------------
    def _fill_meta(self, twin: ContractTwin) -> None:
        clear_layout(self.meta_grid)
        c = twin.contract
        ver = twin.current_version()
        snap = (ver.extraction if ver else {}) or {}
        parties = ", ".join(f"{p.name} ({cp.role.value.replace('_', ' ')})" for p, cp in twin.parties) or "Not identified"
        cur = twin.current_version()
        clauses = {cl.clause_type.value: cl for cl in twin.clauses if cur is None or cl.contract_version_id == cur.id}
        renewal = ("Auto-renews" if c.auto_renews else "No auto-renewal stated" if c.auto_renews is False else "Renewal not stated") + \
                  (f" · {c.renewal_term_months}-month terms" if c.renewal_term_months else "") + (f" · {c.renewal_notice_days} days' notice" if c.renewal_notice_days else "")
        term_cl = clauses.get("termination")
        rows = [
            ("Parties", parties), ("Effective date", fmt_date(c.effective_date)), ("Expiration date", fmt_date(c.expiration_date) + ("" if c.expiration_date else " (not established)")),
            ("Renewal", renewal), ("Payment terms", c.payment_terms or "Not found"), ("Termination", term_cl.summary if term_cl and term_cl.summary else "Not found"),
            ("Service levels", snap.get("sla_summary") or "Not found"), ("Confidentiality", (clauses.get("confidentiality").summary if clauses.get("confidentiality") else None) or "Not found"),
            ("Liability / indemnity", "; ".join(x.summary for k in ("liability", "indemnity") if (x := clauses.get(k)) and x.summary) or "Not found"),
            ("Governing law", c.governing_law or "Not found"),
        ]
        missing = [f.title for f in twin.findings if f.finding_type.value == "missing_information" and f.status.value == "open"]
        missing += list(snap.get("missing_information", []))
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for i, (k, v) in enumerate(rows):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(label(k.upper(), "faint"))
            val = label(elide(v, 34), "muted" if "Not " in v[:4] else None)
            val.setToolTip(v)
            cell.addWidget(val)
            (row1 if i < 5 else row2).addLayout(cell, 1)
        self.meta_grid.addLayout(row1)
        self.meta_grid.addLayout(row2)
        if missing:
            self.meta_grid.addWidget(label("Missing or unresolved: " + "; ".join(dict.fromkeys(missing))[:300], "faint", wrap=True))

    def _fill_clauses(self, twin: ContractTwin) -> None:
        self.clause_tree.clear()
        groups: dict[str, list] = {}
        shown = getattr(self, "_version_id", None)
        for cl in (c for c in twin.clauses if shown is None or c.contract_version_id == shown):
            groups.setdefault(cl.clause_type.value, []).append(cl)
        for g in [x for x in CLAUSE_GROUPS if x in groups] + [x for x in groups if x not in CLAUSE_GROUPS]:
            top = QTreeWidgetItem([f"{g.replace('_', ' ').title()}  ({len(groups[g])})"])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for cl in groups[g]:
                it = QTreeWidgetItem([f"§{cl.section_reference or '-'}  {cl.heading or ''}"])
                it.setData(0, Qt.ItemDataRole.UserRole, cl)
                it.setToolTip(0, cl.summary or cl.text)
                top.addChild(it)
            self.clause_tree.addTopLevelItem(top)
            top.setExpanded(True)

    def _clause_selected(self) -> None:
        items = self.clause_tree.selectedItems()
        if not items or not self._twin:
            return
        cl = items[0].data(0, Qt.ItemDataRole.UserRole)
        if cl is None:
            return
        ev = [EvidenceVM.from_row(e, self._twin.contract.title, f"{cl.clause_type.value} clause") for e in self._twin.evidence(SubjectType.CLAUSE, cl.id)]
        self.evidence.set_evidence(ev)
        if cl.page_number:
            self._highlight = ev[0].quote if ev else None
            idx = next((i for i, (n, _t) in enumerate(self._pages) if n == cl.page_number), self._page_idx)
            self._goto(idx)

    def _fill_insights(self, twin: ContractTwin) -> None:
        clear_layout(self.insights_lay)
        c = twin.contract
        if c.summary:
            self.insights_lay.addWidget(label(c.summary, "muted", wrap=True))
        self.insights_lay.addWidget(label("REVIEW SIGNALS", "faint"))
        active = [f for f in twin.findings if f.status.value in ("open", "acknowledged")]
        if not active:
            self.insights_lay.addWidget(label("No open review signals. AI signals are prompts for human review, not legal conclusions.", "muted", wrap=True))
        for f in sorted(active, key=lambda f: -f.weight)[:14]:
            row = QHBoxLayout()
            row.addWidget(StatusBadge(f.severity.value, severity_tone(f.severity.value)))
            row.addWidget(StatusBadge("uncertainty" if f.signal_category.value == "extraction_uncertainty" else "risk", "violet" if f.signal_category.value == "extraction_uncertainty" else "warning"))
            t = label(elide(f.title, 60), None, wrap=True)
            t.setToolTip(f.description)
            row.addWidget(t, 1)
            if any(True for _ in twin.evidence(SubjectType.FINDING, f.id)):
                b = NeonButton("Evidence", "ghost", "quote")
                b.clicked.connect(lambda _=False, fid=f.id: self.evidence.set_evidence([EvidenceVM.from_row(e, twin.contract.title, "review signal") for e in twin.evidence(SubjectType.FINDING, fid)]))
                row.addWidget(b)
            self.insights_lay.addLayout(row)
        self.insights_lay.addWidget(label(f"Business review score {c.business_risk_score or 0:.0f}/100 · extraction uncertainty {c.extraction_uncertainty_score or 0:.0f}/100", "faint"))
        self.insights_lay.addStretch(1)

    def _fill_obligations(self, twin: ContractTwin) -> None:
        clear_layout(self.obl_lay)
        from app.services.obligations import ObligationRow

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setSpacing(6)
        rows = self.ctx.ws.obligations.list(contract_id=twin.contract.id)
        for r in rows:
            card = ObligationCard(r)
            card.clicked.connect(lambda oid, t=twin: self.evidence.set_evidence([EvidenceVM.from_row(e, t.contract.title, "obligation") for e in t.evidence(SubjectType.OBLIGATION, oid)]))
            lay.addWidget(card)
        if not rows:
            lay.addWidget(label("No obligations were extracted for this contract.", "muted"))
        lay.addStretch(1)
        scroll.setWidget(host)
        self.obl_lay.addWidget(scroll)
        _ = ObligationRow

    def _fill_versions(self, twin: ContractTwin) -> None:
        clear_layout(self.ver_lay)
        for v in twin.versions:
            row = QHBoxLayout()
            row.addWidget(StatusBadge("current" if v.id == twin.contract.current_version_id else v.document_role.value, "success" if v.id == twin.contract.current_version_id else "neutral"))
            row.addWidget(label(f"v{v.version_number} · {v.label}", None), 1)
            self.ver_lay.addLayout(row)
        for am in twin.amendments:
            self.ver_lay.addWidget(label(f"Comparison: {am.summary or ''}", "muted", wrap=True))
            for ch in am.changes[:14]:
                text = ch.get("summary") or ch.get("label") or ""
                tone = {"added": "success", "removed": "danger", "modified": "warning"}.get(ch.get("change_type", ""), "info")
                r = QHBoxLayout()
                r.addWidget(StatusBadge(ch.get("change_type") or ch.get("kind", ""), tone))
                r.addWidget(label(elide(text, 110), "muted", wrap=True), 1)
                self.ver_lay.addLayout(r)
        if len(twin.versions) >= 2:
            b = NeonButton("Compare latest two versions", "default", "layers")
            b.clicked.connect(self._compare)
            self.ver_lay.addWidget(b)
        self.ver_lay.addStretch(1)

    # -- viewer ------------------------------------------------------------------------------
    def _goto(self, idx: int) -> None:
        if not self._pages:
            self.doc_view.setPlainText("No indexed text is available for this version.")
            self.page_label.setText("")
            return
        self._page_idx = max(0, min(idx, len(self._pages) - 1))
        num, text = self._pages[self._page_idx]
        self.doc_view.setPlainText(text)
        self.page_label.setText(f"page {num} / {self._pages[-1][0]}")
        if self._highlight:
            self._mark(self._highlight)

    def _mark(self, needle: str) -> None:
        doc = self.doc_view.document()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(34, 211, 238, 90))
        fmt.setForeground(QColor(theme().text))
        first = " ".join(needle.split())[:60]
        cur = doc.find(first, 0, QTextDocument.FindFlag(0))
        if cur.isNull():
            cur = doc.find(first.split(" ")[0] + " " + " ".join(first.split(" ")[1:3]), 0)
        if not cur.isNull():
            cur.select(QTextCursor.SelectionType.LineUnderCursor) if False else None
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = fmt
            # extend to the full quote where possible
            end = doc.find(" ".join(needle.split())[-40:], cur.position())
            if not end.isNull():
                cur.setPosition(end.position(), QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cur
            self.doc_view.setExtraSelections([sel])
            self.doc_view.setTextCursor(cur)
            self.doc_view.ensureCursorVisible()

    def _version_changed(self, _i: int) -> None:
        vid = self.version_combo.currentData()
        if vid and vid != getattr(self, "_version_id", None):
            self._pending_focus = (None, None, str(vid), None)
            self.ctx.run(lambda: self.ctx.ws.contracts.document_pages(vid), lambda pages: (setattr(self, "_pages", pages), setattr(self, "_version_id", vid), self._goto(0)), name="load version")

    def _doc_search(self, q: str) -> None:
        if not q or not self._version_id:
            self.search_hits.setText("")
            return
        self.ctx.run(lambda: self.ctx.ws.contracts.search_in_document(self._version_id, q), lambda hits: self._show_hits(q, hits), name="search document")

    def _show_hits(self, q: str, hits) -> None:
        self.search_hits.setText(f"{len(hits)} match(es) — first on page {hits[0][0]}" if hits else "No matches in this document.")
        if hits:
            self._highlight = q
            self._goto(next((i for i, (n, _t) in enumerate(self._pages) if n == hits[0][0]), 0))

    def _open_source(self, vm: EvidenceVM) -> None:
        self._highlight = vm.quote
        idx = next((i for i, (n, _t) in enumerate(self._pages) if n == vm.page), self._page_idx)
        self._goto(idx)

    def _deadline_selected(self, d) -> None:
        if d is None or not self._twin:
            return
        lines = [f"{d.label} — {fmt_date(d.due_date)} [{d.validation_status.value}]", f"Kind: {d.kind.value}   Anchor: {d.anchor_event or '—'} = {fmt_date(d.anchor_date)}", "", "Calculation:"]
        lines += [f"  {i}. {t}" for i, t in enumerate(d.calculation_trace, 1)]
        if d.assumptions:
            lines += ["", "Assumptions:"] + [f"  • {a}" for a in d.assumptions]
        if d.missing_anchors:
            lines += ["", f"Missing: {', '.join(map(str, d.missing_anchors))}"]
        self.dl_trace.setPlainText("\n".join(lines))
        self.evidence.set_evidence([EvidenceVM.from_row(e, self._twin.contract.title, "deadline source") for e in self._twin.evidence(SubjectType.DEADLINE, d.id)],
                                   "This deadline has no direct quote (for example it is derived from several terms). See the calculation trace.")

    # -- actions -----------------------------------------------------------------------------
    def _ask(self, q: str) -> None:
        if not q or self._selected is None:
            return
        cp = self.ctx.ws.copilot
        if cp is None:
            self.ask_out.setPlainText(self.ctx.ws.index_error or "Search index unavailable.")
            return
        self.ask_out.setPlainText("Thinking…")
        cid = self._selected

        def show(ans):
            text = ans.answer + ("\n\nUncertainty: " + ans.uncertainty if ans.uncertainty else "")
            text += "".join(f"\n\n[{s.label}] {s.contract_title} · §{s.section_reference or '-'} · p.{s.page_number}\n“{s.excerpt[:280]}”" for s in ans.sources[:5])
            self.ask_out.setPlainText(text)

        self.ctx.run(lambda: cp.ask(q, contract_id=cid), show, name="analysis question")

    def _approve(self) -> None:
        if self._selected and ConfirmationDialog.ask(self, "Approve analysis", "You confirm that you reviewed the extracted terms, obligations and deadlines for this contract. It will become Active. Individual items keep their own review state.",
                                                     confirm_text="Approve", verify_text="I have reviewed the AI-extracted terms against the source document."):
            cid = self._selected
            self.ctx.run(lambda: self.ctx.ws.contracts.approve(cid), lambda _c: (self.ctx.toast("Contract approved.", "success"), self.ctx.invalidate_all(), self.open_contract(cid)), name="approve")

    def _delete(self) -> None:
        if self._selected and ConfirmationDialog.ask(self, "Delete contract", "The contract is archived and hidden immediately, and permanently removed after the retention period.", confirm_text="Delete", danger=True):
            cid = self._selected
            self.ctx.run(lambda: self.ctx.ws.contracts.delete(cid), lambda _x: (self.ctx.toast("Contract deleted.", "success"), self.ctx.invalidate_all(), self._back()), name="delete")

    def _rerun(self) -> None:
        if not self._selected or not self._twin:
            return
        if not self.ctx.ws.llm.available:
            self.ctx.toast("AI is not configured. Set OPENAI_API_KEY in .env to run analysis.", "warning")
            return
        cid, vid = self._selected, self._twin.contract.current_version_id
        self.ctx.toast("Analysis started. Agents are working…", "info")
        self.ctx.run(lambda: self.ctx.ws.analysis.analyze_version(cid, vid), lambda out: (self.ctx.toast(f"Analysis {out.status.value.replace('_', ' ')}." + (f" {out.error}" if out.error else ""), "success" if not out.error else "danger"),
                     self.ctx.invalidate_all(), self.open_contract(cid)), name="analysis rerun")

    def _compare(self) -> None:
        if not self._twin or len(self._twin.versions) < 2:
            return
        v = self._twin.versions
        cid = self._twin.contract.id
        self.ctx.run(lambda: self.ctx.ws.analysis.compare_versions(cid, v[-2].id, v[-1].id), lambda o: (self.ctx.toast("Comparison complete.", "success"), self.open_contract(cid, tab="versions")), name="compare versions")

    def _add_version(self) -> None:
        if not self._selected:
            return
        from PyQt6.QtWidgets import QDialog, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Add document")
        form = QFormLayout(dlg)
        role = QComboBox()
        for name, r in ROLE_CHOICES:
            role.addItem(name, r)
        form.addRow("This document is a", role)
        ok = NeonButton("Choose file…", "primary")
        ok.clicked.connect(dlg.accept)
        form.addRow(ok)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Add document", "", "Contracts (*.pdf *.docx)")
        if f:
            r = role.currentData()
            self.upload([Path(f)], contract_id=self._selected, role=r, label_text=role.currentText())


_ = (QColor,)
