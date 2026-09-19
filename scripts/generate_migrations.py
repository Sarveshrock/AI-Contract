"""Regenerate migrations/0002_tables.sql and migrations/0004_rls.sql from the Pydantic models.

Usage:  python scripts/generate_migrations.py [--check]
--check exits non-zero if the committed files are stale (used by the test-suite / CI).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database.sqlgen import generate_rls_migration, generate_tables_migration  # noqa: E402

TARGETS = {
    ROOT / "migrations" / "0002_tables.sql": generate_tables_migration,
    ROOT / "migrations" / "0004_rls.sql": generate_rls_migration,
}


def main() -> int:
    check = "--check" in sys.argv
    stale = False
    for path, gen in TARGETS.items():
        content = gen()
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                print(f"STALE: {path.name}")
                stale = True
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
