"""AgentStatusPanel (+ compact pill) and TimelineItem/TimelineView."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app.ui.components.base import clear_layout, label
from app.ui.components.primitives import StatusBadge
from app.ui.theme import icons
from app.ui.theme.motion import Motion, stagger
from app.ui.theme.tokens import SPACE, theme, tone_color

AGENTS = [
    ("supervisor", "Supervisor", "Classifies the task, plans the workflow, coordinates agents and manages retries."),
    ("document_intelligence", "Document Intelligence", "Structure, OCR quality, related documents, embedded-instruction scan."),
    ("contract_extraction", "Contract Extraction", "Parties, dates, financial, renewal and termination terms, clauses."),
    ("obligation_intelligence", "Obligation Intelligence", "Responsible party, trigger, conditions, frequency, deadline text."),
    ("temporal_reasoning", "Temporal Reasoning", "Deterministic deadline calculation with traces."),
    ("amendment_intelligence", "Amendment Intelligence", "Version and amendment comparison."),
    ("risk_triage", "Risk Triage", "Review signals: ambiguity, gaps, exposure, playbook deviations."),
    ("evidence_qa", "Evidence & QA", "Schema, citation, date and party validation; routes low quality to review."),
    ("grounded_copilot", "Grounded Copilot", "Answers from retrieved passages with verified citations."),
]
_STATE_TONE = {"idle": "neutral", "running": "cyan", "ok": "success", "warning": "warning", "failed": "danger", "skipped": "neutral"}


class _PulseDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._state = "idle"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "running" and Motion.enabled:
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(tone_color(_STATE_TONE.get(self._state, "neutral")))
        if self._state == "running" and Motion.enabled:
            ring = QColor(color)
            ring.setAlphaF(max(0.0, 0.5 * (1 - self._phase)))
            p.setPen(QPen(ring, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = 3 + 4 * self._phase
            p.drawEllipse(QPointF(self.rect().center()), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(self.rect().center()), 3.5, 3.5)
        p.end()


class AgentStatusPanel(QWidget):
    """Lists every agent with its live state; used on the Agent Runs screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["xs"])
        self._rows: dict[str, tuple[_PulseDot, QLabel, QLabel]] = {}
        for key, title, desc in AGENTS:
            row = QFrame()
            row.setProperty("panel", "card")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            h = QHBoxLayout(row)
            h.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
            dot = _PulseDot()
            h.addWidget(dot)
            text = QVBoxLayout()
            text.setSpacing(0)
            text.addWidget(label(title, "muted"))
            text.addWidget(label(desc, "faint", wrap=True))
            h.addLayout(text, 1)
            state = label("idle", "faint")
            h.addWidget(state)
            lay.addWidget(row)
            self._rows[key] = (dot, state, row)  # type: ignore[assignment]

    def set_state(self, agent: str, state: str, note: str = "") -> None:
        if agent in self._rows:
            dot, lab, _ = self._rows[agent]  # type: ignore[misc]
            dot.set_state(state)
            lab.setText(note or state)

    def reset(self) -> None:
        for k in self._rows:
            self.set_state(k, "idle")


class AgentStatusPill(QFrame):
    """Compact top-bar indicator: idle / N running."""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", "sunken")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("AI agent status")
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 12, 4)
        h.setSpacing(6)
        self._dot = _PulseDot()
        h.addWidget(self._dot)
        self._icon = QLabel()
        self._icon.setPixmap(icons.pixmap("cpu", theme().text_dim, 16))
        h.addWidget(self._icon)
        self._text = label("Agents idle", "muted")
        h.addWidget(self._text)

    def set_running(self, n: int, model: str = "") -> None:
        self._dot.set_state("running" if n else "idle")
        self._text.setText(f"{n} agent run{'s' if n != 1 else ''} active" if n else "Agents idle")
        self.setToolTip(f"Model: {model}" if model else "")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class TimelineItem(QWidget):
    def __init__(self, title: str, subtitle: str = "", when: str = "", tone: str = "cyan", last: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone, self._last = tone, last
        h = QHBoxLayout(self)
        h.setContentsMargins(26, 2, 0, 10)
        h.setSpacing(SPACE["md"])
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(title, None, wrap=True))
        if subtitle:
            col.addWidget(label(subtitle, "faint", wrap=True))
        h.addLayout(col, 1)
        if when:
            h.addWidget(label(when, "faint"), 0, Qt.AlignmentFlag.AlignTop)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = theme()
        c = QColor(tone_color(self._tone))
        line = QColor(t.border_hi)
        p.setPen(QPen(line, 1.2))
        if not self._last:
            p.drawLine(9, 14, 9, self.height())
        p.setPen(Qt.PenStyle.NoPen)
        halo = QColor(c)
        halo.setAlphaF(0.22)
        p.setBrush(halo)
        p.drawEllipse(3, 8, 12, 12)
        p.setBrush(c)
        p.drawEllipse(6, 11, 6, 6)
        p.end()


class TimelineView(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._host = QWidget()
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 4, 0)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)
        self.setWidget(self._host)

    def set_items(self, items: list[tuple[str, str, str, str]], empty: str = "No activity yet.", animate: bool = True) -> None:
        """items: (title, subtitle, when, tone)"""
        while self._lay.count() > 1:
            it = self._lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not items:
            self._lay.insertWidget(0, label(empty, "muted"))
            return
        widgets = []
        for i, (title, sub, when, tone) in enumerate(items):
            w = TimelineItem(title, sub, when, tone, last=i == len(items) - 1)
            self._lay.insertWidget(i, w)
            widgets.append(w)
        if animate:
            stagger(widgets[:12])


_ = (clear_layout, StatusBadge)
