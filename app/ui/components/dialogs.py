"""ConfirmationDialog and prompt dialogs. Consequential actions always pass through these."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from app.ui.components.base import label
from app.ui.components.primitives import NeonButton
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, theme


class _Base(QDialog):
    def __init__(self, parent: QWidget | None, title: str, width: int = 460) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["lg"])
        self.root.setSpacing(SPACE["md"])


class ConfirmationDialog(_Base):
    """Explicit confirmation with an optional "I have verified this" gate for external/consequential actions."""

    def __init__(self, parent: QWidget | None, title: str, message: str, *, confirm_text: str = "Confirm", danger: bool = False, verify_text: str | None = None,
                 detail: str | None = None) -> None:
        super().__init__(parent, title)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("alert" if danger else "shield_check", theme().danger if danger else theme().cyan, 30))
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.addWidget(label(title, "h3"))
        text.addWidget(label(message, "muted", wrap=True))
        if detail:
            d = label(detail, "faint", wrap=True)
            text.addWidget(d)
        head.addLayout(text, 1)
        self.root.addLayout(head)
        self._check: QCheckBox | None = None
        if verify_text:
            self._check = QCheckBox(verify_text)
            self.root.addWidget(self._check)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = NeonButton("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        self._ok = NeonButton(confirm_text, "danger" if danger else "primary")
        self._ok.clicked.connect(self.accept)
        self._ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(self._ok)
        self.root.addLayout(row)
        if self._check is not None:
            self._ok.setEnabled(False)
            self._check.toggled.connect(self._ok.setEnabled)

    @staticmethod
    def ask(parent: QWidget | None, title: str, message: str, **kw) -> bool:
        return ConfirmationDialog(parent, title, message, **kw).exec() == QDialog.DialogCode.Accepted


class TextPromptDialog(_Base):
    def __init__(self, parent: QWidget | None, title: str, prompt: str, *, placeholder: str = "", min_len: int = 1, confirm_text: str = "Save", initial: str = "") -> None:
        super().__init__(parent, title, 520)
        self.root.addWidget(label(title, "h3"))
        self.root.addWidget(label(prompt, "muted", wrap=True))
        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.setPlainText(initial)
        self._edit.setFixedHeight(110)
        self.root.addWidget(self._edit)
        self._min = min_len
        self._hint = label("", "faint")
        self.root.addWidget(self._hint)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = NeonButton("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        self._ok = NeonButton(confirm_text, "primary")
        self._ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self._ok)
        self.root.addLayout(row)
        self._edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        n = len(self._edit.toPlainText().strip())
        self._ok.setEnabled(n >= self._min)
        self._hint.setText("" if n >= self._min else f"Enter at least {self._min} characters.")

    def value(self) -> str:
        return self._edit.toPlainText().strip()

    @staticmethod
    def ask(parent: QWidget | None, title: str, prompt: str, **kw) -> str | None:
        dlg = TextPromptDialog(parent, title, prompt, **kw)
        return dlg.value() if dlg.exec() == QDialog.DialogCode.Accepted else None


_ = QDialogButtonBox
