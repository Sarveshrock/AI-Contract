from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CONTRACTLENS_MODE", "local")

from app.database.sqlite_store import SqliteTableStore  # noqa: E402
from app.models.entities import Organization, Profile  # noqa: E402
from app.models.enums import Role  # noqa: E402
from app.repositories import Repositories  # noqa: E402
from app.security.access import Principal  # noqa: E402


@pytest.fixture()
def store() -> SqliteTableStore:
    s = SqliteTableStore(":memory:")
    yield s
    s.close()


def make_principal(store: SqliteTableStore, org_name: str, email: str, role: Role = Role.OWNER) -> Principal:
    from app.security.auth import LocalAuthService

    return LocalAuthService(store).ensure_demo_principal(org_name=org_name, email=email, role=role)


@pytest.fixture()
def principal(store) -> Principal:
    return make_principal(store, "Acme Legal", "alice@acme.test")


@pytest.fixture()
def other_principal(store) -> Principal:
    return make_principal(store, "Globex Corp", "bob@globex.test")


@pytest.fixture()
def repos(store, principal) -> Repositories:
    return Repositories(store, principal.org_id)


@pytest.fixture()
def sample_dir() -> Path:
    return ROOT / "data" / "samples"


# ---------------------------------------------------------------------------------------------
# A seeded demo workspace is expensive (parse + embed + analyse five documents), so it is built once per
# session and cloned per test (SQLite backup + copied in-memory vectors + copied storage directory).
# ---------------------------------------------------------------------------------------------
import shutil  # noqa: E402
from datetime import date  # noqa: E402

DEMO_TODAY = date(2026, 9, 19)


@pytest.fixture(scope="session")
def _demo_template(tmp_path_factory):
    from app.demo.seed import seed_demo
    from tests.helpers import make_workspace

    tmp = tmp_path_factory.mktemp("demo-template")
    store = SqliteTableStore(":memory:")
    principal = make_principal(store, "Acme Legal", "alice@acme.test")
    ws = make_workspace(store, principal, tmp)
    seed_demo(ws, ROOT / "data" / "samples", today=DEMO_TODAY)
    return store, ws.container.vector_store, tmp


@pytest.fixture()
def clone_demo(_demo_template, tmp_path):
    """Factory: returns a fresh WorkspaceContext over a private copy of the seeded demo data."""
    from app.rag.vector_store import InMemoryVectorStore
    from tests.helpers import make_workspace

    tpl_store, tpl_vectors, tpl_tmp = _demo_template
    made = []

    def build(llm=None, role=None):
        n = len(made)
        store = SqliteTableStore(":memory:")
        tpl_store._conn.backup(store._conn)
        vectors = InMemoryVectorStore()
        vectors._collections = {o: dict(c) for o, c in tpl_vectors._collections.items()}
        vectors._meta = dict(tpl_vectors._meta)
        root = tmp_path / f"clone{n}"
        shutil.copytree(tpl_tmp / "storage", root / "storage")
        principal = make_principal(store, "Acme Legal", "alice@acme.test")
        if role is not None:
            from app.security.access import Principal

            principal = Principal(user_id=principal.user_id, email=principal.email, org_id=principal.org_id, role=role, org_name=principal.org_name)
        ws = make_workspace(store, principal, root, llm=llm, vector_store=vectors)
        made.append(ws)
        return ws

    return build
