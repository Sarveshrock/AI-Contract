"""DataTable: a Qt Model/View table driven by column specs, with badge cells, sorting and text filtering."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QHeaderView, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView, QVBoxLayout, QWidget

from app.ui.theme.tokens import is_classic, theme, tone_color

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
TONE_ROLE = Qt.ItemDataRole.UserRole + 2
OBJ_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class Column:
    title: str
    get: Callable[[Any], Any]
    fmt: Callable[[Any, Any], str] | None = None       # (value, row) -> text
    kind: str = "text"                                 # text | badge | mono | number | progress
    tone: Callable[[Any, Any], str] | None = None      # (value, row) -> tone (badge columns)
    width: int | None = None
    stretch: bool = False
    sort: Callable[[Any], Any] | None = None


class RowModel(QAbstractTableModel):
    def __init__(self, columns: list[Column], parent=None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.rows: list[Any] = []

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section].title.upper()
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = self.rows[index.row()], self.columns[index.column()]
        value = col.get(row)
        if role == Qt.ItemDataRole.DisplayRole:
            text = col.fmt(value, row) if col.fmt else ("" if value is None else str(value))
            return text.replace("_", " ").upper() if col.kind == "badge" else text
        if role == SORT_ROLE:
            return col.sort(value) if col.sort else (value if isinstance(value, (int, float)) else str(value or "").lower())
        if role == TONE_ROLE:
            return col.tone(value, row) if col.tone else "neutral"
        if role == OBJ_ROLE:
            return row
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) if col.kind == "number" else int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return col.fmt(value, row) if col.fmt else (str(value) if value is not None else None)
        return None


class BadgeDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        model = index.model()
        src = model.sourceModel() if isinstance(model, QSortFilterProxyModel) else model
        kind = src.columns[index.column()].kind
        if kind == "progress":
            self._progress(painter, option, index)
            return
        if kind != "badge":
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)  # selection / hover background
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not text:
            return
        color = QColor(tone_color(index.data(TONE_ROLE) or "neutral"))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(option.font)
        f.setPixelSize(11)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        w = fm.horizontalAdvance(text) + 18
        r = QRectF(option.rect.left() + 8, option.rect.center().y() - 10, min(w, option.rect.width() - 12), 20)
        if is_classic():
            from app.ui.theme.classic import BADGES

            tone = index.data(TONE_ROLE) or "neutral"
            fill, fg = BADGES.get(tone, BADGES["neutral"])
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QColor("#404040"))
            painter.setBrush(QColor(fill))
            painter.drawRect(r)
            painter.setPen(QColor(fg))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 10)))
            painter.restore()
            return
        bg = QColor(color)
        bg.setAlphaF(0.15)
        painter.setBrush(bg)
        border = QColor(color)
        border.setAlphaF(0.5)
        painter.setPen(border)
        painter.drawRoundedRect(r, 10, 10)
        painter.setPen(color)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 10)))
        painter.restore()

    def _progress(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        try:
            value = float(index.data(SORT_ROLE) or 0)
        except (TypeError, ValueError):
            value = 0.0
        t = theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect.left() + 8, option.rect.center().y() - 4, option.rect.width() - 56, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.border))
        painter.drawRoundedRect(r, 4, 4)
        tone = "success" if value < 30 else "warning" if value < 60 else "danger"
        painter.setBrush(QColor(tone_color(tone)))
        painter.drawRoundedRect(QRectF(r.left(), r.top(), r.width() * max(0.0, min(1.0, value / 100)), r.height()), 4, 4)
        painter.setPen(QColor(t.text_dim))
        painter.drawText(QRectF(r.right() + 6, option.rect.top(), 44, option.rect.height()), Qt.AlignmentFlag.AlignVCenter, f"{value:.0f}")
        painter.restore()


class _Proxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._text = ""

    def set_text(self, text: str) -> None:
        self._text = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        if not self._text:
            return True
        m = self.sourceModel()
        return any(self._text in str(m.index(row, c, parent).data(Qt.ItemDataRole.DisplayRole) or "").lower() for c in range(m.columnCount()))


class DataTable(QWidget):
    """Table + empty overlay. Emits the *row object* (not an index) on selection/activation."""

    rowSelected = pyqtSignal(object)
    rowActivated = pyqtSignal(object)

    def __init__(self, columns: list[Column], *, empty_text: str = "No rows to show.", row_height: int = 38, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model_ = RowModel(columns, self)
        self.proxy = _Proxy(self)
        self.proxy.setSourceModel(self.model_)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setItemDelegate(BadgeDelegate(self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(False)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(row_height)
        self.view.horizontalHeader().setHighlightSections(False)
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.setAccessibleName("Data table")
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self._empty = QLabel(empty_text, self.view)
        self._empty.setProperty("role", "muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._configure_columns(columns)
        self.view.selectionModel().selectionChanged.connect(self._on_selection)
        self.view.doubleClicked.connect(lambda idx: self.rowActivated.emit(idx.data(OBJ_ROLE)))
        self.view.activated.connect(lambda idx: self.rowActivated.emit(idx.data(OBJ_ROLE)))
        self.proxy.modelReset.connect(self._toggle_empty)
        self.proxy.layoutChanged.connect(self._toggle_empty)

    def _configure_columns(self, columns: list[Column]) -> None:
        hdr = self.view.horizontalHeader()
        for i, c in enumerate(columns):
            if c.width:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.view.setColumnWidth(i, c.width)
            elif c.stretch:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

    def set_rows(self, rows: list[Any], *, keep_selection: bool = True) -> None:
        prev = self.selected() if keep_selection else None
        self.model_.set_rows(rows)
        self._toggle_empty()
        if prev is not None:
            for i, r in enumerate(rows):
                if r is prev or getattr(r, "id", object()) == getattr(prev, "id", None) or (hasattr(r, "obligation") and hasattr(prev, "obligation") and r.obligation.id == prev.obligation.id):
                    self.select_row(i)
                    break

    def set_empty_text(self, text: str) -> None:
        self._empty.setText(text)

    def _toggle_empty(self) -> None:
        self._empty.setVisible(self.proxy.rowCount() == 0)
        self._empty.setGeometry(self.view.viewport().rect().adjusted(20, 40, -20, -20))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._toggle_empty()

    def _on_selection(self) -> None:
        self.rowSelected.emit(self.selected())

    def selected(self) -> Any | None:
        idx = self.view.currentIndex()
        return idx.data(OBJ_ROLE) if idx.isValid() and self.view.selectionModel().isSelected(idx) else None

    def select_row(self, source_row: int) -> None:
        idx = self.proxy.mapFromSource(self.model_.index(source_row, 0))
        if idx.isValid():
            self.view.selectRow(idx.row())

    def filter(self, text: str) -> None:
        self.proxy.set_text(text)
        self._toggle_empty()

    def count(self) -> int:
        return self.proxy.rowCount()

    def rows(self) -> list[Any]:
        return [self.proxy.index(i, 0).data(OBJ_ROLE) for i in range(self.proxy.rowCount())]
