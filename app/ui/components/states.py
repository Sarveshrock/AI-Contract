"""EmptyState, LoadingState (skeleton shimmer), ErrorState and a stack that switches between them."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from app.ui.components.base import label
from app.ui.components.primitives import NeonButton
from app.ui.theme import icons
from app.ui.theme.motion import Motion, fade_in
from app.ui.theme.tokens import SPACE, theme


class EmptyState(QWidget):
    actionClicked = pyqtSignal()

    def __init__(self, title: str = "Nothing here yet", message: str = "", action_text: str | None = None, icon_name: str = "layers", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(SPACE["md"])
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setPixmap(icons.pixmap(icon_name, theme().cyan_dim, 44, 1.4))
        self._title = label(title, "h3", align=Qt.AlignmentFlag.AlignCenter)
        self._msg = label(message, "muted", wrap=True, align=Qt.AlignmentFlag.AlignCenter)
        self._msg.setMaximumWidth(460)
        self._btn = NeonButton(action_text or "", "primary")
        self._btn.setVisible(bool(action_text))
        self._btn.clicked.connect(self.actionClicked)
        for w in (self._icon, self._title, self._msg):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._btn, 0, Qt.AlignmentFlag.AlignHCenter)

    def set(self, title: str, message: str = "", action_text: str | None = None) -> None:
        self._title.setText(title)
        self._msg.setText(message)
        self._btn.setText(action_text or "")
        self._btn.setVisible(bool(action_text))


class ErrorState(QWidget):
    retry = pyqtSignal()

    def __init__(self, message: str = "Something went wrong.", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(SPACE["md"])
        icon = QLabel()
        icon.setPixmap(icons.pixmap("alert", theme().danger, 40, 1.5))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(label("Could not load this view", "h3", align=Qt.AlignmentFlag.AlignCenter))
        self._msg = label(message, "muted", wrap=True, selectable=True, align=Qt.AlignmentFlag.AlignCenter)
        self._msg.setMaximumWidth(520)
        lay.addWidget(self._msg, 0, Qt.AlignmentFlag.AlignHCenter)
        btn = NeonButton("Try again", "default", "refresh")
        btn.clicked.connect(self.retry)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_message(self, message: str) -> None:
        self._msg.setText(message)


class LoadingState(QWidget):
    """Skeleton blocks with a soft shimmer (static when reduced motion is on)."""

    def __init__(self, rows: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(140)

    def showEvent(self, e) -> None:  # noqa: N802
        if Motion.enabled:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.02) % 1.4
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        t = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        row_h, gap = 16, 14
        y = 12.0
        widths = [0.92, 0.7, 0.85, 0.55, 0.78, 0.6, 0.88, 0.5]
        for i in range(self._rows):
            rect = QRectF(12, y, (w - 24) * widths[i % len(widths)], row_h)
            base = QColor(t.border)
            base.setAlphaF(0.55)
            p.setBrush(base)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 6, 6)
            if Motion.enabled:
                g = QLinearGradient(rect.left() + rect.width() * (self._phase - 0.4), 0, rect.left() + rect.width() * self._phase, 0)
                hi = QColor(t.cyan)
                hi.setAlphaF(0.16)
                zero = QColor(t.cyan)
                zero.setAlphaF(0.0)
                g.setColorAt(0.0, zero)
                g.setColorAt(0.5, hi)
                g.setColorAt(1.0, zero)
                p.setBrush(g)
                p.drawRoundedRect(rect, 6, 6)
            y += row_h + gap
            if y > h:
                break
        p.end()


class StatefulView(QStackedWidget):
    """Content / loading / empty / error in one place; keeps screens free of visibility juggling."""

    retryRequested = pyqtSignal()
    emptyAction = pyqtSignal()

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = content
        self.loading = LoadingState()
        self.empty = EmptyState()
        self.error = ErrorState()
        for w in (content, self.loading, self.empty, self.error):
            self.addWidget(w)
        self.error.retry.connect(self.retryRequested)
        self.empty.actionClicked.connect(self.emptyAction)

    def show_content(self) -> None:
        if self.currentWidget() is not self.content:
            self.setCurrentWidget(self.content)
            fade_in(self.content, 160)

    def show_loading(self) -> None:
        self.setCurrentWidget(self.loading)

    def show_empty(self, title: str, message: str = "", action: str | None = None) -> None:
        self.empty.set(title, message, action)
        self.setCurrentWidget(self.empty)

    def show_error(self, message: str) -> None:
        self.error.set_message(message)
        self.setCurrentWidget(self.error)
