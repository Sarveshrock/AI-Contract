"""Chart widgets: Donut, Bars, Heatmap, RiskMatrix, Trend line (PyQtGraph) and Sparkline. All painted from theme tokens."""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from app.ui.theme.tokens import theme, tone_color

PALETTE_TOKENS = ("cyan", "violet", "success", "warning", "danger", "info", "indigo")


def palette() -> list[QColor]:
    t = theme()
    return [QColor(getattr(t, k)) for k in PALETTE_TOKENS]


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(size)
    f.setBold(bold)
    return f


class _ChartBase(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(150)
        self._empty_text = "No data yet"

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(QColor(theme().text_faint))
        p.setFont(_font(12))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)


class DonutChart(_ChartBase):
    def __init__(self, parent: QWidget | None = None, colors: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self._data: dict[str, float] = {}
        self._colors = colors or {}
        self.setAccessibleName("Donut chart")

    def set_data(self, data: dict[str, float], center_label: str = "total") -> None:
        self._data = {k: v for k, v in data.items() if v}
        self._center_label = center_label
        self.setAccessibleDescription(", ".join(f"{k}: {v:g}" for k, v in self._data.items()))
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._data:
            self._paint_empty(p)
            return
        t = theme()
        total = sum(self._data.values())
        side = min(self.height() - 8, self.width() * 0.5)
        ring = QRectF(6, (self.height() - side) / 2, side, side)
        pal = palette()
        start = 90 * 16
        for i, (k, v) in enumerate(self._data.items()):
            color = QColor(self._colors.get(k) or pal[i % len(pal)])
            span = -int(360 * 16 * v / total)
            pen = QPen(color, side * 0.16)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.drawArc(ring.adjusted(side * 0.08, side * 0.08, -side * 0.08, -side * 0.08), start, span)
            start += span
        p.setPen(QColor(t.text))
        p.setFont(_font(int(side * 0.2), True))
        p.drawText(ring, Qt.AlignmentFlag.AlignCenter, f"{total:g}")
        p.setPen(QColor(t.text_faint))
        p.setFont(_font(11))
        p.drawText(QRectF(ring.left(), ring.center().y() + side * 0.1, ring.width(), 20), Qt.AlignmentFlag.AlignCenter, self._center_label)
        # legend
        x, y = ring.right() + 16, max(8.0, (self.height() - len(self._data) * 22) / 2)
        p.setFont(_font(12))
        for i, (k, v) in enumerate(self._data.items()):
            color = QColor(self._colors.get(k) or pal[i % len(pal)])
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y + 5, 10, 10), 3, 3)
            p.setPen(QColor(t.text_dim))
            p.drawText(QRectF(x + 16, y, self.width() - x - 20, 20), Qt.AlignmentFlag.AlignVCenter, f"{k.replace('_', ' ')}  ·  {v:g}")
            y += 22
        p.end()


