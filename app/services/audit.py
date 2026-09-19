"""Application-level audit events (row changes are captured by the DB trigger / AuditingStore)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.logging import get_logger
from app.database.store import TableStore
from app.security.access import Principal
from app.security.redaction import redact_mapping

log = get_logger(__name__)


class AuditSink(Protocol):
    def write(self, principal: Principal, action: str, entity_type: str, entity_id: UUID | None, metadata: dict[str, Any]) -> None: ...


class LocalAuditSink:
    """Direct insert. Used by local mode and tests where there is no RLS layer."""

    def __init__(self, store: TableStore) -> None:
        self._store = store

    def write(self, principal: Principal, action: str, entity_type: str, entity_id: UUID | None, metadata: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._store.insert("audit_logs", [{
            "id": str(uuid4()), "org_id": str(principal.org_id), "actor_id": str(principal.user_id), "actor_email": principal.email,
            "action": action, "entity_type": entity_type, "entity_id": str(entity_id) if entity_id else None,
            "before": None, "after": None, "metadata": metadata, "created_at": now, "updated_at": now,
        }])


class SupabaseAuditSink:
    """Calls the SECURITY DEFINER ``log_audit_event`` RPC (audit_logs has no client INSERT policy)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def write(self, principal: Principal, action: str, entity_type: str, entity_id: UUID | None, metadata: dict[str, Any]) -> None:
        self._client.rpc("log_audit_event", {
            "p_org": str(principal.org_id), "p_action": action, "p_entity_type": entity_type,
            "p_entity_id": str(entity_id) if entity_id else None, "p_metadata": metadata,
        }).execute()


class AuditService:
    def __init__(self, sink: AuditSink, principal: Principal) -> None:
        self._sink = sink
        self._principal = principal

    def record(self, action: str, entity_type: str, entity_id: UUID | str | None = None, **metadata: Any) -> None:
        """Record an audit event. Failures are logged loudly but never mask the audited operation."""
        eid = UUID(str(entity_id)) if entity_id else None
        try:
            self._sink.write(self._principal, action, entity_type, eid, redact_mapping(metadata, mask_emails=False))
        except Exception:  # noqa: BLE001
            log.exception("AUDIT WRITE FAILED action=%s entity=%s", action, entity_type)
