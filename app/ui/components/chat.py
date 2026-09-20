"""Chat presentation shared by the AI Copilot screen and the per-contract Ask tab."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from app.agents.copilot import CopilotAnswer
from app.ui.components.base import clear_layout, label
from app.ui.components.data_table import Column, DataTable
from app.ui.components.polish import AccentCard, Avatar, TypingDots, _rgba, initials
from app.ui.components.primitives import NeonButton, StatusBadge
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, is_classic, theme

MODE_LABEL = {"structured": ("computed from stored records", "cyan"), "rag": ("grounded in retrieved passages", "success"),
              "extractive": ("retrieval only · no AI answer", "warning"), "refused": ("not answered", "danger")}


class ChatMessage(QWidget):
    """One message row: avatar + bubble. ``lay`` is the bubble's content layout."""

    def __init__(self, *, user: bool, name: str) -> None:
        super().__init__()
        self.user = user
        t = theme()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE["md"])
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        if is_classic():
            fill = "#E4ECFF" if user else "#FFFFFF"
            self.bubble.setStyleSheet(f"QFrame#bubble {{ background: {fill}; border: 2px solid; border-color: #808080 #FFFFFF #FFFFFF #808080; }}")
        elif user:
            self.bubble.setStyleSheet(
                f"QFrame#bubble {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {_rgba(t.indigo, 0.55)}, stop:1 {_rgba(t.cyan, 0.30)});"
                f" border: 1px solid {_rgba(t.cyan, 0.55)}; border-top-right-radius: 4px; border-radius: 16px; }}")
        else:
            self.bubble.setStyleSheet(f"QFrame#bubble {{ background: {_rgba(t.panel, 0.95)}; border: 1px solid {t.border_hi}; border-top-left-radius: 4px; border-radius: 16px; }}")
        self.lay = QVBoxLayout(self.bubble)
        self.lay.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"] + 2)
        self.lay.setSpacing(SPACE["sm"] + 2)
        head = QHBoxLayout()
        head.setSpacing(SPACE["sm"])
        head.addWidget(label(name, "muted"))
        head.addWidget(label(datetime.now().strftime("%H:%M"), "faint"))
        head.addStretch(1)
        self._head = head
        self.lay.addLayout(head)
        if user:
            row.addStretch(1)
            row.addWidget(self.bubble)
            row.addWidget(Avatar(text=initials(name), size=32, tone="violet"), 0, Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(Avatar("copilot", size=32), 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(self.bubble, 1)  # full width: a capped bubble makes Qt over-estimate wrapped-label heights

    # Qt sizes a scroll area's content from minimumSizeHint, which for word-wrapped labels assumes a very narrow
    # column and so leaves large blank gaps. Once the real width is known, report the exact height for it.
    def _height_at_width(self) -> int | None:
        w = self.width()
        lay = self.layout()
        return lay.totalHeightForWidth(w) if w > 80 and lay is not None else None

    def minimumSizeHint(self):  # noqa: N802
        base = super().minimumSizeHint()
        h = self._height_at_width()
        return QSize(base.width(), h) if h else base

    def sizeHint(self):  # noqa: N802
        base = super().sizeHint()
        h = self._height_at_width()
        return QSize(base.width(), h) if h else base

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.updateGeometry()

    def set_user_text(self, text: str) -> None:
        """Size the bubble to its text; a wrapped QLabel alone would collapse to a narrow column."""
        from PyQt6.QtGui import QFontMetrics

        lb = label(text, None, wrap=True, selectable=True)
        widest = max((QFontMetrics(lb.font()).horizontalAdvance(line) for line in text.splitlines() or [""]), default=0)
        self.bubble.setFixedWidth(max(200, min(620, widest + 2 * SPACE["lg"] + 14)))
        self.lay.addWidget(lb)

    def add_head_widget(self, w: QWidget) -> None:
        self._head.insertWidget(self._head.count() - 1, w)

    def reset_content(self) -> None:
        """Clear everything below the header (used when a pending bubble receives its answer)."""
        while self.lay.count() > 1:
            item = self.lay.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                clear_layout(item.layout())


def typing_indicator(text: str = "Reading authorised contracts") -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(TypingDots())
    h.addWidget(label(text + "…", "muted"))
    h.addStretch(1)
    return w


def source_card(s, index: int, on_open: Callable[[object], None] | None) -> AccentCard:
    card = AccentCard("cyan")
    head = QHBoxLayout()
    head.setSpacing(SPACE["sm"])
    head.addWidget(StatusBadge(s.label or f"S{index}", "cyan"))
    head.addWidget(label(f"{s.contract_title} · §{s.section_reference or '-'} · p.{s.page_number or '-'}", "muted"), 1)
    if s.contract_id and on_open is not None:
        b = NeonButton("Open source", "ghost", "external")
        b.clicked.connect(lambda _=False, x=s: on_open(x))
        head.addWidget(b)
    card.body.addLayout(head)
    q = label(f"“{s.excerpt[:420]}”", "quote", wrap=True, selectable=True)
    card.body.addWidget(q)
    return card


def fill_answer(msg: ChatMessage, a: CopilotAnswer, on_open: Callable[[object], None] | None, *, max_sources: int = 8) -> None:
    """Render a CopilotAnswer into an assistant message."""
    msg.reset_content()
    mode, tone = MODE_LABEL.get(a.mode, (a.mode, "neutral"))
    badges = QHBoxLayout()
    badges.setSpacing(SPACE["sm"])
    badges.addWidget(StatusBadge(mode, tone))
    badges.addWidget(StatusBadge(a.intent.value.replace("_", " "), "info"))
    if a.insufficient_evidence:
        badges.addWidget(StatusBadge("insufficient evidence", "warning"))
    badges.addStretch(1)
    msg.lay.addLayout(badges)
    msg.lay.addWidget(label(a.answer, None, wrap=True, selectable=True))
    if a.rows and a.mode == "structured" and len(a.rows) > 1 and all(isinstance(r, dict) and r.get("kind") is None for r in a.rows[:1]):
        keys = list(a.rows[0].keys())[:4]
        t = DataTable([Column(k.replace("_", " ").title(), lambda r, k=k: r.get(k), stretch=True) for k in keys])
        t.setMinimumHeight(min(260, 60 + 34 * len(a.rows)))
        t.set_rows(a.rows[:60])
        msg.lay.addWidget(t)
    if a.uncertainty:
        card = AccentCard("warning")
        card.body.addWidget(label("Uncertainty: " + a.uncertainty, "muted", wrap=True))
        msg.lay.addWidget(card)
    if a.sources:
        msg.lay.addWidget(label(f"SOURCES · {min(len(a.sources), max_sources)}", "faint"))
    for i, s in enumerate(a.sources[:max_sources], 1):
        msg.lay.addWidget(source_card(s, i, on_open))
    for n in a.notes:
        msg.lay.addWidget(label(n, "faint", wrap=True))


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    lb = QLabel()
    lb.setPixmap(icons.pixmap(name, color, size))
    lb.setFixedSize(size + 2, size + 2)
    return lb


def make_follower(scroll: QScrollArea) -> Callable[[], None]:
    """Return ``follow()``: keep a scroll area pinned to the bottom while late layout (tables, cards) settles."""
    state = {"on": False}
    bar = scroll.verticalScrollBar()
    bar.rangeChanged.connect(lambda _lo, hi: bar.setValue(hi) if state["on"] else None)

    def follow() -> None:
        state["on"] = True
        bar.setValue(bar.maximum())
        QTimer.singleShot(700, lambda: state.update(on=False))

    return follow


def make_composer(placeholder: str, on_submit: Callable[[str], None], button_text: str = "Ask") -> tuple[QFrame, QLineEdit]:
    """Rounded message composer: a borderless input and a pill send button."""
    frame = QFrame()
    frame.setProperty("panel", "composer")
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    row = QHBoxLayout(frame)
    row.setContentsMargins(SPACE["lg"], 5, 5, 5)
    row.setSpacing(SPACE["sm"])
    line = QLineEdit()
    line.setPlaceholderText(placeholder)
    line.setAccessibleName(placeholder)
    line.setStyleSheet("QLineEdit { background: transparent; border: none; padding: 7px 2px; }")
    line.returnPressed.connect(lambda: on_submit(line.text()))
    send = NeonButton(button_text, "primary", "send")
    send.setStyleSheet("QPushButton { border-radius: 17px; padding: 5px 16px; }")
    send.setMinimumHeight(34)
    send.clicked.connect(lambda: on_submit(line.text()))
    row.addWidget(line, 1)
    row.addWidget(send)
    return frame, line
