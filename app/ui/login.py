"""Sign-in dialog: Supabase Auth in production, one-click local demo workspace otherwise."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from app.core.container import AppContainer
from app.core.errors import ContractLensError
from app.core.logging import get_logger
from app.security.access import Principal
from app.ui.components.base import label
from app.ui.components.primitives import NeonButton, StatusBadge
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, theme

log = get_logger(__name__)


class LoginDialog(QDialog):
    def __init__(self, container: AppContainer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.container = container
        self.principals: list[Principal] = []
        self.setWindowTitle("ContractLens Enterprise — Sign in")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 34, 40, 30)
        root.setSpacing(SPACE["md"])
        logo = QLabel()
        logo.setPixmap(icons.pixmap("logo", theme().cyan, 54, 1.5))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(logo)
        root.addWidget(label("CONTRACTLENS ENTERPRISE", "h2", align=Qt.AlignmentFlag.AlignCenter))
        root.addWidget(label("AI CONTRACT INTELLIGENCE & OBLIGATION OPERATIONS", "eyebrow", align=Qt.AlignmentFlag.AlignCenter))
        s = container.settings
        card = QFrame()
        card.setProperty("panel", "glass")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 22, 24, 22)
        cl.setSpacing(SPACE["md"])
        self.error = label("", None, wrap=True)
        self.error.setStyleSheet(f"color: {theme().danger};")
        self.error.setVisible(False)
        if s.mode == "supabase":
            cl.addWidget(label("Sign in with your Supabase account", "h3"))
            self.email = QLineEdit()
            self.email.setPlaceholderText("E-mail")
            self.email.setAccessibleName("E-mail")
            self.password = QLineEdit()
            self.password.setPlaceholderText("Password")
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.password.setAccessibleName("Password")
            self.password.returnPressed.connect(self._sign_in)
            self.email.returnPressed.connect(self.password.setFocus)
            cl.addWidget(self.email)
            cl.addWidget(self.password)
            self.btn = NeonButton("Sign in", "primary", "lock")
        else:
            cl.addWidget(label("Local demo mode", "h3"))
            cl.addWidget(label("No Supabase project is configured, so ContractLens runs on a local database with a single demo user. Data stays on this machine. Add SUPABASE_URL and SUPABASE_ANON_KEY to .env for team use with server-side access control.", "muted", wrap=True))
            self.btn = NeonButton("Enter demo workspace", "primary", "play")
        self.btn.clicked.connect(self._sign_in)
        cl.addWidget(self.error)
        cl.addWidget(self.btn)
        root.addWidget(card)
        badges = QHBoxLayout()
        badges.addStretch(1)
        badges.addWidget(StatusBadge("supabase" if s.mode == "supabase" else "demo / local", "success" if s.mode == "supabase" else "violet"))
        badges.addWidget(StatusBadge("ai ready" if s.ai_configured else "ai not configured", "success" if s.ai_configured else "warning"))
        badges.addWidget(StatusBadge(container.vector_info.backend, "info"))
        badges.addStretch(1)
        root.addLayout(badges)
        if container.vector_info.backend == "sqlite-fallback":
            root.addWidget(label(container.vector_info.note, "faint", wrap=True, align=Qt.AlignmentFlag.AlignCenter))
        root.addWidget(label("ContractLens assists professionals and does not provide legal advice.", "faint", align=Qt.AlignmentFlag.AlignCenter))

    def _sign_in(self) -> None:
        self.error.setVisible(False)
        self.btn.set_busy(True, "Signing in…")
        try:
            if self.container.settings.mode == "supabase":
                self.principals = self.container.sign_in(self.email.text(), self.password.text())
            else:
                self.principals = self.container.sign_in()
        except ContractLensError as exc:
            self.error.setText(exc.user_message)
            self.error.setVisible(True)
            self.btn.set_busy(False)
            return
        except Exception:  # noqa: BLE001
            log.exception("sign-in failed")
            self.error.setText("Sign-in failed unexpectedly. Details were written to the log.")
            self.error.setVisible(True)
            self.btn.set_busy(False)
            return
        self.accept()
