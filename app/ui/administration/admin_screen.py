"""Administration: members, playbook, workspace settings, audit log and system maintenance."""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget, QDoubleSpinBox,
)

from app.models.entities import PlaybookRule
from app.models.enums import ClauseType, PlaybookRuleType, Role, Severity
from app.security.access import Permission
from app.ui.components.base import label
from app.ui.components.data_table import Column, DataTable
from app.ui.components.dialogs import ConfirmationDialog
from app.ui.components.primitives import GlassPanel, NeonButton, SearchBar
from app.ui.context import BaseScreen
from app.ui.theme.motion import Motion
from app.ui.theme.tokens import SPACE, status_tone

RULE_HELP = {
    PlaybookRuleType.REQUIRE_CLAUSE: "Requires a clause type (set the clause type).",
    PlaybookRuleType.MAX_NOTICE_DAYS: "Flags renewal/termination notice longer than N days.",
    PlaybookRuleType.MIN_NOTICE_DAYS: "Flags notice shorter than N days.",
    PlaybookRuleType.FORBID_AUTO_RENEWAL: "Flags any automatic renewal.",
    PlaybookRuleType.FORBID_PHRASE: "Flags phrases (comma separated).",
    PlaybookRuleType.MAX_PAYMENT_DAYS: "Flags payment periods longer than N days.",
    PlaybookRuleType.REQUIRE_GOVERNING_LAW: "Approved jurisdictions (comma separated).",
}