class BarChart(_ChartBase):
    def __init__(self, parent: QWidget | None = None, *, horizontal: bool = False, color: str = "cyan") -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[float] = []
        self._horizontal = horizontal
        self._color = color
        self.setAccessibleName("Bar chart")

    def set_data(self, labels: Sequence[str], values: Sequence[float], color: str | None = None) -> None:
        self._labels, self._values = [str(x) for x in labels], [float(v) for v in values]
        if color:
            self._color = color
        self.setAccessibleDescription(", ".join(f"{a}: {b:g}" for a, b in zip(self._labels, self._values)))
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._values or not any(self._values):
            self._paint_empty(p)
            return
        t = theme()
        base = QColor(tone_color(self._color))
        mx = max(self._values)
        n = len(self._values)
        if self._horizontal:
            label_w = min(150.0, max(60.0, self.width() * 0.32))
            row_h = min(28.0, (self.height() - 8) / n)
            p.setFont(_font(12))
            for i, (lab, v) in enumerate(zip(self._labels, self._values)):
                y = 4 + i * row_h
                p.setPen(QColor(t.text_dim))
                p.drawText(QRectF(0, y, label_w - 8, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, p.fontMetrics().elidedText(lab.replace("_", " "), Qt.TextElideMode.ElideRight, int(label_w - 10)))
                w = (self.width() - label_w - 44) * (v / mx)
                bar = QRectF(label_w, y + row_h * 0.2, max(w, 2), row_h * 0.6)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.border))
                p.drawRoundedRect(QRectF(label_w, bar.y(), self.width() - label_w - 44, bar.height()), 4, 4)
                p.setBrush(base)
                p.drawRoundedRect(bar, 4, 4)
                p.setPen(QColor(t.text))
                p.drawText(QRectF(label_w + w + 6, y, 40, row_h), Qt.AlignmentFlag.AlignVCenter, f"{v:g}")
        else:
            left, bottom, top = 8.0, 22.0, 16.0
            slot = (self.width() - left * 2) / n
            plot_h = self.height() - bottom - top
            p.setFont(_font(10))
            for i, (lab, v) in enumerate(zip(self._labels, self._values)):
                h = plot_h * (v / mx)
                x = left + i * slot + slot * 0.18
                bar = QRectF(x, top + plot_h - h, slot * 0.64, max(h, 2))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(base if v else QColor(t.border))
                p.drawRoundedRect(bar, 4, 4)
                p.setPen(QColor(t.text_faint))
                p.drawText(QRectF(left + i * slot, self.height() - bottom + 2, slot, bottom - 2), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, lab)
                if v:
                    p.setPen(QColor(t.text))
                    p.drawText(QRectF(left + i * slot, bar.y() - 15, slot, 14), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, f"{v:g}")
        p.end()


class Heatmap(_ChartBase):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[str] = []
        self._cols: list[str] = []
        self._grid: list[list[float]] = []
        self.setMinimumHeight(180)

    def set_data(self, rows: list[str], cols: list[str], grid: list[list[float]]) -> None:
        self._rows, self._cols, self._grid = rows, cols, grid
        self.setAccessibleDescription(f"Heatmap of {len(rows)} contracts by {len(cols)} signal types")
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows or not self._cols:
            self._paint_empty(p)
            return
        t = theme()
        mx = max((max(r) for r in self._grid if r), default=1.0) or 1.0
        left, top = min(170.0, self.width() * 0.3), 46.0
        cw = (self.width() - left - 6) / len(self._cols)
        ch = min(30.0, (self.height() - top - 4) / len(self._rows))
        p.setFont(_font(10))
        for j, c in enumerate(self._cols):
            p.save()
            p.translate(left + j * cw + cw / 2, top - 6)
            p.rotate(-30)
            p.setPen(QColor(t.text_faint))
            p.drawText(QRectF(0, -14, 120, 14), Qt.AlignmentFlag.AlignLeft, c.replace("_", " "))
            p.restore()
        low, hi = QColor(t.violet), QColor(t.danger)
        for i, r in enumerate(self._rows):
            y = top + i * ch
            p.setPen(QColor(t.text_dim))
            p.setFont(_font(11))
            p.drawText(QRectF(0, y, left - 8, ch), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, p.fontMetrics().elidedText(r, Qt.TextElideMode.ElideRight, int(left - 12)))
            for j, v in enumerate(self._grid[i]):
                cell = QRectF(left + j * cw + 2, y + 2, cw - 4, ch - 4)
                p.setPen(Qt.PenStyle.NoPen)
                if v <= 0:
                    p.setBrush(QColor(t.border))
                    c0 = QColor(t.border)
                    c0.setAlphaF(0.35)
                    p.setBrush(c0)
                else:
                    k = min(1.0, v / mx)
                    col = QColor(int(low.red() + (hi.red() - low.red()) * k), int(low.green() + (hi.green() - low.green()) * k), int(low.blue() + (hi.blue() - low.blue()) * k))
                    col.setAlphaF(0.35 + 0.6 * k)
                    p.setBrush(col)
                p.drawRoundedRect(cell, 4, 4)
                if v > 0:
                    p.setPen(QColor(t.text))
                    p.setFont(_font(10, True))
                    p.drawText(cell, Qt.AlignmentFlag.AlignCenter, f"{v:g}")
        p.end()


