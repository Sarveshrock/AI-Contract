"""Principals, roles and permissions. Enforced in services and mirrored by database RLS."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.core.errors import AuthorizationError
from app.models.enums import Role


class Permission(StrEnum):
    CONTRACTS_READ = "contracts.read"
    CONTRACTS_WRITE = "contracts.write"
    CONTRACTS_DELETE = "contracts.delete"
    OBLIGATIONS_WRITE = "obligations.write"
    AI_RUN = "ai.run"
    REVIEW_DECIDE = "review.decide"
    ALERTS_MANAGE = "alerts.manage"
    PLAYBOOK_MANAGE = "playbook.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"
    EXTERNAL_ACTION = "external.action"
    AUDIT_READ = "audit.read"
    MEMBERS_MANAGE = "members.manage"
    RETENTION_MANAGE = "retention.manage"


_READ = {Permission.CONTRACTS_READ}
_OPERATE = _READ | {Permission.CONTRACTS_WRITE, Permission.OBLIGATIONS_WRITE, Permission.AI_RUN, Permission.ALERTS_MANAGE}
_MANAGE = _OPERATE | {Permission.REVIEW_DECIDE, Permission.CONTRACTS_DELETE, Permission.EXTERNAL_ACTION}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_READ),
    Role.MEMBER: frozenset(_OPERATE),
    Role.CONTRACT_MANAGER: frozenset(_MANAGE),
    Role.LEGAL_REVIEWER: frozenset(_MANAGE | {Permission.PLAYBOOK_MANAGE, Permission.AUDIT_READ}),
    Role.ADMIN: frozenset(_MANAGE | {Permission.PLAYBOOK_MANAGE, Permission.AUDIT_READ, Permission.INTEGRATIONS_MANAGE, Permission.MEMBERS_MANAGE, Permission.RETENTION_MANAGE}),
    Role.OWNER: frozenset(_MANAGE | {Permission.PLAYBOOK_MANAGE, Permission.AUDIT_READ, Permission.INTEGRATIONS_MANAGE, Permission.MEMBERS_MANAGE, Permission.RETENTION_MANAGE}),
}


@dataclass(frozen=True)
class Principal:
    """The authenticated user acting within exactly one organisation."""

    user_id: UUID
    email: str
    org_id: UUID
    role: Role
    org_name: str = ""
    full_name: str | None = None
    access_token: str | None = None
    is_demo: bool = False

    @property
    def display_name(self) -> str:
        return self.full_name or self.email

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, frozenset())

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise AuthorizationError(
                f"{self.email} ({self.role}) lacks {permission}",
                user_message=f"Your role ({self.role.value.replace('_', ' ')}) does not allow this action.",
            )

    def assert_org(self, org_id: UUID | str) -> None:
        if str(org_id) != str(self.org_id):
            raise AuthorizationError(f"cross-organisation access attempt: {org_id}", user_message="This item belongs to a different organisation.")
