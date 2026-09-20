"""Visual building blocks shared by the obligation, contract and copilot screens.

Avatar, ScoreRing, StatTile, FactTile, AccentCard and TypingDots. Everything is painted from design tokens, so the
high-contrast theme and the reduced-motion switch apply automatically.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.ui.components.base import label, repolish
from app.ui.theme import icons
from app.ui.theme.motion import Motion
from app.ui.theme.tokens import SPACE, is_classic, radius, theme, tone_color


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(a)
    return c


class Avatar(QWidget):
    """Round gradient badge holding an icon or up to two initials."""

    def __init__(self, icon_name: str | None = None, *, text: str = "", size: int = 34, tone: str = "cyan", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon, self._text, self._tone = icon_name, text[:2].upper(), tone
        self.setFixedSize(size, size)

    def set_text(self, text: str, tone: str | None = None) -> None:
        self._icon, self._text = None, text[:2].upper()
        if tone:
            self._tone = tone
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = QColor(tone_color(self._tone))
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, base)
        grad.setColorAt(1.0, QColor(t.indigo if self._tone != "violet" else t.cyan))
        p.setPen(Qt.PenStyle.NoPen)
        if is_classic():
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QColor(tone_color(self._tone if self._tone != "cyan" else "cyan")))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
            p.setPen(QColor("#FFFFFF"))
            p.drawLine(0, 0, self.width() - 2, 0)
            p.drawLine(0, 0, 0, self.height() - 2)
            p.setPen(QColor("#202020"))
            p.drawLine(0, self.height() - 1, self.width() - 1, self.height() - 1)
            p.drawLine(self.width() - 1, 0, self.width() - 1, self.height() - 1)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        else:
            p.setBrush(grad)
            p.drawEllipse(r)
        if self._icon:
            s = int(self.width() * 0.52)
            px = icons.pixmap(self._icon, "#FFFFFF" if is_classic() else "#04121A", s, 1.9)
            p.drawPixmap(int((self.width() - s) / 2), int((self.height() - s) / 2), px)
        elif self._text:
            f = QFont(self.font())
            f.setBold(True)
            f.setPixelSize(max(10, int(self.width() * 0.36)))
            p.setFont(f)
            p.setPen(QColor("#FFFFFF" if is_classic() else "#04121A"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class ScoreRing(QWidget):
    """Donut gauge for a 0-100 score. Higher is 'worse' for review scores, so the tone is supplied by the caller."""

    def __init__(self, caption: str = "", size: int = 74, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value: float | None = None
        self._tone = "cyan"
        self._caption = caption
        self.setFixedSize(size, size)

    def set_value(self, value: float | None, tone: str = "cyan") -> None:
        self._value, self._tone = value, tone
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        w = 7
        r = QRectF(self.rect()).adjusted(w / 2 + 1, w / 2 + 1, -w / 2 - 1, -w / 2 - 1)
        p.setPen(QPen(_alpha(t.border_hi, 0.6), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(r)
        if self._value is not None:
            p.setPen(QPen(QColor(tone_color(self._tone)), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-max(2.0, self._value) / 100 * 360 * 16))
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(self.width() * 0.30))
        p.setFont(f)
        p.setPen(QColor(t.text))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—" if self._value is None else f"{self._value:.0f}")
        p.end()


class StatTile(QFrame):
    """Compact KPI tile with an accent stripe. Clickable; ``set_active`` highlights the tile that drives a filter."""

    clicked = pyqtSignal()

    def __init__(self, title: str, icon_name: str, tone: str = "cyan", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = tone
        self.setProperty("panel", "stat")
        self.setProperty("active", False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["lg"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        row.setSpacing(SPACE["md"])
        self._icon = QLabel()
        self._icon.setFixedSize(34, 34)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(f"background: {_rgba(tone_color(tone), 0.15)}; border-radius: {radius(10)}px;")
        self._icon.setPixmap(icons.pixmap(icon_name, tone_color(tone), 18))
        row.addWidget(self._icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        self._value = label("0", None)
        self._value.setStyleSheet("font-size: 22px; font-weight: 700; background: transparent;")
        self._title = label(title.upper(), "faint")
        col.addWidget(self._value)
        col.addWidget(self._title)
        row.addLayout(col, 1)

    def set_value(self, value: int | str) -> None:
        self._value.setText(str(value))

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        repolish(self)

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tone_color(self._tone)))
        if is_classic():
            p.end()
            return
        p.drawRoundedRect(QRectF(0, 12, 3.5, self.height() - 24), 1.7, 1.7)
        p.end()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class FactTile(QFrame):
    """Caption + value cell with a tinted icon; used for contract metadata and obligation facts."""

    def __init__(self, caption: str, value: str, icon_name: str = "info", tone: str = "cyan", *, dim: bool = False, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", "fact")
        self.setMinimumHeight(72)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE["md"], SPACE["sm"] + 2, SPACE["md"], SPACE["sm"] + 2)
        row.setSpacing(SPACE["md"])
        ic = QLabel()
        ic.setFixedSize(28, 28)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setStyleSheet(f"background: {_rgba(tone_color(tone), 0.14)}; border-radius: {radius(8)}px;")
        ic.setPixmap(icons.pixmap(icon_name, tone_color(tone), 15))
        row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(caption.upper(), "faint"))
        v = label(value, "muted" if dim else None, wrap=True)
        v.setToolTip(tooltip or value)
        col.addWidget(v)
        row.addLayout(col, 1)


class AccentCard(QFrame):
    """Card with a coloured left stripe. ``body`` is the content layout."""

    clicked = pyqtSignal()

    def __init__(self, tone: str = "info", parent: QWidget | None = None, *, clickable: bool = False) -> None:
        super().__init__(parent)
        self._tone = tone
        self.setProperty("panel", "callout")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = clickable
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE["lg"] + 2, SPACE["md"], SPACE["md"], SPACE["md"])
        self.body.setSpacing(SPACE["sm"])

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tone_color(self._tone)))
        if is_classic():
            p.setBrush(QColor(tone_color(self._tone)))
            p.drawRect(QRectF(2, 2, 4, self.height() - 4))
            p.end()
            return
        p.drawRoundedRect(QRectF(0, 8, 3.5, self.height() - 16), 1.7, 1.7)
        p.end()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._clickable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class TypingDots(QWidget):
    """Three pulsing dots shown while the copilot reads the contracts. Static when reduced motion is on."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0
        self.setFixedSize(44, 16)
        self._timer = QTimer(self)
        self._timer.setInterval(280)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        if Motion.enabled:
            self._phase = (self._phase + 1) % 3
            self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            active = (i == self._phase) or not Motion.enabled
            p.setBrush(_alpha(theme().cyan, 1.0 if active else 0.35))
            p.drawEllipse(QPointF(8 + i * 14, 8), 4 if active else 3.2, 4 if active else 3.2)
        p.end()


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def initials(name: str) -> str:
    parts = [x for x in (name or "").replace(".", " ").replace("_", " ").split() if x]
    return "".join(x[0] for x in parts[:2]) or "?"
