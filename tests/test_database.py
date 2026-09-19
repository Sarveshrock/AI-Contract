from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.core.errors import AuthorizationError, DatabaseError
from app.database.auditing import AuditingStore
from app.database.store import F
from app.database.supabase_store import apply_filters
from app.models.entities import Contract, ContractVersion, Obligation
from app.models.enums import ContractStatus, ObligationStatus
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.models.enums import Role
from tests.conftest import ROOT


def _contract(org_id, title="MSA"):
    return Contract(org_id=org_id, title=title, expiration_date=date(2026, 5, 1), tags=["a", "b"], auto_renews=True)


def test_roundtrip_types(repos, principal):
    c = repos.contracts.add(_contract(principal.org_id))
    got = repos.contracts.require(c.id)
    assert got.expiration_date == date(2026, 5, 1)
    assert got.auto_renews is True and got.tags == ["a", "b"]
    assert got.status == ContractStatus.DRAFT


def test_update_and_filters(repos, principal):
    a = repos.contracts.add(_contract(principal.org_id, "A"))
    repos.contracts.add(_contract(principal.org_id, "B"))
    repos.contracts.update(a.id, status=ContractStatus.ACTIVE, expiration_date=date(2027, 1, 1))
    active = repos.contracts.list([F.eq("status", ContractStatus.ACTIVE)])
    assert [c.title for c in active] == ["A"]
    assert repos.contracts.count([F.lte("expiration_date", date(2026, 12, 31))]) == 1
    assert repos.contracts.count([F.in_("title", ["A", "B"])]) == 2
    assert repos.contracts.count([F.in_("title", [])]) == 0
    assert repos.contracts.count([F.ilike("title", "a")]) == 1


def test_org_isolation_reads(store, principal, other_principal):
    mine = Repositories(store, principal.org_id)
    theirs = Repositories(store, other_principal.org_id)
    c = mine.contracts.add(_contract(principal.org_id, "Secret"))
    assert theirs.contracts.get(c.id) is None
    assert theirs.contracts.list() == []
    assert theirs.contracts.delete(c.id) is False
    with pytest.raises(DatabaseError):
        theirs.contracts.update(c.id, title="hacked")
    assert mine.contracts.require(c.id).title == "Secret"


def test_org_isolation_writes_blocked(store, principal, other_principal):
    theirs = Repositories(store, other_principal.org_id)
    with pytest.raises(AuthorizationError):
        theirs.contracts.add(_contract(principal.org_id))


def test_unique_and_fk_violations(repos, principal):
    c = repos.contracts.add(_contract(principal.org_id))
    v = ContractVersion(org_id=principal.org_id, contract_id=c.id, version_number=1)
    repos.versions.add(v)
    with pytest.raises(DatabaseError):
        repos.versions.add(ContractVersion(org_id=principal.org_id, contract_id=c.id, version_number=1))
    with pytest.raises(DatabaseError):
        repos.versions.add(ContractVersion(org_id=principal.org_id, contract_id=uuid4(), version_number=2))


def test_upsert_idempotent(repos, principal):
    c = repos.contracts.add(_contract(principal.org_id))
    v = repos.versions.add(ContractVersion(org_id=principal.org_id, contract_id=c.id))
    o = Obligation(org_id=principal.org_id, contract_id=c.id, contract_version_id=v.id, title="Pay", description="Pay invoices", fingerprint="fp1")
    repos.obligations.upsert([o], ["contract_version_id", "fingerprint"])
    o2 = o.model_copy(update={"id": uuid4(), "title": "Pay invoices"})
    repos.obligations.upsert([o2], ["contract_version_id", "fingerprint"])
    rows = repos.obligations.list()
    assert len(rows) == 1 and rows[0].title == "Pay invoices"
    assert rows[0].id == o.id  # original id preserved


def test_transaction_rollback(store, repos, principal):
    with pytest.raises(RuntimeError):
        with store.transaction():
            repos.contracts.add(_contract(principal.org_id))
            raise RuntimeError("boom")
    assert repos.contracts.count() == 0


