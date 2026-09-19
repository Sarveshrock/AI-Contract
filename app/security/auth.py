"""Authentication. Supabase Auth in production; a clearly-labelled single-user demo principal locally."""
from __future__ import annotations

import re
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.errors import AuthenticationError, ConfigurationError
from app.core.logging import get_logger
from app.database.store import F, TableStore
from app.models.entities import Organization, OrganizationMember, Profile
from app.models.enums import Role
from app.repositories.base import GlobalRepository
from app.security.access import Principal

log = get_logger(__name__)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEMO_ORG_SLUG = "demo-workspace"
DEMO_EMAIL = "demo@contractlens.local"


class AuthService(Protocol):
    def sign_in(self, email: str, password: str) -> list[Principal]: ...

    def sign_out(self) -> None: ...

    def access_token(self) -> str | None: ...


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "workspace"


class SupabaseAuthService:
    """Email/password sign-in via Supabase Auth; memberships are read through RLS."""

    def __init__(self, client: Any, store: TableStore) -> None:
        self._client = client
        self._store = store

    def sign_in(self, email: str, password: str) -> list[Principal]:
        email = email.strip()
        if not _EMAIL_RE.match(email) or not password:
            raise AuthenticationError("invalid credentials format", user_message="Enter a valid e-mail address and password.")
        try:
            res = self._client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "Invalid login" in msg or "invalid_credentials" in msg:
                raise AuthenticationError("invalid credentials", user_message="Incorrect e-mail or password.") from exc
            if "Email not confirmed" in msg:
                raise AuthenticationError("email not confirmed", user_message="Please confirm your e-mail address before signing in.") from exc
            raise AuthenticationError(f"sign-in failed: {msg}", user_message="Sign-in failed. Check your connection and try again.") from exc
        user, session = res.user, res.session
        if user is None or session is None:
            raise AuthenticationError("no session", user_message="Sign-in failed.")
        uid = UUID(str(user.id))
        members = self._store.select("organization_members", [F.eq("user_id", str(uid))])
        if not members:
            raise AuthenticationError("no membership", user_message="Your account is not a member of any organisation. Ask an administrator for an invitation, or create one with the create_organization function.")
        principals: list[Principal] = []
        profile_rows = self._store.select("profiles", [F.eq("id", str(uid))], limit=1)
        full_name = profile_rows[0].get("full_name") if profile_rows else None
        for m in members:
            org_rows = self._store.select("organizations", [F.eq("id", m["org_id"])], limit=1)
            org_name = org_rows[0]["name"] if org_rows else ""
            principals.append(Principal(user_id=uid, email=email, org_id=UUID(m["org_id"]), role=Role(m["role"]), org_name=org_name,
                                        full_name=full_name, access_token=session.access_token))
        return principals

    def sign_out(self) -> None:
        try:
            self._client.auth.sign_out()
        except Exception:  # noqa: BLE001
            log.warning("sign-out request failed; local session discarded")

    def access_token(self) -> str | None:
        try:
            session = self._client.auth.get_session()
            return session.access_token if session else None
        except Exception:  # noqa: BLE001
            return None


class LocalAuthService:
    """Local demo mode: no credentials, one owner principal. Never used when Supabase is configured."""

    def __init__(self, store: TableStore) -> None:
        self._store = store

    def sign_in(self, email: str = DEMO_EMAIL, password: str = "") -> list[Principal]:  # noqa: ARG002
        return [self.ensure_demo_principal()]

    def ensure_demo_principal(self, org_name: str = "Demo Workspace", email: str = DEMO_EMAIL, role: Role = Role.OWNER) -> Principal:
        orgs = GlobalRepository(self._store, Organization)
        profiles = GlobalRepository(self._store, Profile)
        org = next(iter(orgs.list([F.eq("slug", _slug(org_name))], limit=1)), None)
        if org is None:
            org = orgs.add(Organization(name=org_name, slug=_slug(org_name), settings={}))
        profile_rows = profiles.list([F.eq("email", email)], limit=1)
        profile = profile_rows[0] if profile_rows else profiles.add(Profile(id=uuid4(), email=email, full_name="Demo User" if email == DEMO_EMAIL else email.split("@")[0], default_org_id=org.id))
        members = self._store.select("organization_members", [F.eq("org_id", str(org.id)), F.eq("user_id", str(profile.id))], limit=1)
        if not members:
            self._store.insert("organization_members", [OrganizationMember(org_id=org.id, user_id=profile.id, role=role).to_row()])
            actual_role = role
        else:
            actual_role = Role(members[0]["role"])
        return Principal(user_id=profile.id, email=profile.email, org_id=org.id, role=actual_role, org_name=org.name,
                         full_name=profile.full_name, access_token=None, is_demo=True)

    def sign_out(self) -> None:
        return None

    def access_token(self) -> str | None:
        return None


def build_auth(client: Any | None, store: TableStore) -> AuthService:
    if client is None:
        return LocalAuthService(store)
    if store is None:  # pragma: no cover
        raise ConfigurationError("store required")
    return SupabaseAuthService(client, store)
