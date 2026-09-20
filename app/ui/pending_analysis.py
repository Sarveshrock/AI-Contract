"""Analyse every uploaded-but-unanalysed contract and report what happened."""
from __future__ import annotations

from app.ui.context import UiContext


def run_pending_analysis(ctx: UiContext) -> None:
    if not ctx.ws.llm.available:
        ctx.toast("Nothing can be analysed: no AI provider works and offline analysis is off.", "warning")
        return
    offline = getattr(ctx.ws.llm, "offline_rules", False)
    ctx.toast("Analysing pending contracts with built-in offline rules…" if offline else "Analysing pending contracts…", "info")

    def done(outs) -> None:
        ok = sum(1 for o in outs if o.error is None and o.status.value != "failed")
        failed = len(outs) - ok
        if not outs:
            ctx.toast("No pending contracts to analyse.", "info")
        else:
            ctx.toast(f"Analysed {ok} contract(s)" + (f"; {failed} failed, see Agent Runs." if failed else ".") + (" Results came from offline rules: please review them." if offline and ok else ""),
                      "warning" if failed or offline else "success")
        ctx.invalidate_all()
        ctx.refresh_badges()

    ctx.run(lambda: ctx.ws.contracts.analyze_pending(), done, name="analysis pending")
