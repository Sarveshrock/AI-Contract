"""AI Copilot: grounded Q&A with sources, uncertainty and evidence-opening actions."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from app.agents.copilot import CopilotAnswer
from app.database.store import F
from app.ui.components.base import label
from app.ui.components.chat import ChatMessage, fill_answer, typing_indicator
from app.ui.components.polish import Avatar
from app.ui.components.primitives import NeonButton, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, radius, theme, tone_color

SUGGESTIONS = [
    ("obligations", "cyan", "What obligations does the vendor have under this agreement?"),
    ("calendar", "warning", "Which contracts expire in the next 90 days?"),
    ("layers", "violet", "Compare the termination provisions in version 1 and version 2"),
    ("clock", "danger", "Show all obligations without an explicit deadline"),
    ("search", "info", "Which clauses mention service credits?"),
    ("link", "cyan", "Which deadlines depend on invoice receipt?"),
    ("calendar", "success", "Explain how the renewal notice deadline was calculated"),
    ("alert", "warning", "What information is unresolved or missing?"),
]


class _SuggestionCard(QFrame):
    def __init__(self, icon_name: str, tone: str, text: str, on_click) -> None:
        super().__init__()
        self.setProperty("panel", "suggest")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        self._text, self._on_click = text, on_click
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        row.setSpacing(SPACE["md"])
        ic = QLabel()
        ic.setFixedSize(34, 34)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c = tone_color(tone)
        ic.setStyleSheet(f"background: {_soft(c)}; border-radius: {radius(10)}px;")
        ic.setPixmap(icons.pixmap(icon_name, c, 18))
        row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label(text, None, wrap=True), 1)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._text)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._on_click(self._text)
        else:
            super().keyPressEvent(e)


def _soft(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, 36)"


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

        # header actions: scope + new chat
        self.scope = QComboBox()
        self.scope.setMinimumWidth(240)
        self.scope.setAccessibleName("Question scope")
        self.scope.setToolTip("Limit questions to one contract, or search all authorised contracts")
        new_chat = NeonButton("New chat", "default", "plus")
        new_chat.clicked.connect(self._reset)
        self.actions.addWidget(self.scope)
        self.actions.addWidget(new_chat)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_host = QWidget()
        self.chat = QVBoxLayout(self.chat_host)
        self.chat.setContentsMargins(4, 4, 12, 8)
        self.chat.setSpacing(SPACE["lg"])
        self.hero = self._build_hero()
        self.chat.addWidget(self.hero)
        self.chat.addStretch(1)
        self.scroll.setWidget(self.chat_host)
        self._stick = False
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda _lo, hi: self.scroll.verticalScrollBar().setValue(hi) if self._stick else None)
        lay.addWidget(self.scroll, 1)

        composer = QFrame()
        composer.setProperty("panel", "composer")
        composer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(composer)
        row.setContentsMargins(SPACE["lg"] + 2, 6, 6, 6)
        row.setSpacing(SPACE["sm"])
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask about obligations, renewals, deadlines, amendments or clauses…")
        self.input.setAccessibleName("Ask the copilot")
        self.input.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 8px 4px; font-size: 14px; }")
        self.input.returnPressed.connect(lambda: self._ask(self.input.text()))
        self.send = NeonButton("Ask", "primary", "send")
        self.send.setMinimumHeight(38)
        self.send.setStyleSheet("QPushButton { border-radius: 19px; padding: 6px 20px; }")
        self.send.clicked.connect(lambda: self._ask(self.input.text()))
        row.addWidget(self.input, 1)
        row.addWidget(self.send)
        lay.addWidget(composer)
        self.foot = label("The copilot assists professionals and does not provide legal advice. Answers that cannot be established from contract text are refused rather than guessed.", "faint", wrap=True)
        self.foot.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.foot)
        self._asking = False
        return root

    def _build_hero(self) -> QWidget:
        hero = QWidget()
        v = QVBoxLayout(hero)
        v.setContentsMargins(0, SPACE["lg"], 0, 0)
        v.setSpacing(SPACE["lg"])
        card = QFrame()
        card.setProperty("panel", "hero")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(card)
        row.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        row.setSpacing(SPACE["lg"])
        row.addWidget(Avatar("copilot", size=60), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("How can I help with your contracts?", "h2"))
        col.addWidget(label("Every answer shows its source sections, and claims that the contract text does not support are withheld.", "muted", wrap=True))
        self.mode_note = label("", "faint", wrap=True)
        col.addSpacing(4)
        col.addWidget(self.mode_note)
        row.addLayout(col, 1)
        v.addWidget(card)
        v.addWidget(label("TRY ASKING", "faint"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE["md"])
        grid.setVerticalSpacing(SPACE["md"])
        for i, (ic, tone, text) in enumerate(SUGGESTIONS):
            grid.addWidget(_SuggestionCard(ic, tone, text, self._ask), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        return hero

    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        return {"contracts": [(c.id, c.title) for c in ws.repos.contracts.list([F.is_null("deleted_at")])], "info": ws.system_info()}

    def render(self, data: dict[str, Any]) -> None:
        keep = self.scope.currentData()
        self.scope.clear()
        self.scope.addItem("All authorised contracts", None)
        for cid, t in data["contracts"]:
            self.scope.addItem(t, cid)
        if keep is not None:
            self.scope.setCurrentIndex(max(0, self.scope.findData(keep)))
        info = data["info"]
        if info.index_error:
            self.mode_note.setText(info.index_error)
        elif info.extras.get("offline_reason"):
            self.mode_note.setText("Offline mode: " + info.extras["offline_reason"] + " Structured questions and passage search still work.")
        elif not info.ai_configured:
            self.mode_note.setText("AI generation is not configured. Structured questions (expiry, obligations, deadlines, unresolved items) still work from stored records; open questions return the best matching passages only.")
        else:
            note = f" {info.model_note}" if info.model_note else ""
            self.mode_note.setText(f"Model {info.model} · retrieval: {info.embedder} on {info.vector_backend}.{note}")

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        for i in reversed(range(self.chat.count())):
            w = self.chat.itemAt(i).widget()
            if w is not None and w is not self.hero:
                w.deleteLater()
                self.chat.removeWidget(w)
        if self.chat.indexOf(self.hero) < 0:
            self.chat.insertWidget(0, self.hero)
        self.hero.setVisible(True)
        self.input.clear()

    def _ask(self, q: str) -> None:
        q = q.strip()
        if not q:
            return
        if self.ctx.ws.copilot is None:
            self.ctx.toast(self.ctx.ws.index_error or "Copilot unavailable.", "warning")
            return
        self.input.clear()
        self.hero.setVisible(False)
        self.chat.removeWidget(self.hero)  # a hidden widget would keep its old height in the scroll range
        user_name = getattr(getattr(self.ctx.ws, "principal", None), "display_name", None) or "You"
        ub = ChatMessage(user=True, name=user_name)
        ub.set_user_text(q)
        self.chat.insertWidget(self.chat.count() - 1, ub)
        pending = ChatMessage(user=False, name="ContractLens Copilot")
        pending.lay.addWidget(typing_indicator())
        self.chat.insertWidget(self.chat.count() - 1, pending)
        self._scroll_down()
        cid = self.scope.currentData()
        self.ctx.run(lambda: self.ctx.ws.copilot.ask(q, contract_id=cid), lambda a, p=pending: self._answer(p, a), name="analysis copilot",
                     on_error=lambda e, p=pending: (p.deleteLater(), self.ctx.error(e)))

    def _answer(self, msg: ChatMessage, a: CopilotAnswer) -> None:
        fill_answer(msg, a, lambda s: self.ctx.open_contract(s.contract_id, page=s.page_number, quote=s.excerpt[:120]))
        self._scroll_down()

    def _scroll_down(self) -> None:
        self._stick = True  # follow the range while late layout (tables, source cards) settles
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(700, lambda: setattr(self, "_stick", False))


_ = (StatusBadge, theme)