class AdministrationScreen(BaseScreen):
    nav_id = "admin"
    title = "Administration"
    eyebrow = "Workspace control"
    icon_name = "admin"

    def build(self) -> QWidget:
        tabs = QTabWidget()
        self.tabs = tabs
        tabs.addTab(self._scrolled(self._members_tab(), 420), "Members and roles")
        tabs.addTab(self._scrolled(self._playbook_tab(), 420), "Playbook")
        tabs.addTab(self._scrolled(self._settings_tab(), 460), "Workspace settings")
        tabs.addTab(self._scrolled(self._audit_tab(), 420), "Audit log")
        tabs.addTab(self._scrolled(self._system_tab(), 380), "System")
        return tabs

    @staticmethod
    def _scrolled(page: QWidget, min_height: int) -> QWidget:
        """Pages keep a usable minimum height and scroll instead of squeezing their contents."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QScrollArea

        page.setMinimumHeight(min_height)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(page)
        return area

    # -- members
    def _members_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.members = DataTable([
            Column("Name", lambda m: m.full_name or "", width=180), Column("E-mail", lambda m: m.email, stretch=True),
            Column("Role", lambda m: m.member.role.value, kind="badge", tone=lambda v, r: "cyan" if v in ("owner", "admin") else "info", width=150),
        ], empty_text="No members.")
        lay.addWidget(self.members, 1)
        row = QHBoxLayout()
        self.email = QLineEdit()
        self.email.setPlaceholderText("existing user's e-mail")
        self.role = QComboBox()
        for r in Role:
            self.role.addItem(r.value.replace("_", " ").title(), r)
        add = NeonButton("Add member", "primary", "plus")
        add.clicked.connect(self._add_member)
        chg = NeonButton("Change role", "default")
        chg.clicked.connect(self._change_role)
        rem = NeonButton("Remove", "danger", "trash")
        rem.clicked.connect(self._remove_member)
        for x in (self.email, self.role, add, chg, rem):
            row.addWidget(x)
        lay.addLayout(row)
        lay.addWidget(label("On Supabase, users must first sign up (or be invited in the Supabase dashboard). Roles are enforced by database policies as well as by this app.", "faint", wrap=True))
        return w

    def _add_member(self) -> None:
        e, r = self.email.text(), self.role.currentData()
        self._do(lambda: self.ctx.ws.admin.add_member(e, r), "Member added.")

    def _change_role(self) -> None:
        m = self.members.selected()
        if m:
            r = self.role.currentData()
            self._do(lambda: self.ctx.ws.admin.change_role(m.member.id, r), "Role updated.")

    def _remove_member(self) -> None:
        m = self.members.selected()
        if m and ConfirmationDialog.ask(self, "Remove member", f"Remove {m.email} from this workspace?", confirm_text="Remove", danger=True):
            self._do(lambda: self.ctx.ws.admin.remove_member(m.member.id), "Member removed.")

    # -- playbook
    def _playbook_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.rules = DataTable([
            Column("Rule", lambda r: r.name, stretch=True), Column("Type", lambda r: r.rule_type.value, kind="badge", tone=lambda v, r: "info", width=190),
            Column("Severity", lambda r: r.severity.value, kind="badge", tone=lambda v, r: status_tone(v), width=100),
            Column("Parameters", lambda r: ", ".join(f"{k}={v}" for k, v in r.params.items()) or (r.clause_type.value if r.clause_type else ""), stretch=True),
            Column("Enabled", lambda r: "yes" if r.enabled else "no", width=70),
        ], empty_text="No playbook rules yet. Rules turn your approved positions into review signals.")
        lay.addWidget(self.rules, 1)
        row = QHBoxLayout()
        new = NeonButton("New rule…", "primary", "plus")
        new.clicked.connect(lambda: self._edit_rule(None))
        edit = NeonButton("Edit…", "default", "edit")
        edit.clicked.connect(lambda: self._edit_rule(self.rules.selected()))
        rm = NeonButton("Delete", "danger", "trash")
        rm.clicked.connect(self._delete_rule)
        for b in (new, edit, rm):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _edit_rule(self, rule: PlaybookRule | None) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Playbook rule")
        dlg.setMinimumWidth(480)
        form = QFormLayout(dlg)
        name = QLineEdit(rule.name if rule else "")
        rtype = QComboBox()
        for t in PlaybookRuleType:
            rtype.addItem(t.value.replace("_", " ").title(), t)
        sev = QComboBox()
        for s in Severity:
            sev.addItem(s.value.title(), s)
        clause = QComboBox()
        clause.addItem("—", None)
        for c in ClauseType:
            clause.addItem(c.value.replace("_", " ").title(), c)
        param = QLineEdit()
        hint = label("", "faint", wrap=True)
        days = QSpinBox()
        days.setRange(0, 3650)
        if rule:
            rtype.setCurrentIndex(rtype.findData(rule.rule_type))
            sev.setCurrentIndex(sev.findData(rule.severity))
            clause.setCurrentIndex(max(0, clause.findData(rule.clause_type)))
            days.setValue(int(rule.params.get("days", 0)))
            param.setText(", ".join(rule.params.get("phrases", rule.params.get("allowed", []))))

        def upd() -> None:
            hint.setText(RULE_HELP[rtype.currentData()])
        rtype.currentIndexChanged.connect(lambda _i: upd())
        upd()
        ok = NeonButton("Save", "primary")
        ok.clicked.connect(dlg.accept)
        form.addRow("Name", name)
        form.addRow("Rule type", rtype)
        form.addRow("", hint)
        form.addRow("Severity", sev)
        form.addRow("Clause type", clause)
        form.addRow("Days (N)", days)
        form.addRow("Phrases / jurisdictions", param)
        form.addRow(ok)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t = rtype.currentData()
        items = [x.strip() for x in param.text().split(",") if x.strip()]
        params: dict[str, Any] = {}
        if t in (PlaybookRuleType.MAX_NOTICE_DAYS, PlaybookRuleType.MIN_NOTICE_DAYS, PlaybookRuleType.MAX_PAYMENT_DAYS):
            params["days"] = days.value()
        if t is PlaybookRuleType.FORBID_PHRASE:
            params["phrases"] = items
        if t is PlaybookRuleType.REQUIRE_GOVERNING_LAW:
            params["allowed"] = items
        new_rule = (rule.model_copy(update={}) if rule else PlaybookRule(org_id=self.ctx.ws.principal.org_id, name="x", rule_type=t)).model_copy(
            update={"name": name.text().strip() or "Untitled rule", "rule_type": t, "severity": sev.currentData(), "clause_type": clause.currentData(), "params": params})
        self._do(lambda: (self.ctx.ws.admin.save_rule(new_rule), self.ctx.ws.risk.rescore_all()), "Rule saved.")

    def _delete_rule(self) -> None:
        r = self.rules.selected()
        if r and ConfirmationDialog.ask(self, "Delete rule", f"Delete '{r.name}'?", confirm_text="Delete", danger=True):
            self._do(lambda: self.ctx.ws.admin.delete_rule(r.id), "Rule deleted.")

    # -- settings
    def _settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.offsets = QLineEdit()
        self.holidays = QPlainTextEdit()
        self.holidays.setFixedHeight(70)
        self.holidays.setPlaceholderText("One date per line, YYYY-MM-DD. Used for business-day calculations.")
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSpecialValueText("keep indefinitely")
        form.addRow("Alert offsets (days before deadline)", self.offsets)
        form.addRow("Public holidays", self.holidays)
        form.addRow("Retention after deletion (days)", self.retention)
        lay.addLayout(form)
        row = QHBoxLayout()
        save = NeonButton("Save settings", "primary", "check")
        save.clicked.connect(self._save_settings)
        preview = NeonButton("Preview purge", "default")
        preview.clicked.connect(lambda: self._purge(True))
        purge = NeonButton("Purge expired data…", "danger", "trash")
        purge.clicked.connect(lambda: self._purge(False))
        for b in (save, preview, purge):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.purge_out = label("", "muted", wrap=True)
        lay.addWidget(self.purge_out)
        lay.addStretch(1)
        return w

    def _save_settings(self) -> None:
        try:
            offs = [int(x) for x in self.offsets.text().replace(";", ",").split(",") if x.strip()]
        except ValueError:
            self.ctx.toast("Alert offsets must be whole numbers, e.g. 90, 60, 30, 14, 7, 1.", "danger")
            return
        hol = [x.strip() for x in self.holidays.toPlainText().splitlines() if x.strip()]
        days = self.retention.value() or None

        def work():
            self.ctx.ws.admin.update_settings(alert_offsets_days=offs, holidays=hol)
            self.ctx.ws.admin.set_retention_days(days)
        self._do(work, "Settings saved.")

    def _purge(self, dry: bool) -> None:
        if not dry and not ConfirmationDialog.ask(self, "Purge expired data", "Contracts deleted longer ago than the retention period are removed permanently, including documents and search entries.", confirm_text="Purge permanently", danger=True):
            return
        self.ctx.run(lambda: self.ctx.ws.admin.purge_expired(dry_run=dry), lambda names: self.purge_out.setText(
            (f"{len(names)} contract(s) " + ("would be purged: " if dry else "purged: ") + ", ".join(names)) if names else "Nothing to purge."), name="purge")

    # -- audit
    def _audit_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.audit_filter = SearchBar("Filter audit log")
        self.audit = DataTable([
            Column("When", lambda a: a.created_at, lambda v, r: v.strftime("%d %b %Y %H:%M:%S"), width=150), Column("Actor", lambda a: a.actor_email or "system", width=200),
            Column("Action", lambda a: a.action, width=200), Column("Entity", lambda a: a.entity_type, width=140),
            Column("Detail", lambda a: ", ".join(f"{k}={v}" for k, v in (a.metadata or {}).items() if k != "source")[:200], stretch=True),
        ], empty_text="No audit events (or you lack permission to read them).")
        self.audit_filter.queryChanged.connect(self.audit.filter)
        lay.addWidget(self.audit_filter)
        lay.addWidget(self.audit, 1)
        lay.addWidget(label("The audit log is append-only for clients: only server-side functions can write to it.", "faint"))
        return w

    # -- system
    def _system_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.sys_info = label("", "muted", wrap=True, selectable=True)
        lay.addWidget(self.sys_info)
        row = QHBoxLayout()
        rec = NeonButton("Recover interrupted documents", "default", "refresh")
        rec.clicked.connect(lambda: self._do(lambda: self.ctx.ws.ingestion.recover_interrupted(), "Recovery finished."))
        rei = NeonButton("Rebuild search index…", "default", "db")
        rei.clicked.connect(self._reindex)
        anp = NeonButton("Analyse pending contracts", "default", "cpu")
        anp.clicked.connect(self._analyze_pending)
        self.demo_btn = NeonButton("Load demo data", "primary", "layers")
        self.demo_btn.clicked.connect(self._demo)
        self.demo_rm = NeonButton("Remove demo data…", "danger", "trash")
        self.demo_rm.clicked.connect(self._demo_remove)
        for b in (rec, rei, anp, self.demo_btn, self.demo_rm):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.motion = NeonButton("Toggle reduced motion", "ghost", "sun")
        self.motion.clicked.connect(lambda: (Motion.set_reduced(Motion.enabled), self.ctx.toast("Animations " + ("reduced." if not Motion.enabled else "enabled."), "info")))
        lay.addWidget(self.motion)
        lay.addStretch(1)
        return w

    def _reindex(self) -> None:
        if ConfirmationDialog.ask(self, "Rebuild search index", "All documents are re-embedded with the current embedding model. This can take a while and uses the embedding API.", confirm_text="Rebuild"):
            self.ctx.toast("Rebuilding the search index…", "info")
            self._do(lambda: self.ctx.ws.reindex_all(), "Search index rebuilt.")

    def _analyze_pending(self) -> None:
        if not self.ctx.ws.llm.available:
            self.ctx.toast("AI is not configured; nothing can be analysed.", "warning")
            return
        self.ctx.toast("Analysis started…", "info")
        self.ctx.run(lambda: self.ctx.ws.contracts.analyze_pending(), lambda outs: (self.ctx.toast(f"Analysed {len(outs)} contract(s).", "success"), self.ctx.invalidate_all()), name="analysis pending")

    def _demo(self) -> None:
        from app.ui.demo_actions import load_demo_data

        load_demo_data(self.ctx)

    def _demo_remove(self) -> None:
        from app.ui.demo_actions import clear_demo_data

        clear_demo_data(self.ctx, self)

    def _do(self, fn, ok: str) -> None:
        self.ctx.run(fn, lambda _r: (self.ctx.toast(ok, "success"), self.ctx.invalidate_all(), self.mark_stale(), self.load()), name="admin action")

    # -- data
    def fetch(self) -> dict[str, Any]:
        ws = self.ctx.ws
        p = ws.principal
        out: dict[str, Any] = {"members": [], "rules": [], "audit": [], "settings": {}, "retention": None, "info": ws.system_info()}
        try:
            out["members"] = ws.admin.members()
            out["rules"] = ws.admin.playbook()
            out["settings"] = ws.admin.settings()
            org = ws.repos.organizations.get(p.org_id)
            out["retention"] = org.retention_days if org else None
        except Exception:  # noqa: BLE001
            pass
        if p.can(Permission.AUDIT_READ):
            out["audit"] = ws.admin.audit_log(limit=500)
        from app.demo.seed import has_demo_data

        out["has_demo"] = has_demo_data(ws)
        return out

    def render(self, data: dict[str, Any]) -> None:
        self.members.set_rows(data["members"])
        self.rules.set_rows(data["rules"])
        self.audit.set_rows(data["audit"])
        s = data["settings"]
        self.offsets.setText(", ".join(str(x) for x in s.get("alert_offsets_days", [90, 60, 30, 14, 7, 1])))
        self.holidays.setPlainText("\n".join(s.get("holidays", [])))
        self.retention.setValue(data["retention"] or 0)
        i = data["info"]
        self.sys_info.setText(
            f"Mode: {'DEMO / local (SQLite)' if i.is_demo else 'Supabase'}\nRole: {self.ctx.ws.principal.role.value} in “{self.ctx.ws.principal.org_name}”\n"
            f"AI: {i.model if i.ai_configured else 'not configured'}\nEmbeddings: {i.embedder} — {i.embedder_note}\nVector index: {i.vector_backend} — {i.vector_note}\n"
            f"OCR: {'available' if i.ocr_available else 'not installed'}" + (f"\nIndex warning: {i.index_error}" if i.index_error else ""))
        demo_ok = i.is_demo
        self.demo_btn.setVisible(demo_ok and not data["has_demo"])
        self.demo_rm.setVisible(demo_ok and data["has_demo"])
        can_admin = self.ctx.ws.principal.can(Permission.MEMBERS_MANAGE)
        for wgt in (self.email, self.role):
            wgt.setEnabled(can_admin)


_ = (SPACE, GlassPanel, QDoubleSpinBox)
