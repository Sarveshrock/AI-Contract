"""AI Copilot: grounded Q&A with sources, uncertainty and evidence-opening actions."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.agents.copilot import CopilotAnswer
from app.database.store import F
from app.ui.components.base import clear_layout, label
from app.ui.components.data_table import Column, DataTable
from app.ui.components.primitives import NeonButton, SearchBar, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme.tokens import SPACE, theme

SUGGESTIONS = [
    "What obligations does the vendor have under this agreement?", "Which contracts expire in the next 90 days?", "Compare the termination provisions in version 1 and version 2",
    "Show all obligations without an explicit deadline", "Which clauses mention service credits?", "Which deadlines depend on invoice receipt?",
    "Explain how the renewal notice deadline was calculated", "What information is unresolved or missing?",
]
MODE_LABEL = {"structured": ("computed from stored records", "cyan"), "rag": ("grounded in retrieved passages", "success"), "extractive": ("retrieval only · no AI answer", "warning"), "refused": ("not answered", "danger")}


class _Bubble(QFrame):
    def __init__(self, user: bool) -> None:
        super().__init__()
        t = theme()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("bubble")
        bg = "rgba(102,116,245,0.18)" if user else "rgba(16,26,45,0.92)"
        border = t.indigo if user else t.border_hi
        self.setStyleSheet(f"QFrame#bubble {{ background: {bg}; border: 1px solid {border}; border-radius: 14px; }}")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        self.lay.setSpacing(SPACE["sm"])


class CopilotScreen(BaseScreen):
    nav_id = "copilot"
    title = "AI Copilot"
    eyebrow = "Grounded answers"
    icon_name = "copilot"

    def build(self) -> QWidget:
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])
        self.mode_note = label("", "faint", wrap=True)
        lay.addWidget(self.mode_note)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        wrap = QScrollArea()
        wrap.setWidgetResizable(True)
        wrap.setFixedHeight(46)
        wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        wrap.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        host.setLayout(chips)
        for s in SUGGESTIONS:
            b = NeonButton(s if len(s) < 54 else s[:52] + "…", "chip")
            b.setCheckable(False)
            b.setToolTip(s)
            b.clicked.connect(lambda _=False, q=s: self._ask(q))
            chips.addWidget(b)
        wrap.setWidget(host)
        lay.addWidget(wrap)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.chat_host = QWidget()
        self.chat = QVBoxLayout(self.chat_host)
        self.chat.setSpacing(SPACE["md"])
        self.chat.addStretch(1)
        self.scroll.setWidget(self.chat_host)
        lay.addWidget(self.scroll, 1)
        bar = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.setMinimumWidth(220)
        self.scope.setAccessibleName("Question scope")
        self.input = SearchBar("Ask about your contracts…  answers use authorised data only")
        self.input.submitted.connect(self._ask)
        send = NeonButton("Ask", "primary", "send")
        send.clicked.connect(lambda: self._ask(self.input.text()))
        bar.addWidget(self.scope)
        bar.addWidget(self.input, 1)
        bar.addWidget(send)
        lay.addLayout(bar)
        lay.addWidget(label("The copilot assists professionals and does not provide legal advice. Answers that cannot be established from contract text are refused rather than guessed.", "faint", wrap=True))
        self._welcome = True
        return root

    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        return {"contracts": [(c.id, c.title) for c in ws.repos.contracts.list([F.is_null("deleted_at")])], "info": ws.system_info()}

    def render(self, data: dict[str, Any]) -> None:
        self.scope.clear()
        self.scope.addItem("All authorised contracts", None)
        for cid, t in data["contracts"]:
            self.scope.addItem(t, cid)
        info = data["info"]
        if info.index_error:
            self.mode_note.setText(info.index_error)
        elif not info.ai_configured:
            self.mode_note.setText("AI generation is not configured. Structured questions (expiry, obligations, deadlines, unresolved items) still work from stored records; open questions return the best matching passages only.")
        else:
            self.mode_note.setText(f"Model {info.model} · retrieval: {info.embedder} on {info.vector_backend}")
        if self._welcome:
            self._welcome = False
            b = _Bubble(False)
            b.lay.addWidget(label("Ask about obligations, renewals, deadlines, amendments or clauses. Every answer shows its source sections, and unsupported claims are withheld.", "muted", wrap=True))
            self.chat.insertWidget(self.chat.count() - 1, b)

    # ------------------------------------------------------------------
    def _ask(self, q: str) -> None:
        q = q.strip()
        if not q:
            return
        if self.ctx.ws.copilot is None:
            self.ctx.toast(self.ctx.ws.index_error or "Copilot unavailable.", "warning")
            return
        self.input.clear()
        ub = _Bubble(True)
        ub.lay.addWidget(label(q, None, wrap=True, selectable=True))
        self.chat.insertWidget(self.chat.count() - 1, ub)
        pending = _Bubble(False)
        pending.lay.addWidget(label("Reading authorised contracts…", "muted"))
        self.chat.insertWidget(self.chat.count() - 1, pending)
        self._scroll_down()
        cid = self.scope.currentData()
        self.ctx.run(lambda: self.ctx.ws.copilot.ask(q, contract_id=cid), lambda a, p=pending: self._answer(p, a), name="analysis copilot",
                     on_error=lambda e, p=pending: (p.deleteLater(), self.ctx.error(e)))

    def _answer(self, bubble: _Bubble, a: CopilotAnswer) -> None:
        clear_layout(bubble.lay)
        mode, tone = MODE_LABEL.get(a.mode, (a.mode, "neutral"))
        head = QHBoxLayout()
        head.addWidget(StatusBadge(mode, tone))
        head.addWidget(StatusBadge(a.intent.value.replace("_", " "), "info"))
        if a.insufficient_evidence:
            head.addWidget(StatusBadge("insufficient evidence", "warning"))
        head.addStretch(1)
        bubble.lay.addLayout(head)
        bubble.lay.addWidget(label(a.answer, None, wrap=True, selectable=True))
        if a.rows and a.mode == "structured" and len(a.rows) > 1 and all(isinstance(r, dict) and r.get("kind") is None for r in a.rows[:1]):
            keys = list(a.rows[0].keys())[:4]
            t = DataTable([Column(k.replace("_", " ").title(), lambda r, k=k: r.get(k), stretch=True) for k in keys])
            t.setMinimumHeight(min(260, 60 + 34 * len(a.rows)))
            t.set_rows(a.rows[:60])
            bubble.lay.addWidget(t)
        if a.uncertainty:
            u = label("Uncertainty: " + a.uncertainty, "faint", wrap=True)
            bubble.lay.addWidget(u)
        for s in a.sources[:8]:
            box = QFrame()
            box.setProperty("panel", "sunken")
            box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            v = QVBoxLayout(box)
            h = QHBoxLayout()
            h.addWidget(StatusBadge(s.label, "cyan"))
            h.addWidget(label(f"{s.contract_title} · §{s.section_reference or '-'} · p.{s.page_number or '-'}", "muted"), 1)
            if s.contract_id:
                b = NeonButton("Open source", "default", "external")
                b.clicked.connect(lambda _=False, x=s: self.ctx.open_contract(x.contract_id, page=x.page_number, quote=x.excerpt[:120]))
                h.addWidget(b)
            v.addLayout(h)
            v.addWidget(label(f"“{s.excerpt[:420]}”", "quote", wrap=True, selectable=True))
            bubble.lay.addWidget(box)
        for n in a.notes:
            bubble.lay.addWidget(label(n, "faint", wrap=True))
        self._scroll_down()

    def _scroll_down(self) -> None:
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(60, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
