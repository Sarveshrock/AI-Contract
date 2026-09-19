"""ContractCard and ObligationCard."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from app.services.contracts import ContractSummary
from app.services.obligations import ObligationRow
from app.ui.components.base import elide, fmt_date, label, repolish
from app.ui.components.primitives import StatusBadge
from app.ui.theme.tokens import SPACE, severity_tone, status_tone


class _ClickableCard(QFrame):
    clicked = pyqtSignal(object)

    def __init__(self, payload: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self.setProperty("panel", "card")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._payload)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._payload)
        else:
            super().keyPressEvent(e)


class ContractCard(_ClickableCard):
    def __init__(self, s: ContractSummary, parent: QWidget | None = None) -> None:
        super().__init__(s.contract.id, parent)
        c = s.contract
        self.setAccessibleName(f"Contract {c.title}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label(elide(c.title, 46), "h3"), 1)
        if c.is_demo:
            top.addWidget(StatusBadge("demo", "violet"))
        top.addWidget(StatusBadge(c.status.value, status_tone(c.status.value)))
        lay.addLayout(top)
        lay.addWidget(label(elide(" · ".join(s.parties) or "Parties not identified yet", 70), "muted"))
        meta = QHBoxLayout()
        meta.addWidget(StatusBadge(c.contract_type.value, "info"))
        exp = f"Expires {fmt_date(c.expiration_date)}" if c.expiration_date else "Expiration not established"
        meta.addWidget(label(exp, "faint"))
        meta.addStretch(1)
        lay.addLayout(meta)
        foot = QHBoxLayout()
        foot.addWidget(label(f"{s.open_obligations} open obligations", "faint"))
        if s.open_reviews:
            foot.addWidget(StatusBadge(f"{s.open_reviews} in review queue", "warning"))
        if s.indexing_status and s.indexing_status != "indexed":
            foot.addWidget(StatusBadge(s.indexing_status, status_tone(s.indexing_status)))
        if c.analysis_status.value in ("pending", "failed", "running"):
            foot.addWidget(StatusBadge(f"analysis {c.analysis_status.value}", status_tone(c.analysis_status.value)))
        foot.addStretch(1)
        lay.addLayout(foot)
        if c.business_risk_score is not None:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(c.business_risk_score))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setToolTip(f"Business review score {c.business_risk_score:.0f}/100 (extraction uncertainty {c.extraction_uncertainty_score or 0:.0f}). A review-priority aid, not a legal conclusion.")
            lay.addWidget(bar)


class ObligationCard(_ClickableCard):
    def __init__(self, r: ObligationRow, parent: QWidget | None = None) -> None:
        super().__init__(r.obligation.id, parent)
        o = r.obligation
        self.setAccessibleName(f"Obligation {o.title}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(elide(o.title, 60), "muted"), 1)
        top.addWidget(StatusBadge(r.status_label, status_tone(r.status_label)))
        lay.addLayout(top)
        lay.addWidget(label(f"{r.contract_title} · {o.responsible_party_name or 'party unknown'} → {o.beneficiary_name or '—'}", "faint"))
        foot = QHBoxLayout()
        foot.addWidget(label(f"Due {fmt_date(r.next_due)}" if r.next_due else "No date yet", "faint"))
        foot.addWidget(label(f"Owner: {r.owner_name or 'unassigned'}", "faint"))
        foot.addStretch(1)
        if o.review_status.value == "needs_review":
            foot.addWidget(StatusBadge("needs review", "warning"))
        lay.addLayout(foot)


_ = (severity_tone,)
