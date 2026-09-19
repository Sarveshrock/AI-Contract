"""MonthCalendar: a compact month grid with deadline markers (used by Command Center and Renewal Radar)."""
from __future__ import annotations

import calendar
from datetime import date

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from app.ui.components.base import label
from app.ui.components.primitives import IconButton
from app.ui.theme.tokens import theme, tone_color

CATEGORY_TONE = {"renewal_notice": "danger", "expiration": "warning", "termination_notice": "warning", "payment": "cyan", "delivery": "info", "reporting": "violet", "other": "neutral"}


class _Grid(QWidget):
    daySelected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.year, self.month = date.today().year, date.today().month
        self.events: dict[date, list[tuple[str, str, str]]] = {}
        self.selected: date | None = None
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Deadline calendar")

    def _cell(self, d: date) -> QRectF:
        first_wd, _ = calendar.monthrange(self.year, self.month)
        idx = first_wd + d.day - 1
        row, col = divmod(idx, 7)
        top = 22.0
        cw = self.width() / 7
        rows = 6
        ch = (self.height() - top) / rows
        return QRectF(col * cw, top + row * ch, cw, ch)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        cw = self.width() / 7
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            p.setPen(QColor(t.text_faint))
            p.drawText(QRectF(i * cw, 0, cw, 20), Qt.AlignmentFlag.AlignCenter, name)
        _, days = calendar.monthrange(self.year, self.month)
        today = date.today()
        for day in range(1, days + 1):
            d = date(self.year, self.month, day)
            r = self._cell(d).adjusted(2, 2, -2, -2)
            evs = self.events.get(d, [])
            if d == self.selected:
                p.setPen(QPen(QColor(t.cyan), 1.4))
                p.setBrush(QColor(34, 211, 238, 30))
            elif d == today:
                p.setPen(QPen(QColor(t.border_hi), 1))
                p.setBrush(QColor(255, 255, 255, 10))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 5) if evs else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(t.text if evs or d == today else t.text_dim))
            p.drawText(QRectF(r.left() + 5, r.top() + 2, 20, 14), Qt.AlignmentFlag.AlignLeft, str(day))
            for k, (_, _, cat) in enumerate(evs[:4]):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(tone_color(CATEGORY_TONE.get(cat, "neutral"))))
                p.drawEllipse(QRectF(r.left() + 6 + k * 9, r.bottom() - 11, 6, 6))
            if len(evs) > 4:
                p.setPen(QColor(t.text_faint))
                p.drawText(QRectF(r.right() - 22, r.bottom() - 14, 20, 12), Qt.AlignmentFlag.AlignRight, f"+{len(evs) - 4}")
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        _, days = calendar.monthrange(self.year, self.month)
        for day in range(1, days + 1):
            d = date(self.year, self.month, day)
            if self._cell(d).contains(e.position()):
                self.selected = d
                self.daySelected.emit(d)
                self.update()
                return

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        _, days = calendar.monthrange(self.year, self.month)
        for day in range(1, days + 1):
            d = date(self.year, self.month, day)
            if self._cell(d).contains(e.position()):
                evs = self.events.get(d)
                self.setToolTip("\n".join(f"{lab} — {title}" for lab, title, _ in evs[:8]) if evs else d.strftime("%A %d %B %Y"))
                return

    def keyPressEvent(self, e) -> None:  # noqa: N802
        cur = self.selected or date(self.year, self.month, 1)
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -7, Qt.Key.Key_Down: 7}.get(e.key())
        if step is None:
            super().keyPressEvent(e)
            return
        nd = date.fromordinal(cur.toordinal() + step)
        if nd.month == self.month and nd.year == self.year:
            self.selected = nd
            self.daySelected.emit(nd)
            self.update()


class MonthCalendar(QWidget):
    monthChanged = pyqtSignal(int, int)
    daySelected = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        prev = IconButton("chevron_right", "Previous month")
        prev.setStyleSheet("QToolButton { }")
        nxt = IconButton("chevron_right", "Next month")
        self._title = label("", "h3")
        head.addWidget(prev)
        head.addWidget(self._title, 1, Qt.AlignmentFlag.AlignCenter)
        head.addWidget(nxt)
        lay.addLayout(head)
        self.grid = _Grid()
        lay.addWidget(self.grid, 1)
        prev.clicked.connect(lambda: self.shift(-1))
        nxt.clicked.connect(lambda: self.shift(1))
        self.grid.daySelected.connect(self.daySelected)
        # flip the "previous" chevron
        from PyQt6.QtGui import QTransform
        pm = prev.icon().pixmap(18, 18).transformed(QTransform().rotate(180))
        from PyQt6.QtGui import QIcon
        prev.setIcon(QIcon(pm))
        self._refresh_title()

    def shift(self, months: int) -> None:
        total = self.grid.year * 12 + self.grid.month - 1 + months
        self.grid.year, self.grid.month = divmod(total, 12)
        self.grid.month += 1
        self.grid.selected = None
        self._refresh_title()
        self.grid.update()
        self.monthChanged.emit(self.grid.year, self.grid.month)

    def set_month(self, year: int, month: int) -> None:
        self.grid.year, self.grid.month = year, month
        self._refresh_title()
        self.grid.update()

    def set_events(self, events: dict[date, list[tuple[str, str, str]]]) -> None:
        self.grid.events = events
        self.grid.update()

    def _refresh_title(self) -> None:
        self._title.setText(f"{calendar.month_name[self.grid.month]} {self.grid.year}")

    @property
    def year(self) -> int:
        return self.grid.year

    @property
    def month(self) -> int:
        return self.grid.month
