"""Transient notifications (top-right), with a soft slide/fade transition."""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.ui.components.base import label
from app.ui.theme import icons
from app.ui.theme.motion import Motion
from app.ui.theme.tokens import theme, tone_color

_ICON = {"success": "check", "warning": "alert", "danger": "alert", "info": "info", "neutral": "info"}


class Toast(QFrame):
    def __init__(self, parent: QWidget, message: str, tone: str, ms: int) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        color = tone_color(tone)
        t = theme()
        self.setStyleSheet(f"QFrame {{ background: {t.panel}; border: 1px solid {color}; border-radius: 12px; }} QLabel {{ background: transparent; border: none; }}")
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 14, 10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(_ICON.get(tone, "info"), color, 18))
        h.addWidget(ic)
        msg = label(message, None, wrap=True)
        msg.setMaximumWidth(360)
        h.addWidget(msg, 1)
        self.setFixedWidth(min(440, max(260, msg.sizeHint().width() + 70)))
        self.adjustSize()
        self._ms = ms

    def life(self, on_close) -> None:
        QTimer.singleShot(self._ms, lambda: self._close(on_close) if not sip.isdeleted(self) else None)

    def _close(self, on_close) -> None:
        if Motion.enabled:
            anim = QPropertyAnimation(self, b"pos", self)
            anim.setDuration(200)
            anim.setStartValue(self.pos())
            anim.setEndValue(self.pos() + QPoint(40, 0))
            anim.finished.connect(lambda: (on_close(self), self.deleteLater()))
            anim.start()
            self._anim = anim
        else:
            on_close(self)
            self.deleteLater()


class ToastManager:
    def __init__(self, host: QWidget) -> None:
        self._host = host
        self._toasts: list[Toast] = []

    def show(self, message: str, tone: str = "info", ms: int = 4200) -> None:
        toast = Toast(self._host, message, tone, ms)
        self._toasts.append(toast)
        toast.show()
        self._layout()
        if Motion.enabled:
            end = toast.pos()
            toast.move(end + QPoint(40, 0))
            anim = QPropertyAnimation(toast, b"pos", toast)
            anim.setDuration(220)
            anim.setStartValue(toast.pos())
            anim.setEndValue(end)
            anim.start()
            toast._in = anim  # type: ignore[attr-defined]
        toast.life(self._remove)

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._layout()

    def _layout(self) -> None:
        y = 70
        for t in self._toasts:
            t.move(self._host.width() - t.width() - 20, y)
            t.raise_()
            y += t.height() + 10

    def relayout(self) -> None:
        self._layout()