class RiskMatrix(_ChartBase):
    """Open findings by severity (rows) and category (columns: business risk / extraction uncertainty)."""

    SEVS = ("critical", "high", "medium", "low", "info")
    CATS = (("business_risk", "Business risk"), ("extraction_uncertainty", "Extraction uncertainty"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._counts: dict[tuple[str, str], int] = {}
        self.setMinimumHeight(190)

    def set_data(self, counts: dict[tuple[str, str], int]) -> None:
        self._counts = counts
        self.setAccessibleDescription("; ".join(f"{s}/{c}: {n}" for (s, c), n in counts.items()))
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        left, top = 70.0, 26.0
        cw = (self.width() - left - 4) / len(self.CATS)
        ch = (self.height() - top - 4) / len(self.SEVS)
        p.setFont(_font(11))
        for j, (_, name) in enumerate(self.CATS):
            p.setPen(QColor(t.text_faint))
            p.drawText(QRectF(left + j * cw, 0, cw, top), Qt.AlignmentFlag.AlignCenter, name)
        tones = {"critical": "danger", "high": "danger", "medium": "warning", "low": "info", "info": "neutral"}
        for i, sev in enumerate(self.SEVS):
            y = top + i * ch
            p.setPen(QColor(t.text_dim))
            p.drawText(QRectF(0, y, left - 8, ch), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, sev.capitalize())
            for j, (cat, _) in enumerate(self.CATS):
                n = self._counts.get((sev, cat), 0)
                color = QColor(tone_color(tones[sev]))
                cell = QRectF(left + j * cw + 3, y + 3, cw - 6, ch - 6)
                p.setPen(Qt.PenStyle.NoPen)
                fill = QColor(color)
                fill.setAlphaF(min(0.85, 0.10 + 0.18 * n) if n else 0.06)
                p.setBrush(fill)
                p.drawRoundedRect(cell, 6, 6)
                p.setPen(QColor(t.text if n else t.text_faint))
                p.setFont(_font(15, True))
                p.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(n))
                p.setFont(_font(11))
        p.end()


class Sparkline(_ChartBase):
    def __init__(self, parent: QWidget | None = None, color: str = "cyan") -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._color = color
        self.setMinimumHeight(40)

    def set_data(self, values: Sequence[float]) -> None:
        self._values = [float(v) for v in values]
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        if len(self._values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mx, mn = max(self._values), min(self._values)
        span = (mx - mn) or 1.0
        pts = [QPointF(4 + i * (self.width() - 8) / (len(self._values) - 1), 4 + (self.height() - 8) * (1 - (v - mn) / span)) for i, v in enumerate(self._values)]
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        c = QColor(tone_color(self._color))
        p.setPen(QPen(c, 1.8))
        p.drawPath(path)
        p.end()


class TrendChart(QWidget):
    """Line chart via PyQtGraph (dates on the x axis)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import pyqtgraph as pg

        self._pg = pg
        t = theme()
        pg.setConfigOptions(antialias=True, background=None, foreground=t.text_faint)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setBackground(None)
        self.plot.showGrid(x=False, y=True, alpha=0.15)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        for ax in ("left", "bottom"):
            a = self.plot.getAxis(ax)
            a.setPen(pg.mkPen(t.border_hi))
            a.setTextPen(pg.mkPen(t.text_faint))
        self.plot.setYRange(0, 100)
        self.plot.addLegend(offset=(10, 5), labelTextColor=t.text_dim)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot)
        self.setMinimumHeight(170)
        self._empty = None

    def set_series(self, series: dict[str, tuple[list[datetime], list[float], str]]) -> None:
        pg = self._pg
        self.plot.clear()
        for name, (xs, ys, tone) in series.items():
            if not xs:
                continue
            color = tone_color(tone)
            self.plot.plot([x.timestamp() for x in xs], ys, pen=pg.mkPen(color, width=2), symbol="o", symbolSize=6, symbolBrush=color, symbolPen=None, name=name)


_ = math
