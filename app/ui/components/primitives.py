"""GlassPanel, MetricCard, NeonButton, StatusBadge, SearchBar."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from app.ui.components.base import label, repolish
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, theme, tone_color


class GlassPanel(QFrame):
    """Translucent panel with a fine border, an optional accent edge and a header (title + actions)."""

    def __init__(self, title: str = "", subtitle: str = "", *, accent: str | None = None, padding: int = SPACE["lg"], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", "glass")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._accent = accent
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding, padding, padding)
        outer.setSpacing(SPACE["md"])
        self._header = QHBoxLayout()
        self._header.setSpacing(SPACE["sm"])
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title = label(title, "h3")
        self._subtitle = label(subtitle, "faint")
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        self._subtitle.setVisible(bool(subtitle))
        self._header.addLayout(titles, 1)
        self._header_host = QWidget()
        self._header_host.setLayout(self._header)
        self._header_host.setVisible(bool(title))
        outer.addWidget(self._header_host)
        self.body = QVBoxLayout()
        self.body.setSpacing(SPACE["md"])
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body, 1)

    def set_title(self, title: str, subtitle: str = "") -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))
        self._header_host.setVisible(bool(title))

    def add_action(self, widget: QWidget) -> None:
        self._header.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._header_host.setVisible(True)

    def set_accent(self, color: str | None) -> None:
        self._accent = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        t = theme()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        grad = QLinearGradient(r.topLeft(), r.topRight())
        c = QColor(self._accent or t.cyan)
        c.setAlphaF(0.55 if self._accent else 0.22)
        grad.setColorAt(0.0, c)
        clear = QColor(c)
        clear.setAlphaF(0.0)
        grad.setColorAt(0.6, clear)
        p.setPen(QPen(grad, 1.4))
        p.drawLine(int(r.left() + 14), int(r.top()), int(r.right() - 14), int(r.top()))
        p.end()


class StatusBadge(QLabel):
    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text.replace("_", " ").upper() if text else "", parent)
        self.setProperty("badge", True)
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set(self, text: str, tone: str) -> None:
        self.setText(text.replace("_", " ").upper())
        self.setProperty("tone", tone)
        repolish(self)


class StatusDot(QLabel):
    def __init__(self, tone: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("dot", True)
        self.setProperty("tone", tone)
        self.setFixedSize(8, 8)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class NeonButton(QPushButton):
    """Primary/ghost/danger/success/chip button. The primary variant glows subtly on hover and focus."""

    def __init__(self, text: str = "", variant: str = "default", icon_name: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self._busy_text: str | None = None
        self._orig_text = text
        if icon_name:
            self._apply_icon()
        if variant == "chip":
            self.setCheckable(True)
        self._glow: QGraphicsDropShadowEffect | None = None
        if variant == "primary":
            self._glow = QGraphicsDropShadowEffect(self)
            self._glow.setBlurRadius(22)
            self._glow.setOffset(0, 0)
            c = QColor(theme().cyan)
            c.setAlphaF(0.0)
            self._glow.setColor(c)
            self.setGraphicsEffect(self._glow)

    def _apply_icon(self) -> None:
        t = theme()
        color = "#04121A" if self.property("variant") == "primary" else {"danger": t.danger, "success": t.success}.get(self.property("variant"), t.text_dim)
        self.setIcon(icons.icon(self._icon_name or "info", color, 16))

    def set_busy(self, busy: bool, text: str = "Working…") -> None:
        self.setEnabled(not busy)
        if busy:
            self._orig_text = self.text()
            self.setText(text)
        else:
            self.setText(self._orig_text)

    def _set_glow(self, alpha: float) -> None:
        if self._glow is not None:
            c = QColor(theme().cyan)
            c.setAlphaF(alpha)
            self._glow.setColor(c)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._set_glow(0.45 if self.isEnabled() else 0.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._set_glow(0.0)
        super().leaveEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._set_glow(0.35)
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self._set_glow(0.0)
        super().focusOutEvent(e)


class IconButton(QToolButton):
    def __init__(self, icon_name: str, tooltip: str, parent: QWidget | None = None, *, size: int = 18) -> None:
        super().__init__(parent)
        self.setIcon(icons.icon(icon_name, theme().text_dim, size))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)


class MetricCard(GlassPanel):
    """KPI tile: icon, title, big value and a caption. Clickable and keyboard-activatable."""

    clicked = pyqtSignal()

    def __init__(self, title: str, icon_name: str = "info", accent: str = "cyan", parent: QWidget | None = None) -> None:
        super().__init__(padding=SPACE["md"], parent=parent)
        self._tone = accent
        self.setAccessibleName(title)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(104)
        self.set_accent(tone_color(accent))
        row = QHBoxLayout()
        self._icon = QLabel()
        self._icon.setFixedSize(30, 30)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon_name
        row.addWidget(self._icon)
        self._title = label(title.upper(), "faint", wrap=True)
        row.addWidget(self._title, 1)
        self.body.addLayout(row)
        self._value = label("—", "kpi")
        self.body.addWidget(self._value)
        self._caption = label("", "faint", wrap=True)
        self.body.addWidget(self._caption)
        self._paint_icon()

    def _paint_icon(self) -> None:
        color = tone_color(self._tone)
        self._icon.setPixmap(icons.pixmap(self._icon_name, color, 18))

    def set_value(self, value: str | int, caption: str = "", tone: str | None = None) -> None:
        self._value.setText(str(value))
        self._caption.setText(caption)
        if tone and tone != self._tone:
            self._tone = tone
            self.set_accent(tone_color(tone))
            self._paint_icon()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        self.setStyleSheet(f"QFrame[panel='glass'] {{ border: 1px solid {theme().focus}; }}")
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self.setStyleSheet("")
        super().focusOutEvent(e)


class SearchBar(QLineEdit):
    """Rounded search field with a leading icon, debounced ``queryChanged`` and ``submitted`` on Enter."""

    queryChanged = pyqtSignal(str)
    submitted = pyqtSignal(str)

    def __init__(self, placeholder: str = "Search…", parent: QWidget | None = None, *, debounce_ms: int = 280) -> None:
        super().__init__(parent)
        self.setProperty("search", True)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setAccessibleName(placeholder)
        self._icon = QLabel(self)
        self._icon.setPixmap(icons.pixmap("search", theme().text_faint, 16))
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(lambda: self.queryChanged.emit(self.text().strip()))
        self.textChanged.connect(lambda _t: self._timer.start())
        self.returnPressed.connect(lambda: self.submitted.emit(self.text().strip()))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._icon.move(13, (self.height() - 16) // 2)

    def changeEvent(self, e: QEvent) -> None:  # noqa: N802
        super().changeEvent(e)
