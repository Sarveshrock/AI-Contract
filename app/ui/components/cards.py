"""ContractCard and ObligationCard."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from app.services.contracts import ContractSummary
from app.services.obligations import ObligationRow
from app.ui.components.base import elide, fmt_date, label, repolish
from app.ui.components.polish import Avatar, ScoreRing
from app.ui.components.primitives import StatusBadge
from app.ui.theme.tokens import SPACE, is_classic, severity_tone, status_tone, tone_color


def score_tone(score: float | None) -> str:
    """Review-priority scores: low is calm, high deserves attention."""
    if score is None:
        return "neutral"
    return "success" if score < 35 else "warning" if score < 65 else "danger"


class _ClickableCard(QFrame):
    clicked = pyqtSignal(object)

    def __init__(self, payload: object, parent: QWidget | None = None, *, stripe: str | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._stripe = stripe
        self.setProperty("panel", "card")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self._stripe and not is_classic():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tone_color(self._stripe)))
            p.drawRoundedRect(QRectF(0, 10, 3.5, self.height() - 20), 1.7, 1.7)
            p.end()

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
        c = s.contract
        super().__init__(c.id, parent, stripe=status_tone(c.status.value))
        self.setAccessibleName(f"Contract {c.title}")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACE["lg"] + 2, SPACE["md"], SPACE["lg"], SPACE["md"])
        outer.setSpacing(SPACE["lg"])
        outer.addWidget(Avatar("contract", size=42, tone="violet" if c.is_demo else "cyan"), 0, Qt.AlignmentFlag.AlignTop)
        lay = QVBoxLayout()
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
        outer.addLayout(lay, 1)
        if c.business_risk_score is not None:
            ring = ScoreRing(size=56)
            ring.set_value(c.business_risk_score, score_tone(c.business_risk_score))
            ring.setToolTip(f"Business review score {c.business_risk_score:.0f}/100 (extraction uncertainty {c.extraction_uncertainty_score or 0:.0f}). A review-priority aid, not a legal conclusion.")
            outer.addWidget(ring, 0, Qt.AlignmentFlag.AlignVCenter)


class ObligationCard(_ClickableCard):
    def __init__(self, r: ObligationRow, parent: QWidget | None = None) -> None:
        super().__init__(r.obligation.id, parent, stripe=status_tone(r.status_label))
        o = r.obligation
        self.setAccessibleName(f"Obligation {o.title}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["lg"] + 2, SPACE["sm"] + 2, SPACE["md"], SPACE["sm"] + 2)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(label(elide(o.title, 60), None), 1)
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
