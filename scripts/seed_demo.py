"""Load the demo contracts into the local demo workspace from the command line.

Usage: python scripts/seed_demo.py [--remove]
Only works in local mode (never against Supabase).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.core.container import AppContainer  # noqa: E402
from app.demo.seed import remove_demo_data, seed_demo  # noqa: E402


def main() -> None:
    settings = get_settings()
    container = AppContainer.build(settings)
    principal = container.sign_in()[0]
    ws = container.open_workspace(principal)
    if "--remove" in sys.argv:
        print(f"removed {remove_demo_data(ws)} demo contract(s)")
        return
    report = seed_demo(ws, ROOT / "data" / "samples", on_progress=lambda msg, frac: print(f"[{frac:4.0%}] {msg}"))
    print(f"contracts={report.contracts} obligations={report.obligations} deadlines={report.deadlines} review_cases={report.review_cases} alerts={report.alerts}")
    for note in report.notes:
        print("note:", note)


if __name__ == "__main__":
    main()
