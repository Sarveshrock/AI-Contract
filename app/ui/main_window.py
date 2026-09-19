"""Main window: top bar, navigation, animated screen stack, status bar and background services."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QPainter, QRadialGradient, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.container import AppContainer, WorkspaceContext
from app.core.errors import ContractLensError
from app.core.logging import get_logger
from app.security.access import Principal
from app import APP_NAME, __version__
from app.ui.components.agents_and_timeline import AgentStatusPill
from app.ui.components.base import elide, label
from app.ui.components.evidence_panel import EvidenceVM
from app.ui.components.primitives import NeonButton, SearchBar, StatusBadge
from app.ui.components.toast import ToastManager
from app.ui.context import BaseScreen, UiContext
from app.ui.theme import icons
from app.ui.theme.motion import Motion, slide_fade_in
from app.ui.theme.qss import build_qss
from app.ui.theme.tokens import DARK, HIGH_CONTRAST, SPACE, set_theme, severity_tone, theme
from app.workers.tasks import TaskRunner

log = get_logger(__name__)

NAV = [
    ("command", "Command Center", "command"), ("contracts", "Contract Intelligence", "contract"), ("obligations", "Obligation Operations", "obligations"),
    ("renewals", "Renewal Radar", "radar"), ("risk", "Risk Observatory", "risk"), ("evidence", "Evidence Explorer", "evidence"), ("copilot", "AI Copilot", "copilot"),
    ("runs", "Agent Runs", "runs"), ("integrations", "Integrations", "integrations"), ("admin", "Administration", "admin"),
]


def screen_factories() -> dict[str, type[BaseScreen]]:
    """Import every screen eagerly: a broken screen must fail loudly, never be replaced by a stub."""
    from app.ui.administration.admin_screen import AdministrationScreen
    from app.ui.agent_runs.runs_screen import AgentRunsScreen
    from app.ui.contracts.contracts_screen import ContractsScreen
    from app.ui.copilot.copilot_screen import CopilotScreen
    from app.ui.dashboard.command_center import CommandCenterScreen
    from app.ui.evidence.evidence_screen import EvidenceExplorerScreen
    from app.ui.integrations.integrations_screen import IntegrationsScreen
    from app.ui.obligations.obligations_screen import ObligationsScreen
    from app.ui.renewals.renewals_screen import RenewalsScreen
    from app.ui.risk.risk_screen import RiskObservatoryScreen

    return {"command": CommandCenterScreen, "contracts": ContractsScreen, "obligations": ObligationsScreen, "renewals": RenewalsScreen, "risk": RiskObservatoryScreen,
            "evidence": EvidenceExplorerScreen, "copilot": CopilotScreen, "runs": AgentRunsScreen, "integrations": IntegrationsScreen, "admin": AdministrationScreen}


class _Backdrop(QWidget):
    """Deep-navy backdrop with two soft radial accents (kept subtle for readability)."""

    def paintEvent(self, e) -> None:  # noqa: N802
        t = theme()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.bg0))
        if t.name != "high-contrast":
            for (cx, cy, color, radius) in ((0.08, 0.0, t.cyan, 0.55), (0.98, 1.0, t.violet, 0.6)):
                g = QRadialGradient(QPointF(self.width() * cx, self.height() * cy), max(self.width(), self.height()) * radius)
                c = QColor(color)
                c.setAlphaF(0.075)
                g.setColorAt(0.0, c)
                c.setAlphaF(0.0)
                g.setColorAt(1.0, c)
                p.fillRect(self.rect(), g)
        p.end()


class _BellButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self.setIcon(icons.icon("bell", theme().text_dim, 20))
        self.setToolTip("Notifications")
        self.setAccessibleName("Notifications")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 38)

    def set_count(self, n: int) -> None:
        self._count = n
        self.setToolTip(f"Notifications ({n} unread)" if n else "Notifications")
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self._count:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.width() - 20, 2, 18, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme().danger))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#12060A"))
            f = p.font()
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "9+" if self._count > 9 else str(self._count))
            p.end()


class NotificationPopup(QFrame):
    """Popup listing unread alerts with acknowledge actions."""

    def __init__(self, ctx: UiContext, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setProperty("panel", "glass")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(430)
        self.ctx = ctx
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(8)

    def populate(self) -> None:
        while self._lay.count():
            it = self._lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    x = it.layout().takeAt(0)
                    if x.widget():
                        x.widget().deleteLater()
        head = QHBoxLayout()
        head.addWidget(label("Notifications", "h3"), 1)
        radar = NeonButton("Renewal Radar", "ghost", "radar")
        radar.clicked.connect(lambda: (self.close(), self.ctx.navigate("renewals", None)))
        head.addWidget(radar)
        self._lay.addLayout(head)
        alerts = self.ctx.ws.alerts.list(limit=8)
        if not alerts:
            self._lay.addWidget(label("You're all caught up.", "muted"))
        for a in alerts:
            row = QHBoxLayout()
            row.addWidget(StatusBadge(a.severity.value, severity_tone(a.severity.value)))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(elide(a.title, 52)))
            col.addWidget(label(elide(a.message, 80), "faint"))
            row.addLayout(col, 1)
            if a.status.value == "unread":
                b = NeonButton("Acknowledge", "ghost")
                b.clicked.connect(lambda _=False, aid=a.id: self._ack(aid))
                row.addWidget(b)
            self._lay.addLayout(row)
        self.adjustSize()

    def _ack(self, alert_id) -> None:
        self.ctx.run(lambda: self.ctx.ws.alerts.acknowledge(alert_id), lambda _a: (self.ctx.refresh_badges(), self.populate()), name="acknowledge alert")


class MainWindow(QMainWindow):
    def __init__(self, container: AppContainer, principals: list[Principal], active: Principal, qsettings) -> None:
        super().__init__()
        self.container = container
        self.principals = principals
        self.qsettings = qsettings
        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(icons.icon("logo", theme().cyan, 64, 1.6))
        self.setMinimumSize(1120, 700)
        self.resize(1480, 920)
        self._sign_out_requested = False
        self.runner = TaskRunner(self)
        self.ws: WorkspaceContext = container.open_workspace(active)
        self.ctx = UiContext(self.ws, self.runner, qsettings)
        self._factories = screen_factories()
        self._current: str | None = None
        self._build_chrome()
        self.ctx.toast = lambda message, tone="info": self.toasts.show(message, tone)
        self.ctx.navigate = self.navigate
        self.ctx.open_contract = self.open_contract
        self.ctx.open_evidence = self.open_evidence
        self.ctx.refresh_badges = self.refresh_badges
        self._shortcuts()
        self._timers()
        self.navigate("command")
        QTimer.singleShot(400, self._startup_tasks)

    # ------------------------------------------------------------------ chrome
    def _build_chrome(self) -> None:
        central = _Backdrop()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._topbar())
        self._banner = self._demo_banner()
        root.addWidget(self._banner)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._nav())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self.toasts = ToastManager(central)
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_labels: dict[str, QLabel] = {}
        for key in ("mode", "vector", "embed", "ai", "tasks"):
            lb = QLabel()
            self._status_labels[key] = lb
            sb.addWidget(lb) if key != "tasks" else sb.addPermanentWidget(lb)
        self._update_status()

    def _topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(64)
        bar.setStyleSheet(f"QFrame#TopBar {{ background: rgba(6,10,18,0.85); border: none; border-bottom: 1px solid {theme().border}; }}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 8, 18, 8)
        h.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.pixmap("logo", theme().cyan, 30, 1.6))
        h.addWidget(logo)
        word = QVBoxLayout()
        word.setSpacing(0)
        w1 = QLabel("CONTRACTLENS")
        w1.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {theme().text}; letter-spacing: 2px; background: transparent; border: none;")
        w2 = QLabel("ENTERPRISE · CONTRACT INTELLIGENCE")
        w2.setStyleSheet(f"font-size: 9px; font-weight: 600; color: {theme().cyan}; background: transparent; border: none;")
        word.addWidget(w1)
        word.addWidget(w2)
        h.addLayout(word)
        h.addSpacing(10)
        self.workspace = QComboBox()
        self.workspace.setAccessibleName("Workspace selector")
        self.workspace.setMinimumWidth(190)
        for p in self.principals:
            self.workspace.addItem(f"{p.org_name or 'Workspace'}  ·  {p.role.value.replace('_', ' ')}", p)
        idx = next((i for i, p in enumerate(self.principals) if p.org_id == self.ws.principal.org_id), 0)
        self.workspace.setCurrentIndex(idx)
        self.workspace.currentIndexChanged.connect(self._switch_workspace)
        h.addWidget(self.workspace)
        self.search = SearchBar("Semantic search across contracts  (Ctrl+K)")
        self.search.setMinimumWidth(320)
        self.search.setMaximumWidth(560)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.submitted.connect(self._global_search)
        h.addWidget(self.search, 1)
        h.addStretch(0)
        self.agent_pill = AgentStatusPill()
        self.agent_pill.clicked.connect(lambda: self.navigate("runs"))
        h.addWidget(self.agent_pill)
        self.bell = _BellButton()
        self.bell.clicked.connect(self._show_notifications)
        h.addWidget(self.bell)
        self.user_btn = QToolButton()
        self.user_btn.setIcon(icons.icon("user", theme().text, 20))
        self.user_btn.setText(f"  {self.ws.principal.display_name}")
        self.user_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.user_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.user_btn.setAccessibleName("User menu")
        self.user_btn.setMenu(self._user_menu())
        self.user_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        h.addWidget(self.user_btn)
        return bar

    def _user_menu(self) -> QMenu:
        m = QMenu(self)
        p = self.ws.principal
        head = QAction(f"{p.email} — {p.role.value.replace('_', ' ')}", m)
        head.setEnabled(False)
        m.addAction(head)
        m.addSeparator()
        self.act_motion = QAction("Reduce motion", m, checkable=True)
        self.act_motion.setChecked(self.qsettings.value("reduced_motion", self.ws.settings.reduced_motion, type=bool))
        self.act_motion.toggled.connect(self._set_reduced_motion)
        m.addAction(self.act_motion)
        self.act_contrast = QAction("High-contrast theme", m, checkable=True)
        self.act_contrast.setChecked(self.qsettings.value("high_contrast", False, type=bool))
        self.act_contrast.toggled.connect(self._set_high_contrast)
        m.addAction(self.act_contrast)
        m.addSeparator()
        out = QAction("Sign out", m)
        out.triggered.connect(self._sign_out)
        m.addAction(out)
        return m

    def _demo_banner(self) -> QWidget:
        w = QFrame()
        w.setObjectName("DemoBanner")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setFixedHeight(28)
        w.setStyleSheet("QFrame#DemoBanner { background: rgba(139,124,255,0.16); border: none; border-bottom: 1px solid rgba(139,124,255,0.45); }")
        h = QHBoxLayout(w)
        h.setContentsMargins(18, 0, 18, 0)
        txt = QLabel("DEMO / LOCAL MODE — sample data and a local index. Sample-contract analysis uses curated fixtures instead of a live model. "
                     "Configure Supabase and OpenAI in .env for production use.")
        txt.setStyleSheet(f"color: {theme().violet}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        h.addWidget(txt)
        w.setVisible(self.ws.settings.is_demo)
        return w

    def _nav(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("NavBar")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setFixedWidth(232)
        frame.setStyleSheet(f"QFrame#NavBar {{ background: rgba(8,13,24,0.7); border: none; border-right: 1px solid {theme().border}; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(4)
        self._nav_buttons: dict[str, NeonButton] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for i, (nid, title, icon_name) in enumerate(NAV):
            b = NeonButton(f"  {title}", "default", None)
            b.setProperty("variant", None)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setIcon(icons.icon(icon_name, theme().text_dim, 20))
            b.setToolTip(f"{title}  (Ctrl+{(i + 1) % 10})")
            b.setAccessibleName(title)
            b.clicked.connect(lambda _=False, n=nid: self.navigate(n))
            self._nav_buttons[nid] = b
            lay.addWidget(b)
        lay.addStretch(1)
        up = NeonButton("Upload contract", "primary", "upload")
        up.setToolTip("Upload a PDF or DOCX (Ctrl+U)")
        up.clicked.connect(self.upload_contract)
        lay.addWidget(up)
        lay.addWidget(label(f"v{__version__}", "faint", align=Qt.AlignmentFlag.AlignCenter))
        return frame

    # ------------------------------------------------------------------ navigation
    def _screen(self, nid: str) -> BaseScreen:
        if nid not in self.ctx.screens:
            screen = self._factories[nid](self.ctx)
            self.ctx.screens[nid] = screen
            self.stack.addWidget(screen)
        return self.ctx.screens[nid]

    def navigate(self, nid: str, params: dict[str, Any] | None = None) -> None:
        if nid not in self._factories:
            return
        screen = self._screen(nid)
        changed = nid != self._current
        self._current = nid
        self.stack.setCurrentWidget(screen)
        for k, b in self._nav_buttons.items():
            b.setChecked(k == nid)
        screen.on_show(params)
        if changed:
            slide_fade_in(screen)

    def open_contract(self, contract_id, *, page: int | None = None, quote: str | None = None, tab: str | None = None, version_id: str | None = None) -> None:
        self.navigate("contracts", {"contract_id": str(contract_id), "page": page, "quote": quote, "tab": tab, "version_id": version_id})

    def open_evidence(self, vm: EvidenceVM) -> None:
        if vm.contract_id:
            self.open_contract(vm.contract_id, page=vm.page, quote=vm.quote, version_id=vm.version_id)

    def _global_search(self, text: str) -> None:
        if text:
            self.navigate("evidence", {"query": text})

    # ------------------------------------------------------------------ actions
    def upload_contract(self) -> None:
        self.navigate("contracts", {"upload": True})

    def _show_notifications(self) -> None:
        pop = NotificationPopup(self.ctx, self)
        pop.populate()
        pos = self.bell.mapToGlobal(self.bell.rect().bottomRight())
        pop.move(pos.x() - pop.width(), pos.y() + 6)
        pop.show()
        self._popup = pop

    def _set_reduced_motion(self, on: bool) -> None:
        Motion.set_reduced(on)
        self.qsettings.setValue("reduced_motion", on)
        self.toasts.show("Animations reduced." if on else "Animations enabled.", "info")

    def _set_high_contrast(self, on: bool) -> None:
        self.qsettings.setValue("high_contrast", on)
        apply_theme(QApplication.instance(), on)
        self.toasts.show("High-contrast theme on." if on else "Standard theme on.", "info")
        for s in self.ctx.screens.values():
            s.mark_stale()
        self.update()
        if self._current:
            self.ctx.screens[self._current].load()

    def _sign_out(self) -> None:
        self._sign_out_requested = True
        self.close()

    def _switch_workspace(self, idx: int) -> None:
        p = self.workspace.itemData(idx)
        if p is None or p.org_id == self.ws.principal.org_id:
            return
        self.ws = self.container.open_workspace(p)
        self.ctx.ws = self.ws
        for s in list(self.ctx.screens.values()):
            self.stack.removeWidget(s)
            s.deleteLater()
        self.ctx.screens.clear()
        self._current = None
        self.navigate("command")
        self.toasts.show(f"Switched to {p.org_name}.", "success")

    # ------------------------------------------------------------------ background services
    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Ctrl+U"), self, activated=self.upload_contract)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self._current and self.ctx.screens[self._current].load())
        for i, (nid, _t, _i) in enumerate(NAV):
            QShortcut(QKeySequence(f"Ctrl+{(i + 1) % 10}"), self, activated=lambda n=nid: self.navigate(n))

    def _timers(self) -> None:
        self._poll = QTimer(self)
        self._poll.setInterval(60_000)
        self._poll.timeout.connect(self._periodic)
        self._poll.start()
        self.runner.busyChanged.connect(self._busy)

    def _startup_tasks(self) -> None:
        def work():
            recovered = self.ws.ingestion.recover_interrupted() if self.ws.principal.can(__import__("app.security.access", fromlist=["Permission"]).Permission.CONTRACTS_WRITE) else []
            new = self.ws.alerts.scan() if self.ws.principal.can(__import__("app.security.access", fromlist=["Permission"]).Permission.ALERTS_MANAGE) else []
            return recovered, new

        def done(res) -> None:
            recovered, new = res
            if recovered:
                self.toasts.show(f"Resumed {len(recovered)} interrupted document(s).", "info")
            if new:
                self.toasts.show(f"{len(new)} new alert(s) need attention.", "warning")
            self.refresh_badges()
            if self.ws.index_error:
                self.toasts.show(self.ws.index_error, "warning", 9000)

        self.ctx.run(work, done, name="startup", on_error=lambda e: log.warning("startup tasks failed: %s", type(e).__name__))
        self.refresh_badges()

    def _periodic(self) -> None:
        self.ctx.run(lambda: self.ws.alerts.scan(), lambda new: (self.toasts.show(f"{len(new)} new alert(s).", "warning") if new else None, self.refresh_badges()), name="periodic scan",
                     on_error=lambda e: log.warning("periodic scan failed"))

    def refresh_badges(self) -> None:
        self.ctx.run(lambda: self.ws.alerts.unread_count(), self.bell.set_count, name="badge", on_error=lambda e: None)

    def _busy(self, n: int) -> None:
        ai = sum(1 for name in self.runner.active_names() if name.startswith(("analysis", "upload", "compare")))
        self.agent_pill.set_running(ai, self.ws.llm.model if self.ws.llm.available else "")
        self._status_labels["tasks"].setText(f"{n} background task{'s' if n != 1 else ''}" if n else "Ready")

    def _update_status(self) -> None:
        info = self.ws.system_info()
        s = self._status_labels
        s["mode"].setText(("DEMO/LOCAL" if info.is_demo else "SUPABASE") + "  |  ")
        s["vector"].setText(f"Vectors: {info.vector_backend}  |  ")
        s["embed"].setText(f"Embeddings: {info.embedder}  |  ")
        s["ai"].setText(f"AI: {info.model if info.ai_configured else 'not configured'}  |  OCR: {'ready' if info.ocr_available else 'unavailable'}")
        s["tasks"].setText("Ready")
        s["vector"].setToolTip(info.vector_note)
        s["embed"].setToolTip(info.embedder_note)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "toasts"):
            self.toasts.relayout()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._poll.stop()
        if not self.runner.wait_idle(3000):
            log.warning("closing with background tasks still running")
        super().closeEvent(e)

    @property
    def sign_out_requested(self) -> bool:
        return self._sign_out_requested


def apply_theme(app: QApplication | None, high_contrast: bool) -> None:
    set_theme(HIGH_CONTRAST if high_contrast else DARK)
    if app is not None:
        app.setStyleSheet(build_qss(theme()))


_ = (QFileDialog, ContractLensError, SPACE)
