"""ReviewPanel: a human decision surface for one review case."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from app.models.entities import ReviewCase
from app.models.enums import ReviewDecision
from app.ui.components.base import label
from app.ui.components.evidence_panel import EvidencePanel, EvidenceVM
from app.ui.components.primitives import GlassPanel, NeonButton, StatusBadge
from app.ui.theme.tokens import SPACE, severity_tone, status_tone


class ReviewPanel(GlassPanel):
    decided = pyqtSignal(object, str, dict)   # ReviewDecision, notes, correction
    dismissed = pyqtSignal(str)
    openEvidence = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Review workbench", "AI output is a proposal. Your decision is recorded in the audit log.", parent=parent)
        self._case: ReviewCase | None = None
        self._title = label("Select a review case", "h3", wrap=True)
        self._badges = QHBoxLayout()
        self._reason = label("", "muted", wrap=True)
        self._proposed = label("", "quote", wrap=True)
        self._proposed.setVisible(False)
        self.body.addWidget(self._title)
        self.body.addLayout(self._badges)
        self.body.addWidget(self._reason)
        self.body.addWidget(self._proposed)
        self.evidence = EvidencePanel("Supporting evidence")
        self.evidence.openRequested.connect(self.openEvidence)
        self.evidence.setMinimumHeight(140)
        self.body.addWidget(self.evidence, 1)
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText("Decision notes (required for reject / correct)")
        self._notes.setFixedHeight(64)
        self.body.addWidget(self._notes)
        self._corr_host = QWidget()
        form = QFormLayout(self._corr_host)
        form.setContentsMargins(0, 0, 0, 0)
        self._corr_title = QLineEdit()
        self._corr_desc = QLineEdit()
        self._corr_title.setPlaceholderText("Corrected title (obligations only)")
        self._corr_desc.setPlaceholderText("Corrected description (obligations only)")
        form.addRow("Title", self._corr_title)
        form.addRow("Description", self._corr_desc)
        self._corr_host.setVisible(False)
        self.body.addWidget(self._corr_host)
        row = QHBoxLayout()
        self._approve = NeonButton("Approve", "success", "check")
        self._reject = NeonButton("Reject", "danger", "close")
        self._correct = NeonButton("Correct…", "default", "edit")
        self._escalate = NeonButton("Escalate", "ghost", "flag")
        self._dismiss = NeonButton("Dismiss", "ghost")
        for b in (self._approve, self._reject, self._correct, self._escalate):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self._dismiss)
        self.body.addLayout(row)
        self._approve.clicked.connect(lambda: self._emit(ReviewDecision.APPROVED))
        self._reject.clicked.connect(lambda: self._emit(ReviewDecision.REJECTED))
        self._escalate.clicked.connect(lambda: self._emit(ReviewDecision.ESCALATED))
        self._correct.clicked.connect(self._toggle_correct)
        self._dismiss.clicked.connect(lambda: self.dismissed.emit(self._notes.toPlainText().strip()))
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        for b in (self._approve, self._reject, self._correct, self._escalate, self._dismiss):
            b.setEnabled(on)

    def _toggle_correct(self) -> None:
        if not self._corr_host.isVisible():
            self._corr_host.setVisible(True)
            self._correct.setText("Submit correction")
            return
        self._emit(ReviewDecision.CORRECTED)

    def _emit(self, decision: ReviewDecision) -> None:
        correction: dict[str, Any] = {}
        if decision is ReviewDecision.CORRECTED:
            if self._corr_title.text().strip():
                correction["title"] = self._corr_title.text().strip()
            if self._corr_desc.text().strip():
                correction["description"] = self._corr_desc.text().strip()
        self.decided.emit(decision, self._notes.toPlainText().strip(), correction)

    def show_case(self, case: ReviewCase | None, evidence: list[EvidenceVM] | None = None) -> None:
        self._case = case
        self._notes.clear()
        self._corr_host.setVisible(False)
        self._correct.setText("Correct…")
        while self._badges.count():
            it = self._badges.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if case is None:
            self._title.setText("Select a review case")
            self._reason.setText("")
            self._proposed.setVisible(False)
            self.evidence.clear()
            self._set_enabled(False)
            return
        self._title.setText(case.title)
        self._badges.addWidget(StatusBadge(case.priority.value, severity_tone(case.priority.value)))
        self._badges.addWidget(StatusBadge(case.subject_type.value, "info"))
        self._badges.addWidget(StatusBadge(case.status.value, status_tone(case.status.value)))
        self._badges.addStretch(1)
        self._reason.setText(case.reason)
        pc = case.proposed_change or {}
        if pc:
            self._proposed.setText(f"Proposed change: {pc.get('field', '').replace('_', ' ')}  {pc.get('old', '—')} → {pc.get('value', '—')}\nNothing is applied until you approve.")
            self._proposed.setVisible(True)
        else:
            self._proposed.setVisible(False)
        self.evidence.set_evidence(evidence or [], "No verified evidence is attached. That is itself a reason for careful review.")
        obl = case.subject_type.value == "obligation"
        self._correct.setEnabled(obl)
        self._correct.setToolTip("" if obl else "Corrections are available for obligations")
        self._set_enabled(True)
        self._correct.setEnabled(obl)

    @property
    def case(self) -> ReviewCase | None:
        return self._case


_ = (QVBoxLayout, SPACE)
