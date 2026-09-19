"""Module 10 — Risk Observatory: portfolio signals, contract profiles, human review workbench, scoring configuration."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget

from app.config.risk import SIGNALS, RiskConfig
from app.core.errors import ContractLensError
from app.models.enums import ReviewDecision, SubjectType
from app.ui.components.base import clear_layout, elide, label
from app.ui.components.charts import Heatmap, RiskMatrix, TrendChart
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog
from app.ui.components.evidence_panel import EvidenceVM
from app.ui.components.primitives import GlassPanel, MetricCard, NeonButton, StatusBadge
from app.ui.components.review_panel import ReviewPanel
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, severity_tone

DISCLAIMER = "Signals and scores prioritise human review. They are not legal advice or conclusions about validity or compliance. Extraction uncertainty measures how much AI output still needs checking; it is not contract risk."


class RiskObservatoryScreen(BaseScreen):
    nav_id = "risk"
    title = "Risk Observatory"
    eyebrow = "Review signals"
    icon_name = "risk"
    empty_title = "No risk data yet"
    empty_message = "Review signals appear after contracts are analysed."

    def build(self) -> QWidget:
        self.tabs = QTabWidget()
        self._tab_index = {"portfolio": 0, "profile": 1, "review": 2, "scoring": 3}
        self.tabs.addTab(self._portfolio_tab(), "Portfolio")
        self.tabs.addTab(self._profile_tab(), "Contract profile")
        self.tabs.addTab(self._review_tab(), "Review workbench")
        self.tabs.addTab(self._scoring_tab(), "Scoring configuration")
        self._selected_contract = None
        return self.tabs

    # ------------------------------------------------------------------ portfolio
    def _portfolio_tab(self) -> QWidget:
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(SPACE["md"])
        krow = QHBoxLayout()
        self.k_high = MetricCard("High-severity open", "risk", "danger")
        self.k_review = MetricCard("Review queue", "eye", "warning")
        self.k_avg = MetricCard("Avg business score", "shield_check", "violet")
        self.k_unc = MetricCard("Avg extraction uncertainty", "info", "info")
        for k in (self.k_high, self.k_review, self.k_avg, self.k_unc):
            krow.addWidget(k, 1)
        lay.addLayout(krow)
        lay.addWidget(label(DISCLAIMER, "faint", wrap=True))
        row = QHBoxLayout()
        self.matrix = RiskMatrix()
        p1 = GlassPanel("Risk matrix", "Open findings by severity and signal category")
        p1.body.addWidget(self.matrix)
        self.heat = Heatmap()
        p2 = GlassPanel("Portfolio heatmap", "Summed signal weight by contract and finding type")
        p2.body.addWidget(self.heat)
        self.trend = TrendChart()
        p3 = GlassPanel("Risk trend", "Scores recorded at each analysis run")
        p3.body.addWidget(self.trend)
        row.addWidget(p1, 2)
        row.addWidget(p2, 3)
        row.addWidget(p3, 3)
        lay.addLayout(row, 1)
        self.contract_table = DataTable([
            Column("Contract", lambda r: r["contract"].title, stretch=True),
            Column("Business review score", lambda r: r["business"], kind="progress", width=200),
            Column("Extraction uncertainty", lambda r: r["extraction"], kind="progress", width=200),
            Column("Band", lambda r: r["band"], kind="badge", tone=lambda v, r: {"high": "danger", "medium": "warning"}.get(v, "success"), width=90),
            Column("Top contributing signals", lambda r: "; ".join(r["top"]), stretch=True),
        ], empty_text="No scored contracts.")
        self.contract_table.rowActivated.connect(self._open_profile)
        p4 = GlassPanel("Contract risk profiles", "Double-click a row for the explanation of its score")
        p4.body.addWidget(self.contract_table)
        p4.setMinimumHeight(260)
        lay.addWidget(p4)
        for p in (p1, p2, p3):
            p.setMinimumHeight(290)
        w = QScrollArea()
        w.setWidgetResizable(True)
        w.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w.setWidget(inner)
        return w

    def _profile_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.prof_title = label("Select a contract on the Portfolio tab", "h2")
        self.prof_scores = label("", "muted", wrap=True)
        self.prof_table = DataTable([
            Column("Weight", lambda c: c["weight"], lambda v, r: f"+{v:.0f}", kind="number", width=70),
            Column("Category", lambda c: c["category"], kind="badge", tone=lambda v, r: "violet" if v == "extraction_uncertainty" else "warning", width=170),
            Column("Severity", lambda c: c["severity"], kind="badge", tone=lambda v, r: severity_tone(v), width=100),
            Column("Signal", lambda c: c["title"], stretch=True),
            Column("Why it matters", lambda c: c["description"], stretch=True),
        ], empty_text="No active signals.")
        lay.addWidget(self.prof_title)
        lay.addWidget(self.prof_scores)
        lay.addWidget(self.prof_table, 1)
        lay.addWidget(label(DISCLAIMER, "faint", wrap=True))
        return w

    def _open_profile(self, row) -> None:
        cid = row["contract"].id
        self.prof_title.setText(row["contract"].title)
        self.ctx.run(lambda: self.ctx.ws.risk.explain(cid), self._profile_loaded, name="risk explain")

    def _profile_loaded(self, exp: dict[str, Any]) -> None:
        self.prof_scores.setText(f"Business review score {exp['business']:.0f}/100 · Extraction uncertainty {exp['extraction']:.0f}/100 · band {exp['band']}"
                                 + (" · human review recommended" if exp["review_recommended"] else ""))
        self.prof_table.set_rows(exp["contributions"])
        self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------ review workbench
    def _review_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.queue = DataTable([
            Column("Priority", lambda c: c.priority.value, kind="badge", tone=lambda v, r: severity_tone(v), width=90),
            Column("Case", lambda c: c.title, stretch=True),
            Column("Type", lambda c: c.subject_type.value, kind="badge", tone=lambda v, r: "info", width=110),
            Column("Status", lambda c: c.status.value, kind="badge", tone=lambda v, r: "warning" if v == "open" else "cyan", width=100),
        ], empty_text="The review queue is empty.")
        self.queue.rowSelected.connect(self._case_selected)
        qp = GlassPanel("Review queue", "Highest priority first")
        qp.body.addWidget(self.queue)
        split.addWidget(qp)
        self.review = ReviewPanel()
        self.review.decided.connect(self._decide)
        self.review.dismissed.connect(self._dismiss)
        self.review.openEvidence.connect(self.ctx.open_evidence)
        split.addWidget(self.review)
        split.setSizes([520, 640])
        lay.addWidget(split)
        return w

    def _case_selected(self, case) -> None:
        if case is None:
            self.review.show_case(None)
            return

        def load():
            ws = self.ctx.ws
            ev = []
            title = next((c.title for c in ws.repos.contracts.list() if c.id == case.contract_id), "")
            if case.subject_id and case.subject_type in (SubjectType.OBLIGATION, SubjectType.CLAUSE, SubjectType.DEADLINE, SubjectType.FINDING, SubjectType.PARTY):
                ev = [EvidenceVM.from_row(e, title, case.subject_type.value) for e in ws.repos.evidence.list([__import__("app.database.store", fromlist=["F"]).F.eq("subject_id", str(case.subject_id))])]
            return ev

        self.ctx.run(load, lambda ev, c=case: self.review.show_case(c, ev), name="review evidence")

    def _decide(self, decision: ReviewDecision, notes: str, correction: dict) -> None:
        case = self.review.case
        if not case:
            return
        if decision is ReviewDecision.APPROVED and case.proposed_change and not ConfirmationDialog.ask(
                self, "Apply proposed change", "Approving applies this change to the contract record and recalculates dependent deadlines.", confirm_text="Approve and apply",
                verify_text="I verified the change against the signed amendment."):
            return
        cid = case.id
        self.ctx.run(lambda: self.ctx.ws.reviews.decide(cid, decision, notes, correction),
                     lambda _c: (self.ctx.toast(f"Decision recorded: {decision.value}.", "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="review decide")

    def _dismiss(self, reason: str) -> None:
        case = self.review.case
        if case:
            cid = case.id
            self.ctx.run(lambda: self.ctx.ws.reviews.dismiss(cid, reason), lambda _c: (self.ctx.toast("Case dismissed.", "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="review dismiss")

    # ------------------------------------------------------------------ scoring config
    def _scoring_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(label("Weights and enabled signals are stored per workspace and re-score all contracts. Only users with the playbook permission can save.", "muted", wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setHorizontalSpacing(14)
        self._cfg_widgets: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        for i, (key, sig) in enumerate(SIGNALS.items()):
            cb = QCheckBox(sig.title)
            cb.setChecked(True)
            sp = QDoubleSpinBox()
            sp.setRange(0, 50)
            sp.setDecimals(1)
            sp.setValue(sig.weight)
            self.grid.addWidget(cb, i, 0)
            self.grid.addWidget(StatusBadge(sig.category.value.replace("_", " "), "violet" if sig.category.value == "extraction_uncertainty" else "warning"), i, 1)
            self.grid.addWidget(sp, i, 2)
            self._cfg_widgets[key] = (cb, sp)
        self.grid.setRowStretch(len(SIGNALS), 1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        save = NeonButton("Save and re-score", "primary", "check")
        save.clicked.connect(self._save_cfg)
        lay.addWidget(save, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def _save_cfg(self) -> None:
        weights = {k: sp.value() for k, (cb, sp) in self._cfg_widgets.items() if abs(sp.value() - SIGNALS[k].weight) > 1e-6}
        disabled = [k for k, (cb, sp) in self._cfg_widgets.items() if not cb.isChecked()]
        cfg = RiskConfig(weights=weights, disabled=disabled)

        def work():
            self.ctx.ws.risk.save_config(cfg)
            return self.ctx.ws.risk.rescore_all()

        self.ctx.run(work, lambda n: (self.ctx.toast(f"Scoring configuration saved; {n} contract(s) re-scored.", "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="risk config")

    # ------------------------------------------------------------------ data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        d = ws.analytics.dashboard()
        names, types, grid = ws.analytics.risk_heatmap()
        contracts = ws.repos.contracts.list([__import__("app.database.store", fromlist=["F"]).F.is_null("deleted_at")])
        from app.services.risk import score_findings

        findings = ws.repos.findings.list()
        by: dict[Any, list] = {}
        for f in findings:
            by.setdefault(f.contract_id, []).append(f)
        cfg = ws.risk.config()
        rows = []
        for c in contracts:
            s = score_findings(by.get(c.id, []), cfg)
            rows.append({"contract": c, "business": s.business, "extraction": s.extraction, "band": s.band, "top": [x.title for x in sorted(s.contributions, key=lambda x: -x.weight)[:3]]})
        return {"dash": d, "matrix": ws.analytics.risk_matrix(), "heat": (names, types, grid), "trend": ws.analytics.risk_trend(), "rows": rows, "queue": ws.reviews.queue(), "cfg": cfg}

    def is_empty(self, data) -> bool:
        return not data["rows"]

    def render(self, data: dict[str, Any]) -> None:
        d, rows = data["dash"], data["rows"]
        high = sum(n for (sev, _c), n in data["matrix"].items() if sev in ("high", "critical"))
        self.k_high.set_value(high, "open high/critical signals", "danger" if high else "success")
        self.k_review.set_value(d.review_queue, "cases awaiting a decision", "warning" if d.review_queue else "success")
        n = max(1, len(rows))
        self.k_avg.set_value(f"{sum(r['business'] for r in rows) / n:.0f}", "0–100, higher = more to review")
        self.k_unc.set_value(f"{sum(r['extraction'] for r in rows) / n:.0f}", "how much AI output needs checking")
        self.matrix.set_data(data["matrix"])
        self.heat.set_data(*data["heat"])
        tr = data["trend"]
        self.trend.set_series({"Business": ([t[0] for t in tr], [t[1] for t in tr], "warning"), "Extraction uncertainty": ([t[0] for t in tr], [t[2] for t in tr], "violet")})
        self.contract_table.set_rows(sorted(rows, key=lambda r: -r["business"]))
        self.queue.set_rows(data["queue"])
        cfg: RiskConfig = data["cfg"]
        for k, (cb, sp) in self._cfg_widgets.items():
            cb.setChecked(cfg.enabled(k))
            sp.setValue(cfg.weight_for(k))

    def on_show(self, params: dict[str, Any] | None = None) -> None:
        super().on_show(params)
        if params and params.get("tab") in self._tab_index:
            self.tabs.setCurrentIndex(self._tab_index[params["tab"]])


_ = (clear_layout, elide, ContractLensError)