def test_cascade_delete(repos, principal):
    c = repos.contracts.add(_contract(principal.org_id))
    v = repos.versions.add(ContractVersion(org_id=principal.org_id, contract_id=c.id))
    repos.contracts.delete(c.id)
    assert repos.versions.count() == 0


def test_row_audit_trigger_emulation(store, principal):
    audited = AuditingStore(store, lambda: (str(principal.user_id), principal.email))
    repos = Repositories(audited, principal.org_id)
    c = repos.contracts.add(_contract(principal.org_id))
    repos.contracts.update(c.id, status=ContractStatus.ACTIVE)
    repos.contracts.delete(c.id)
    actions = [r.action for r in Repositories(store, principal.org_id).audit.list(order_by=[("created_at", False)])]
    assert actions == ["contracts.insert", "contracts.update", "contracts.delete"]
    upd = Repositories(store, principal.org_id).audit.list([F.eq("action", "contracts.update")])[0]
    assert upd.before["status"] == "draft" and upd.after["status"] == "active"


def test_permissions():
    p = Principal(user_id=uuid4(), email="v@x.test", org_id=uuid4(), role=Role.VIEWER)
    assert p.can(Permission.CONTRACTS_READ) and not p.can(Permission.CONTRACTS_WRITE)
    with pytest.raises(AuthorizationError):
        p.require(Permission.REVIEW_DECIDE)
    admin = Principal(user_id=uuid4(), email="a@x.test", org_id=uuid4(), role=Role.ADMIN)
    admin.require(Permission.INTEGRATIONS_MANAGE)
    with pytest.raises(AuthorizationError):
        admin.assert_org(uuid4())


class _FakeQuery:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a))
            return self
        return rec

    @property
    def not_(self):
        self.calls.append(("not", ()))
        return self


def test_supabase_filter_translation():
    q = _FakeQuery()
    apply_filters(q, [F.eq("a", 1), F.eq("b", None), F.in_("c", [1, 2]), F.gte("d", date(2026, 1, 1)), F.not_null("e")])
    names = [c[0] for c in q.calls]
    assert names == ["eq", "is_", "in_", "gte", "not", "is_"]
    assert ("gte", ("d", "2026-01-01")) in q.calls


def test_migrations_are_generated_and_current():
    from app.database.sqlgen import generate_rls_migration, generate_tables_migration

    assert (ROOT / "migrations" / "0002_tables.sql").read_text(encoding="utf-8") == generate_tables_migration()
    assert (ROOT / "migrations" / "0004_rls.sql").read_text(encoding="utf-8") == generate_rls_migration()


def test_every_table_has_rls_and_org_isolation():
    from app.models.registry import ALL_MODELS

    rls = (ROOT / "migrations" / "0004_rls.sql").read_text(encoding="utf-8")
    tables = (ROOT / "migrations" / "0002_tables.sql").read_text(encoding="utf-8")
    assert len(ALL_MODELS) >= 20
    for m in ALL_MODELS:
        assert f"ALTER TABLE public.{m.table_name} ENABLE ROW LEVEL SECURITY;" in rls
        if m.org_scoped:
            assert f"CONSTRAINT {m.table_name}_id_org_key UNIQUE (id, org_id)" in tables
    # audit log is append-only for clients: no insert/update/delete policies
    assert "audit_logs_insert" not in rls and "audit_logs_update" not in rls and "audit_logs_delete" not in rls


def test_required_tables_present():
    from app.models.registry import TABLES

    required = {"profiles", "organizations", "organization_members", "contracts", "contract_versions", "documents", "document_chunks",
                "parties", "contract_parties", "clauses", "obligations", "obligation_dependencies", "deadlines", "evidence",
                "analysis_runs", "analysis_findings", "review_cases", "alerts", "audit_logs", "integrations"}
    assert required <= set(TABLES)
