"""Integrations: service-health tiles, implemented external actions (calendar export, webhook) and their audit trail."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from app.core.errors import ContractLensError
from app.models.enums import IntegrationProvider, ValidationStatus
from app.security.access import Permission
from app.ui.components.base import clear_layout, label
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog
from app.ui.components.primitives import GlassPanel, NeonButton, StatusBadge
from app.ui.context import BaseScreen
from app.ui.theme import icons
from app.ui.theme.tokens import SPACE, theme, tone_color


class _HealthTile(GlassPanel):
    def __init__(self, icon_name: str, name: str, ok: bool, detail: str) -> None:
        super().__init__(padding=SPACE["md"], accent=tone_color("success" if ok else "warning"))
        self.setMinimumHeight(118)
        head = QHBoxLayout()
        ic = label("")
        ic.setPixmap(icons.pixmap(icon_name, tone_color("success" if ok else "warning"), 20))
        head.addWidget(ic)
        head.addWidget(label(name, "h3"), 1)
        head.addWidget(StatusBadge("ready" if ok else "attention", "success" if ok else "warning"))
        self.body.addLayout(head)
        d = label(detail, "faint", wrap=True)
        d.setToolTip(detail)
        self.body.addWidget(d)
        self.body.addStretch(1)


class IntegrationsScreen(BaseScreen):
    nav_id = "integrations"
    title = "Integrations"
    eyebrow = "External actions & services"
    icon_name = "integrations"

    def build(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 10, 8)
        lay.setSpacing(SPACE["lg"])
        scroll.setWidget(host)

        lay.addWidget(label("SERVICE HEALTH", "faint"))
        self.health = QGridLayout()
        self.health.setSpacing(SPACE["md"])
        lay.addLayout(self.health)

        lay.addWidget(label("EXTERNAL ACTIONS", "faint"))
        row = QHBoxLayout()
        row.setSpacing(SPACE["lg"])
        row.addWidget(self._calendar_card(), 1)
        row.addWidget(self._webhook_card(), 1)
        lay.addLayout(row)
        lay.addWidget(label("Safeguards apply to every external action: the integration must be enabled by an administrator, you need the permission, "
                            "only human-confirmed deadlines can be sent, you confirm each send, and every attempt (allowed or denied) is audited.", "faint", wrap=True))

        lay.addWidget(label("AUDIT TRAIL", "faint"))
        self.audit = DataTable([
            Column("When", lambda a: a.created_at, lambda v, r: v.strftime("%d %b %H:%M"), width=110),
            Column("Actor", lambda a: a.actor_email or "", width=190),
            Column("Action", lambda a: a.action, kind="badge", tone=lambda v, r: "danger" if "denied" in v else "success" if "completed" in v else "info", width=150),
            Column("Detail", lambda a: ", ".join(f"{k}={v}" for k, v in (a.metadata or {}).items() if k != "source"), stretch=True),
        ], empty_text="No external actions have been attempted yet.")
        self.audit.setMinimumHeight(260)
        panel = GlassPanel("External action audit trail", "Requires audit-log permission to view")
        panel.body.addWidget(self.audit)
        lay.addWidget(panel)
        lay.addStretch(1)
        return scroll

    # ------------------------------------------------------------------ cards
    def _calendar_card(self) -> GlassPanel:
        card = GlassPanel("Calendar export (.ics)", "Writes confirmed deadlines to a file any calendar can import")
        self.cal_badge = StatusBadge("disabled", "neutral")
        card.add_action(self.cal_badge)
        self.cal_enabled = QCheckBox("Enable calendar export for this workspace")
        card.body.addWidget(self.cal_enabled)
        card.body.addWidget(label("Confirmation is always required. Events are all-day with a 7-day reminder.", "faint", wrap=True))
        row = QHBoxLayout()
        save = NeonButton("Save", "primary", "check")
        save.clicked.connect(self._save_cal)
        self.cal_export = NeonButton("Export confirmed deadlines…", "default", "download")
        self.cal_export.clicked.connect(self._export)
        row.addWidget(save)
        row.addWidget(self.cal_export)
        row.addStretch(1)
        card.body.addLayout(row)
        self.cal_note = label("", "faint", wrap=True)
        card.body.addWidget(self.cal_note)
        card.setMinimumHeight(230)
        return card

    def _webhook_card(self) -> GlassPanel:
        card = GlassPanel("Webhook", "Posts confirmed deadlines to one allowlisted HTTPS endpoint")
        self.hook_badge = StatusBadge("disabled", "neutral")
        card.add_action(self.hook_badge)
        self.hook_url = QLineEdit()
        self.hook_url.setPlaceholderText("https://hooks.example.com/contractlens")
        self.hook_url.setAccessibleName("Webhook URL")
        card.body.addWidget(label("Endpoint URL", "faint"))
        card.body.addWidget(self.hook_url)
        self.hook_enabled = QCheckBox("Enable webhook")
        card.body.addWidget(self.hook_enabled)
        card.body.addWidget(label("The URL's host becomes the only allowed target. Secrets are never stored here and redirects are not followed.", "faint", wrap=True))
        save = NeonButton("Save", "primary", "check")
        save.clicked.connect(self._save_hook)
        row = QHBoxLayout()
        row.addWidget(save)
        row.addStretch(1)
        card.body.addLayout(row)
        self.hook_note = label("", "faint", wrap=True)
        card.body.addWidget(self.hook_note)
        card.setMinimumHeight(230)
        return card

    # ------------------------------------------------------------------ data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        can_manage = ws.principal.can(Permission.INTEGRATIONS_MANAGE)
        audit = []
        try:
            audit = ws.admin.audit_log(limit=400, entity_type="tool")
        except ContractLensError:
            audit = []  # members without audit permission simply see no trail
        confirmed = sum(1 for r in ws.deadlines.list() if r.deadline.validation_status is ValidationStatus.CONFIRMED and r.deadline.due_date)
        return {"integrations": ws.integrations.list() if can_manage else [], "info": ws.system_info(), "audit": audit, "can": can_manage, "confirmed": confirmed}

    def render(self, data: dict[str, Any]) -> None:
        info = data["info"]
        clear_layout(self.health)
        tiles = [
            ("db", "Backend", True, "Supabase — row-level security enforced server-side" if not info.is_demo else "Local SQLite demo mode. Add Supabase settings to .env for teams."),
            ("cloud", "AI (OpenAI)", info.ai_configured, info.model if info.ai_configured else "Not configured. Set OPENAI_API_KEY or OPENAI_BASE_URL."),
            ("layers", "Embeddings", "Offline" not in info.embedder_note, f"{info.embedder} — {info.embedder_note}"),
            ("cube", "Vector index", not info.index_error, f"{info.vector_backend}: {info.vector_note or 'ready'}" + (f" — {info.index_error}" if info.index_error else "")),
            ("evidence", "OCR (Tesseract)", info.ocr_available, "Ready." if info.ocr_available else "Not installed. Scanned PDFs are rejected with an explanation."),
        ]
        for i, (ic, name, ok, detail) in enumerate(tiles):
            self.health.addWidget(_HealthTile(ic, name, ok, detail), i // 3, i % 3)
        for c in range(3):
            self.health.setColumnStretch(c, 1)
        by = {i.provider: i for i in data["integrations"]}
        cal, hook = by.get(IntegrationProvider.CALENDAR_ICS), by.get(IntegrationProvider.WEBHOOK)
        can = data["can"]
        self.cal_enabled.setChecked(bool(cal and cal.enabled))
        self.hook_enabled.setChecked(bool(hook and hook.enabled))
        self.hook_url.setText(str(hook.config.get("url", "")) if hook else "")
        for w in (self.cal_enabled, self.hook_enabled, self.hook_url):
            w.setEnabled(can)
        self.cal_badge.set("enabled" if cal and cal.enabled else "disabled", "success" if cal and cal.enabled else "neutral")
        self.hook_badge.set("enabled" if hook and hook.enabled else "disabled", "success" if hook and hook.enabled else "neutral")
        self.cal_export.setEnabled(bool(cal and cal.enabled) and data["confirmed"] > 0)
        self.cal_note.setText(f"{data['confirmed']} confirmed deadline(s) available to export." + ("" if can else " Only administrators can change this setting."))
        self.hook_note.setText("" if can else "Only administrators can change this setting.")
        self.audit.set_rows(data["audit"])

    # ------------------------------------------------------------------ actions
    def _save(self, fn, ok: str) -> None:
        self.ctx.run(fn, lambda _r: (self.ctx.toast(ok, "success"), self.mark_stale(), self.load()), name="integration save")

    def _save_cal(self) -> None:
        en = self.cal_enabled.isChecked()
        self._save(lambda: self.ctx.ws.integrations.save(IntegrationProvider.CALENDAR_ICS, "Calendar export (.ics)", enabled=en, requires_confirmation=True), "Calendar integration saved.")

    def _save_hook(self) -> None:
        url, en = self.hook_url.text().strip(), self.hook_enabled.isChecked()
        host = urlparse(url).hostname
        self._save(lambda: self.ctx.ws.integrations.save(IntegrationProvider.WEBHOOK, "Ticketing webhook", config={"url": url, "allowed_hosts": [host] if host else []},
                                                         enabled=en, requires_confirmation=True), "Webhook saved.")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export calendar", "contractlens-deadlines.ics", "iCalendar (*.ics)")
        if not path or not ConfirmationDialog.ask(self, "Export to calendar file", "Only human-confirmed deadlines are exported. The action is recorded in the audit log.", confirm_text="Export"):
            return
        ws = self.ctx.ws

        def work():
            ids = [r.deadline.id for r in ws.deadlines.list() if r.deadline.validation_status is ValidationStatus.CONFIRMED and r.deadline.due_date]
            return ws.integrations.export_calendar(ids, path, confirmed=True)

        self.ctx.run(work, lambda n: (self.ctx.toast(f"Exported {n} deadline(s).", "success"), self.mark_stale(), self.load()), name="export ics")


_ = (theme,)
