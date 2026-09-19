"""Administration: members and roles, playbook rules, workspace settings, audit log, retention."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import AuthorizationError, DatabaseError, ValidationFailure
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import AuditLog, OrganizationMember, PlaybookRule, Profile
from app.models.enums import Role
from app.rag.indexer import DocumentIndexer
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService
from app.services.storage import FileStorage

log = get_logger(__name__)


@dataclass
class MemberRow:
    member: OrganizationMember
    email: str
    full_name: str | None


class AdminService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService, *, supabase_client: Any | None = None,
                 storage: FileStorage | None = None, indexer: DocumentIndexer | None = None) -> None:
        self._p, self._r, self._audit = principal, repos, audit
        self._client, self._storage, self._indexer = supabase_client, storage, indexer

    # ------------------------------------------------------------------ members
    def members(self) -> list[MemberRow]:
        ms = self._r.members.list()
        profiles = {UUID(p["id"]): p for p in self._r.store.select("profiles", [F.in_("id", [str(m.user_id) for m in ms])])} if ms else {}
        return [MemberRow(m, profiles.get(m.user_id, {}).get("email", "(unknown)"), profiles.get(m.user_id, {}).get("full_name")) for m in ms]

    def add_member(self, email: str, role: Role) -> OrganizationMember:
        """Add an existing user to this organisation. On Supabase this uses an admin-checked RPC; users must already have signed up."""
        self._p.require(Permission.MEMBERS_MANAGE)
        if role is Role.OWNER and self._p.role is not Role.OWNER:
            raise AuthorizationError("only owners grant owner", user_message="Only an owner can grant the owner role.")
        email = email.strip().lower()
        if "@" not in email:
            raise ValidationFailure("bad email", user_message="Enter a valid e-mail address.")
        if self._client is not None:
            try:
                self._client.rpc("add_org_member_by_email", {"p_org": str(self._p.org_id), "p_email": email, "p_role": role.value}).execute()
            except Exception as exc:  # noqa: BLE001
                raise DatabaseError(str(exc), user_message="Could not add the member. The user must have signed up first, and you must be an administrator.") from exc
            member = next((m for m in self.members() if m.email.lower() == email), None)
            if member is None:
                raise DatabaseError("member not visible after add", user_message="The member was added but could not be read back.")
            self._audit.record("member.add", "member", member.member.id, email=email, role=role.value)
            return member.member
        profiles = self._r.profiles.list([F.eq("email", email)], limit=1)
        profile = profiles[0] if profiles else self._r.profiles.add(Profile(id=uuid4(), email=email, full_name=email.split("@")[0], default_org_id=self._p.org_id))
        m = self._r.members.add(OrganizationMember(org_id=self._p.org_id, user_id=profile.id, role=role))
        self._audit.record("member.add", "member", m.id, email=email, role=role.value)
        return m

    def change_role(self, member_id: UUID | str, role: Role) -> OrganizationMember:
        self._p.require(Permission.MEMBERS_MANAGE)
        m = self._r.members.require(member_id)
        if (role is Role.OWNER or m.role is Role.OWNER) and self._p.role is not Role.OWNER:
            raise AuthorizationError("owner changes need owner", user_message="Only an owner can grant or remove the owner role.")
        if m.role is Role.OWNER and role is not Role.OWNER and self._r.members.count([F.eq("role", Role.OWNER)]) <= 1:
            raise ValidationFailure("last owner", user_message="The workspace must keep at least one owner.")
        m = self._r.members.update(m.id, role=role)
        self._audit.record("member.role", "member", m.id, role=role.value)
        return m

    def remove_member(self, member_id: UUID | str) -> None:
        self._p.require(Permission.MEMBERS_MANAGE)
        m = self._r.members.require(member_id)
        if m.user_id == self._p.user_id:
            raise ValidationFailure("cannot remove self", user_message="You cannot remove yourself.")
        if m.role is Role.OWNER and (self._p.role is not Role.OWNER or self._r.members.count([F.eq("role", Role.OWNER)]) <= 1):
            raise ValidationFailure("owner protected", user_message="Owners can only be removed by another owner, and never the last one.")
        self._r.members.delete(m.id)
        self._audit.record("member.remove", "member", m.id)

    # ------------------------------------------------------------------ playbook
    def playbook(self) -> list[PlaybookRule]:
        return self._r.playbooks.list(order_by=[("name", False)])

    def save_rule(self, rule: PlaybookRule) -> PlaybookRule:
        self._p.require(Permission.PLAYBOOK_MANAGE)
        rule = rule.model_copy(update={"org_id": self._p.org_id})
        existing = self._r.playbooks.get(rule.id)
        row = self._r.playbooks.update(rule.id, **rule.model_dump(exclude={"id", "org_id", "created_at", "updated_at"})) if existing else self._r.playbooks.add(rule)
        self._audit.record("playbook.save", "playbook_rule", row.id, name=row.name)
        return row

    def delete_rule(self, rule_id: UUID | str) -> None:
        self._p.require(Permission.PLAYBOOK_MANAGE)
        self._r.playbooks.delete(rule_id)
        self._audit.record("playbook.delete", "playbook_rule", rule_id)

    # ------------------------------------------------------------------ workspace settings
    def settings(self) -> dict[str, Any]:
        org = self._r.organizations.get(self._p.org_id)
        return dict(org.settings) if org else {}

    def update_settings(self, **values: Any) -> dict[str, Any]:
        self._p.require(Permission.RETENTION_MANAGE)
        current = self.settings()
        if "alert_offsets_days" in values:
            offs = sorted({int(x) for x in values["alert_offsets_days"]}, reverse=True)
            if not offs or any(o < 0 or o > 730 for o in offs):
                raise ValidationFailure("bad offsets", user_message="Alert offsets must be between 0 and 730 days.")
            values["alert_offsets_days"] = offs
        if "holidays" in values:
            values["holidays"] = sorted({date.fromisoformat(str(h)).isoformat() for h in values["holidays"]})
        current.update(values)
        self._r.organizations.update(self._p.org_id, settings=current)
        self._audit.record("settings.update", "organization", self._p.org_id, keys=sorted(values))
        return current

    def set_retention_days(self, days: int | None) -> None:
        self._p.require(Permission.RETENTION_MANAGE)
        if days is not None and days < 30:
            raise ValidationFailure("retention too short", user_message="Retention must be at least 30 days.")
        self._r.organizations.update(self._p.org_id, retention_days=days)
        self._audit.record("retention.set", "organization", self._p.org_id, days=days)

    # ------------------------------------------------------------------ audit log
    def audit_log(self, *, limit: int = 300, action_prefix: str | None = None, entity_type: str | None = None) -> list[AuditLog]:
        self._p.require(Permission.AUDIT_READ)
        filters = []
        if entity_type:
            filters.append(F.eq("entity_type", entity_type))
        rows = self._r.audit.list(filters, order_by=[("created_at", True)], limit=limit)
        return [r for r in rows if not action_prefix or r.action.startswith(action_prefix)]

    # ------------------------------------------------------------------ retention
    def purge_expired(self, *, dry_run: bool = True, now: datetime | None = None) -> list[str]:
        """Permanently remove contracts that were soft-deleted longer ago than the retention period."""
        self._p.require(Permission.RETENTION_MANAGE)
        org = self._r.organizations.get(self._p.org_id)
        days = org.retention_days if org else None
        if not days:
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        purged: list[str] = []
        for c in self._r.contracts.list([F.not_null("deleted_at")]):
            deleted = c.deleted_at if c.deleted_at and c.deleted_at.tzinfo else (c.deleted_at.replace(tzinfo=timezone.utc) if c.deleted_at else None)
            if deleted and deleted < cutoff:
                purged.append(c.title)
                if not dry_run:
                    for d in self._r.documents.list([F.eq("contract_id", str(c.id))]):
                        if self._indexer:
                            self._indexer.remove_document(str(d.id))
                        if self._storage:
                            try:
                                self._storage.delete(d.storage_path)
                            except Exception:  # noqa: BLE001
                                log.warning("could not delete stored file for %s", d.id)
                    self._r.contracts.delete(c.id)
        if purged and not dry_run:
            self._audit.record("retention.purge", "organization", self._p.org_id, contracts=len(purged))
        return purged
