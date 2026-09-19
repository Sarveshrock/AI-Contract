"""Small layout/widget helpers shared by all components and screens."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from app.ui.theme.tokens import SPACE


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value: Any) -> None:
    w.setProperty(name, value)
    repolish(w)


def label(text: str = "", role: str | None = None, *, wrap: bool = False, selectable: bool = False, align: Qt.AlignmentFlag | None = None) -> QLabel:
    lb = QLabel(text)
    lb.setMinimumWidth(1)  # let labels shrink instead of forcing horizontal scrolling
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if align is not None:
        lb.setAlignment(align)
    return lb


def divider() -> QFrame:
    f = QFrame()
    f.setProperty("divider", True)
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def vbox(*items: QWidget | QLayout | None, spacing: int = SPACE["md"], margins: tuple[int, int, int, int] = (0, 0, 0, 0), stretch_end: bool = False) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    _fill(lay, items)
    if stretch_end:
        lay.addStretch(1)
    return lay


def hbox(*items: QWidget | QLayout | None, spacing: int = SPACE["sm"], margins: tuple[int, int, int, int] = (0, 0, 0, 0), stretch_end: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    _fill(lay, items)
    if stretch_end:
        lay.addStretch(1)
    return lay


def _fill(lay: QLayout, items: Iterable[QWidget | QLayout | None]) -> None:
    for it in items:
        if it is None:
            lay.addStretch(1) if isinstance(lay, (QVBoxLayout, QHBoxLayout)) else None
        elif isinstance(it, QLayout):
            lay.addItem(it)
        else:
            lay.addWidget(it)


def wrap_layout(layout: QLayout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return w


def elide(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def fmt_date(d: Any) -> str:
    return d.strftime("%d %b %Y") if d else "—"


def days_text(days: int | None) -> str:
    if days is None:
        return "—"
    if days < 0:
        return f"{-days}d overdue"
    return "today" if days == 0 else f"in {days}d"
