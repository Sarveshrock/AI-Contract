"""External actions (calendar export, webhook) — allowlisted, permissioned, confirmed, audited, verified-data-only."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.core.errors import ToolAuthorizationError, ValidationFailure
from app.database.store import F
from app.models.entities import Integration
from app.models.enums import IntegrationProvider
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.security.tool_policy import ToolExecutor, ToolSpec
from app.services.audit import AuditService
from app.services.deadlines import DeadlineService
from app.tools.ics import CalendarItem, build_ics

PROVIDER_ACTIONS = {IntegrationProvider.CALENDAR_ICS: ["export_ics"], IntegrationProvider.WEBHOOK: ["post_deadlines"]}
MAX_WEBHOOK_BYTES = 200_000


class IntegrationService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService, deadlines: DeadlineService) -> None:
        self._p, self._r, self._audit, self._deadlines = principal, repos, audit, deadlines

    # ------------------------------------------------------------------ configuration
    def list(self) -> list[Integration]:
        return self._r.integrations.list(order_by=[("name", False)])

    def save(self, provider: IntegrationProvider, name: str, *, config: dict[str, Any] | None = None, enabled: bool = False, requires_confirmation: bool = True,
             integration_id: UUID | str | None = None) -> Integration:
        self._p.require(Permission.INTEGRATIONS_MANAGE)
        config = dict(config or {})
        if any(k.lower() in ("token", "secret", "password", "api_key", "authorization") for k in config):
            raise ValidationFailure("secret in config", user_message="Secrets must not be stored in integration settings.")
        if provider is IntegrationProvider.WEBHOOK:
            url = str(config.get("url", ""))
            u = urlparse(url)
            if enabled and (u.scheme != "https" or not u.hostname):
                raise ValidationFailure("webhook must be https", user_message="Webhook URLs must use HTTPS.")
            if enabled and u.hostname not in config.get("allowed_hosts", [u.hostname]):
                raise ValidationFailure("host not allowlisted", user_message="The webhook host must be in the allowed hosts list.")
        values = dict(provider=provider, name=name.strip(), config=config, enabled=enabled, requires_confirmation=requires_confirmation, allowed_actions=PROVIDER_ACTIONS[provider])
        if integration_id:
            row = self._r.integrations.update(integration_id, **values)
        else:
            existing = self._r.integrations.first([F.eq("name", name.strip())])
            row = self._r.integrations.update(existing.id, **values) if existing else self._r.integrations.add(Integration(org_id=self._p.org_id, created_by=self._p.user_id, **values))
        self._audit.record("integration.save", "integration", row.id, provider=provider.value, enabled=enabled)
        return row

    # ------------------------------------------------------------------ actions
    def _integration(self, provider: IntegrationProvider) -> Integration:
        row = next((i for i in self._r.integrations.list() if i.provider is provider and i.enabled), None)
        if row is None:
            raise ToolAuthorizationError(f"no enabled {provider} integration", user_message=f"The {provider.value.replace('_', ' ')} integration is not enabled. An administrator must enable it first.")
        return row

    def _executor(self, integ: Integration, action: str, handler) -> ToolExecutor:
        ex = ToolExecutor(self._p, self._audit)
        if action in integ.allowed_actions:
            ex.register(ToolSpec(f"{integ.provider.value}.{action}", f"{integ.name}: {action}", Permission.EXTERNAL_ACTION, requires_confirmation=integ.requires_confirmation, handler=handler))
        return ex

    def export_calendar(self, deadline_ids: list[UUID | str], path: str | Path, *, confirmed: bool) -> int:
        """Write an .ics file for **human-confirmed** deadlines only."""
        integ = self._integration(IntegrationProvider.CALENDAR_ICS)
        deadlines = [self._r.deadlines.require(i) for i in deadline_ids]
        verified = self._deadlines.confirmed_only(deadline_ids)
        titles = {c.id: c.title for c in self._r.contracts.list()}

        def handler(**_: Any) -> dict[str, Any]:
            items = [CalendarItem(str(d.id) + str(d.due_date), f"{d.label} — {titles.get(d.contract_id, 'Contract')}", d.due_date,
                                  "Confirmed deadline exported from ContractLens. Verify against the contract before relying on it.") for d in verified if d.due_date]
            Path(path).write_text(build_ics(items), encoding="utf-8", newline="")
            return {"events": len(items)}

        ex = self._executor(integ, "export_ics", handler)
        res = ex.invoke("calendar_ics.export_ics", confirmed=confirmed, verified_source=len(verified) == len(deadlines) and bool(deadlines), args={"count": len(deadlines)})
        return int(res["events"])

    def send_webhook(self, deadline_ids: list[UUID | str], *, confirmed: bool) -> int:
        integ = self._integration(IntegrationProvider.WEBHOOK)
        url = str(integ.config.get("url", ""))
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in integ.config.get("allowed_hosts", []):
            raise ToolAuthorizationError("webhook target not allowlisted", user_message="The webhook target is not an allowlisted HTTPS host.")
        deadlines = [self._r.deadlines.require(i) for i in deadline_ids]
        verified = self._deadlines.confirmed_only(deadline_ids)
        titles = {c.id: c.title for c in self._r.contracts.list()}

        def handler(**_: Any) -> dict[str, Any]:
            body = json.dumps({"source": "contractlens", "deadlines": [{"label": d.label, "due_date": d.due_date.isoformat() if d.due_date else None, "contract": titles.get(d.contract_id)} for d in verified]}).encode()
            if len(body) > MAX_WEBHOOK_BYTES:
                raise ValidationFailure("payload too large")
            req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "ContractLens/1.0"})
            opener = urllib.request.build_opener(_NoRedirect())
            try:
                with opener.open(req, timeout=10) as resp:  # noqa: S310 - https + allowlisted host enforced above
                    return {"status": resp.status, "deadlines": len(verified)}
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ValidationFailure(f"webhook failed: {exc}", user_message="The webhook request failed. Nothing was marked as sent.") from exc

        ex = self._executor(integ, "post_deadlines", handler)
        res = ex.invoke("webhook.post_deadlines", confirmed=confirmed, verified_source=len(verified) == len(deadlines) and bool(deadlines), args={"count": len(deadlines), "host": u.hostname})
        return int(res["deadlines"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None
