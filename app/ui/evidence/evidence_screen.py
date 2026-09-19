"""Evidence Explorer: hybrid semantic search across authorised contracts and the evidence ledger."""
from __future__ import annotations

import html
import re
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget

from app.database.store import F
from app.models.enums import ClauseType
from app.rag.retriever import RetrievalScope, RetrievedChunk
from app.rag.text_utils import query_terms
from app.ui.components.base import clear_layout, label
from app.ui.components.cards import _ClickableCard
from app.ui.components.data_table import Column, DataTable
from app.ui.components.evidence_panel import EvidencePanel, EvidenceVM
from app.ui.components.primitives import GlassPanel, NeonButton, SearchBar, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, theme


def _highlight(text: str, query: str, limit: int = 700) -> str:
    esc = html.escape(text[:limit] + ("…" if len(text) > limit else ""))
    for term in sorted({t for t in re.findall(r"[A-Za-z0-9]{3,}", query)}, key=len, reverse=True):
        esc = re.sub(rf"(?i)\b({re.escape(html.escape(term))})\b", f"<span style='background:{theme().cyan_dim}; color:#fff'>\\1</span>", esc)
    return esc.replace("\n", "<br>")


class EvidenceExplorerScreen(BaseScreen):
    nav_id = "evidence"
    title = "Evidence Explorer"
    eyebrow = "Search & provenance"
    icon_name = "evidence"

    def build(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._search_tab(), "Semantic search")
        tabs.addTab(self._ledger_tab(), "Evidence ledger")
        self.tabs = tabs
        return tabs

    def _search_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(SPACE["md"])
        bar = QHBoxLayout()
        self.query = SearchBar("Search meaning and keywords, e.g. “who pays late fees” or “notice of non-renewal”")
        self.query.submitted.connect(self._search)
        self.scope = QComboBox()
        self.clause = QComboBox()
        self.clause.addItem("Any clause type", None)
        for c in ClauseType:
            self.clause.addItem(c.value.replace("_", " ").title(), c.value)
        go = NeonButton("Search", "primary", "search")
        go.clicked.connect(lambda: self._search(self.query.text()))
        bar.addWidget(self.query, 1)
        bar.addWidget(self.scope)
        bar.addWidget(self.clause)
        bar.addWidget(go)
        lay.addLayout(bar)
        self.info = label("", "faint", wrap=True)
        lay.addWidget(self.info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        self.results = QVBoxLayout(host)
        self.results.setSpacing(SPACE["sm"])
        self.results.addStretch(1)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        return w

    def _ledger_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.ledger = DataTable([
            Column("Contract", lambda e: e[1], width=170),
            Column("Subject", lambda e: e[0].subject_type.value, kind="badge", tone=lambda v, r: "info", width=100),
            Column("Field", lambda e: e[0].field_name or "", width=90),
            Column("Quote", lambda e: e[0].quote, stretch=True),
            Column("Page", lambda e: e[0].page_number, kind="number", width=55),
            Column("§", lambda e: e[0].section_reference or "", width=60),
            Column("Verified", lambda e: "verified" if e[0].verified else "unverified", kind="badge", tone=lambda v, r: "success" if v == "verified" else "danger", width=100),
        ], empty_text="No evidence has been recorded yet.")
        self.ledger.rowSelected.connect(self._ledger_selected)
        p = GlassPanel("Evidence ledger", "Every stored quote was verified verbatim against the source passage")
        self.ledger_filter = SearchBar("Filter evidence")
        self.ledger_filter.queryChanged.connect(self.ledger.filter)
        p.body.addWidget(self.ledger_filter)
        p.body.addWidget(self.ledger)
        split.addWidget(p)
        self.ledger_panel = EvidencePanel("Selected evidence")
        self.ledger_panel.openRequested.connect(self.ctx.open_evidence)
        rp = GlassPanel()
        rp.body.addWidget(self.ledger_panel)
        split.addWidget(rp)
        split.setSizes([800, 420])
        lay.addWidget(split)
        return w

    # ------------------------------------------------------------------ data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        titles = {c.id: c.title for c in ws.repos.contracts.list([F.is_null("deleted_at")])}
        ev = [(e, titles.get(e.contract_id, "Contract")) for e in ws.repos.evidence.list(order_by=[("created_at", True)], limit=1500) if e.contract_id in titles]
        return {"titles": titles, "ev": ev}

    def render(self, data: dict[str, Any]) -> None:
        self._titles = data["titles"]
        self.scope.clear()
        self.scope.addItem("All contracts", None)
        for cid, t in self._titles.items():
            self.scope.addItem(t, cid)
        self.ledger.set_rows(data["ev"])
        info = self.ctx.ws.system_info()
        self.info.setText(f"Hybrid retrieval (dense + BM25, reranked) · embeddings: {info.embedder} · index: {info.vector_backend}"
                          + (f" — {info.embedder_note.rstrip('.')}" if "Offline" in info.embedder_note else "") + ". Results are restricted to your organisation's contracts.")

    def on_show(self, params: dict[str, Any] | None = None) -> None:
        super().on_show(params)
        if params and params.get("query"):
            self.tabs.setCurrentIndex(0)
            self.query.setText(params["query"])
            self._search(params["query"])

    def _search(self, q: str) -> None:
        q = q.strip()
        if not q:
            return
        ws = self.ctx.ws
        if ws.retriever is None:
            self.ctx.toast(ws.index_error or "Search index unavailable.", "warning")
            return
        cid, ctype = self.scope.currentData(), self.clause.currentData()
        clear_layout(self.results)
        self.results.addWidget(label("Searching…", "muted"))

        def work():
            scope = RetrievalScope(contract_ids=[str(cid)] if cid else [str(c.id) for c in ws.repos.contracts.list([F.is_null("deleted_at")])], clause_types=[ctype] if ctype else None)
            titles = {c.id: c.title for c in ws.repos.contracts.list([F.is_null("deleted_at")])}
            return ws.retriever.retrieve(q, scope, k=12), titles

        self.ctx.run(work, lambda out: self._show(q, out[0], out[1]), name="evidence search")

    def _show(self, q: str, res, titles) -> None:
        clear_layout(self.results)
        if not res.chunks:
            self.results.addWidget(label("No passages found. Try different words or remove filters.", "muted"))
            self.results.addStretch(1)
            return
        rel = sum(1 for c in res.chunks if c.relevant)
        self.results.addWidget(label(f"{len(res.chunks)} passages ranked · {rel} judged relevant. Passages marked “weak match” may not answer the question.", "faint"))
        for c in res.chunks:
            self.results.addWidget(self._card(q, c, titles))
        self.results.addStretch(1)

    def _card(self, q: str, c: RetrievedChunk, titles) -> QWidget:
        from uuid import UUID

        card = _ClickableCard(c.chunk_id)
        lay = QVBoxLayout(card)
        head = QHBoxLayout()
        head.addWidget(label(titles.get(UUID(c.contract_id), "Contract"), "h3"), 1)
        head.addWidget(StatusBadge(f"§{c.section_reference or '-'} · p.{c.page_number}", "info"))
        head.addWidget(StatusBadge(f"semantic {c.dense_score:.2f}", "violet"))
        head.addWidget(StatusBadge(f"keyword {c.keyword_score:.2f}", "cyan"))
        head.addWidget(StatusBadge("relevant" if c.relevant else "weak match", "success" if c.relevant else "warning"))
        lay.addLayout(head)
        body = label("", None, wrap=True, selectable=True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(_highlight(c.text, q))
        lay.addWidget(body)
        row = QHBoxLayout()
        row.addStretch(1)
        b = NeonButton("Open in contract", "default", "external")
        b.clicked.connect(lambda _=False, ch=c: self.ctx.open_contract(ch.contract_id, page=ch.page_number, quote=ch.text[:120], version_id=str(ch.metadata.get("contract_version_id"))))
        row.addWidget(b)
        lay.addLayout(row)
        return card

    def _ledger_selected(self, row) -> None:
        if row is None:
            self.ledger_panel.clear()
            return
        e, title = row
        self.ledger_panel.set_evidence([EvidenceVM.from_row(e, title, e.subject_type.value)])


_ = query_terms
