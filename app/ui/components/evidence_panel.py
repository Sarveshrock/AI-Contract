"""EvidencePanel: verified source quotes with location and an "Open source" action."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QPropertyAnimation, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.ui.components.base import clear_layout, label
from app.ui.components.primitives import NeonButton, StatusBadge
from app.ui.theme.motion import Motion
from app.ui.theme.tokens import SPACE


@dataclass
class EvidenceVM:
    quote: str
    page: int | None = None
    section: str | None = None
    verified: bool = True
    method: str | None = None
    contract_id: str | None = None
    contract_title: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    version_id: str | None = None
    subject: str | None = None
    label: str | None = None

    @classmethod
    def from_row(cls, e: Any, contract_title: str | None = None, subject: str | None = None) -> "EvidenceVM":
        return cls(e.quote, e.page_number, e.section_reference, e.verified, e.verification_method, str(e.contract_id), contract_title,
                   str(e.document_id) if e.document_id else None, str(e.chunk_id) if e.chunk_id else None, str(e.contract_version_id), subject, e.field_name)

    @property
    def location(self) -> str:
        parts = []
        if self.section:
            parts.append(f"§{self.section}")
        if self.page:
            parts.append(f"page {self.page}")
        return " · ".join(parts) or "location unknown"


class EvidenceItem(QFrame):
    openRequested = pyqtSignal(object)

    COLLAPSED = 74

    def __init__(self, vm: EvidenceVM, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        self.setProperty("panel", "card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        lay.setSpacing(SPACE["sm"])
        head = QHBoxLayout()
        if vm.contract_title:
            head.addWidget(label(vm.contract_title, "h3" if False else "muted"), 1)
        else:
            head.addStretch(1)
        head.addWidget(StatusBadge("verified" if vm.verified else "unverified", "success" if vm.verified else "danger"))
        lay.addLayout(head)
        self._quote = label(f"“{vm.quote}”", "quote", wrap=True, selectable=True)
        lay.addWidget(self._quote)
        foot = QHBoxLayout()
        foot.addWidget(label(vm.location + (f" · {vm.subject}" if vm.subject else ""), "faint"), 1)
        self._toggle = NeonButton("Expand", "ghost")
        self._toggle.setVisible(len(vm.quote) > 160)
        self._toggle.clicked.connect(self._flip)
        foot.addWidget(self._toggle)
        openb = NeonButton("Open source", "default", "external")
        openb.setToolTip("Open the source passage in the contract viewer")
        openb.clicked.connect(lambda: self.openRequested.emit(self.vm))
        foot.addWidget(openb)
        lay.addLayout(foot)
        self._expanded = False
        if len(vm.quote) > 160:
            self._quote.setMaximumHeight(self.COLLAPSED)

    def _flip(self) -> None:
        self._expanded = not self._expanded
        target = self._quote.sizeHint().height() + 400 if self._expanded else self.COLLAPSED
        self._toggle.setText("Collapse" if self._expanded else "Expand")
        if Motion.enabled:
            anim = QPropertyAnimation(self._quote, b"maximumHeight", self)
            anim.setDuration(180)
            anim.setStartValue(self._quote.maximumHeight())
            anim.setEndValue(target)
            anim.start()
            self._anim = anim
        else:
            self._quote.setMaximumHeight(target)


class EvidencePanel(QWidget):
    openRequested = pyqtSignal(object)

    def __init__(self, title: str = "Evidence", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["sm"])
        self._title = label(title, "h3")
        self._count = label("", "faint")
        head = QHBoxLayout()
        head.addWidget(self._title)
        head.addWidget(self._count)
        head.addStretch(1)
        outer.addLayout(head)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 4, 0)
        self._list.setSpacing(SPACE["sm"])
        self._list.addStretch(1)
        self._scroll.setWidget(host)
        outer.addWidget(self._scroll, 1)
        self._empty = label("Select an item to see its source evidence.", "muted", wrap=True)
        self._list.insertWidget(0, self._empty)

    def set_evidence(self, items: list[EvidenceVM], empty_text: str = "No verified evidence is attached to this item.") -> None:
        while self._list.count() > 1:
            it = self._list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not items:
            self._empty = label(empty_text, "muted", wrap=True)
            self._list.insertWidget(0, self._empty)
            self._count.setText("")
            return
        self._count.setText(f"{len(items)} passage{'s' if len(items) != 1 else ''}")
        for i, vm in enumerate(items):
            w = EvidenceItem(vm)
            w.openRequested.connect(self.openRequested)
            self._list.insertWidget(i, w)

    def clear(self) -> None:
        self.set_evidence([], "Select an item to see its source evidence.")


_ = clear_layout
