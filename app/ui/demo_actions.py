"""Explicit user actions for demo mode (never run automatically)."""
from __future__ import annotations

from app.config.settings import PROJECT_ROOT
from app.demo.seed import remove_demo_data, seed_demo
from app.ui.context import UiContext
from app.ui.components.dialogs import ConfirmationDialog


def load_demo_data(ctx: UiContext) -> None:
    ws = ctx.ws
    ctx.toast("Loading demo contracts. Sample documents are parsed, indexed and analysed by the real pipeline…", "info")

    def work(task):
        return seed_demo(ws, PROJECT_ROOT / "data" / "samples", on_progress=lambda msg, frac: task.progress("seed", frac, msg))

    def done(report) -> None:
        note = f" ({'; '.join(report.notes)})" if report.notes else ""
        ctx.toast(f"Demo data loaded: {report.contracts} contracts, {report.obligations} obligations, {report.deadlines} deadlines.{note}", "success")
        ctx.invalidate_all()
        for s in ctx.screens.values():
            if s.isVisible():
                s.load()

    ctx.run(work, done, name="analysis demo seed", on_progress=lambda stage, frac, msg: None)


def clear_demo_data(ctx: UiContext, parent=None) -> None:
    if not ConfirmationDialog.ask(parent, "Remove demo data", "All demo contracts and everything derived from them (obligations, deadlines, evidence, search index entries) will be deleted.",
                                  confirm_text="Remove demo data", danger=True):
        return

    def done(n: int) -> None:
        ctx.toast(f"Removed {n} demo contract(s).", "success")
        ctx.invalidate_all()
        for s in ctx.screens.values():
            if s.isVisible():
                s.load()

    ctx.run(lambda: remove_demo_data(ctx.ws), done, name="remove demo")
