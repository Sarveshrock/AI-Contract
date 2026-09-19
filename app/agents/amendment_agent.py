"""Amendment Intelligence Agent: compares versions and interprets amendment documents.

* Full versions (original vs revised/restated): deterministic clause diff + contract-field diff.
* Partial amendments/addenda: the LLM lists the instructions it contains, each with verified evidence;
  proposed term changes are routed to human review and are never applied automatically.
"""
from __future__ import annotations

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.evidence import resolve_all
from app.agents.prompts import AMENDMENT_SYSTEM, CHANGE_NARRATIVE_SYSTEM
from app.agents.state import AmendmentReport, ClauseDiff, FieldChange
from app.agents.windows import make_windows
from app.core.errors import ContractLensError
from app.models.enums import DocumentRole
from app.schemas.llm_outputs import AmendmentExtraction, ChangeNarratives
from app.security.prompt_guard import wrap_untrusted
from app.security.tool_policy import Capability
from app.tools.diffing import assemble_sections, diff_sections

COMPARED_FIELDS = {
    "effective_date": "Effective date", "expiration_date": "Expiration date", "initial_term_months": "Initial term (months)",
    "auto_renews": "Auto-renewal", "renewal_term_months": "Renewal term (months)", "renewal_notice_days": "Renewal notice (days)",
    "payment_terms": "Payment terms", "payment_days": "Payment period (days)", "governing_law": "Governing law",
    "termination_convenience_notice_days": "Termination-for-convenience notice (days)", "termination_cure_days": "Cure period (days)",
    "total_value": "Total value", "currency": "Currency",
}
TERM_TO_FIELD = {"payment_terms": "payment_days", "renewal_notice": "renewal_notice_days", "renewal_term": "renewal_term_months",
                 "termination_notice": "termination_convenience_notice_days", "effective_date": "effective_date",
                 "expiration_date": "expiration_date", "governing_law": "governing_law"}


def diff_snapshots(old: dict, new: dict) -> list[FieldChange]:
    out: list[FieldChange] = []
    for key, label in COMPARED_FIELDS.items():
        o, n = old.get(key), new.get(key)
        if o != n and (o is not None or n is not None):
            out.append(FieldChange(key, o, n, f"{label}: {o if o is not None else 'not stated'} → {n if n is not None else 'not stated'}"))
    return out


class AmendmentIntelligenceAgent(Agent):
    name = "amendment_intelligence"
    title = "Amendment Intelligence"
    description = "Compares versions, detects added/removed/modified clauses and changed terms, and interprets amendment instructions."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.COMPUTE)
        base_id = ctx.params.get("base_version_id")
        if ctx.version_id is None or not base_id:
            return AgentResult(AgentStatus.SKIPPED, {"reason": "no base version to compare with"})
        warnings: list[str] = []
        base_chunks = ctx.data.chunks(base_id)
        new_chunks = ctx.data.chunks(ctx.version_id)
        report = AmendmentReport(base_version_id=str(base_id), new_version_id=str(ctx.version_id))
        role = ctx.data.version(ctx.version_id).document_role
        partial = role in (DocumentRole.AMENDMENT, DocumentRole.ADDENDUM, DocumentRole.SCHEDULE)

        if not partial:
            report.clause_diffs = diff_sections(assemble_sections(base_chunks), assemble_sections(new_chunks))
            base_snap = ctx.data.version(base_id).extraction or {}
            new_snap = ctx.state.contract.snapshot() if ctx.state.contract else (ctx.data.version(ctx.version_id).extraction or {})
            if base_snap and new_snap:
                report.field_changes = diff_snapshots(base_snap, new_snap)
            else:
                warnings.append("One of the versions has no extracted term snapshot; only clause-level differences are shown.")
        else:
            self.need(ctx, Capability.LLM_STRUCTURED)
            self._instructions(ctx, new_chunks, report, warnings)

        # how did deadlines move relative to the base version?
        old_deadlines = {d.fingerprint: d for d in ctx.data.deadlines(base_id)}
        for d in ctx.state.deadlines:
            old = old_deadlines.get(d.fingerprint)
            if old and old.due_date and d.computation.due_date and old.due_date != d.computation.due_date:
                report.deadline_shifts.append({"label": d.label, "old_due": old.due_date.isoformat(), "new_due": d.computation.due_date.isoformat(),
                                               "shift_days": (d.computation.due_date - old.due_date).days})
        self._narrate(ctx, report, warnings)
        report.summary = self._summary(report)
        ctx.state.amendment = report
        return AgentResult(AgentStatus.WARNING if warnings else AgentStatus.OK,
                           {"partial_amendment": partial, "clause_changes": len(report.clause_diffs), "field_changes": len(report.field_changes),
                            "instructions": len(report.instructions), "deadline_shifts": len(report.deadline_shifts)}, warnings)

    # ------------------------------------------------------------------
    def _instructions(self, ctx: AgentContext, chunks, report: AmendmentReport, warnings: list[str]) -> None:
        for i, w in enumerate(make_windows(chunks, 22000), start=1):
            out = ctx.llm.structured(system=AMENDMENT_SYSTEM, user=f"Amendment passages:\n\n{w.text}\n\nList every instruction that changes the base agreement.",
                                     schema=AmendmentExtraction, purpose=f"amendment_instructions[{i}]")
            if out.amendment_effective_date.iso_date and not ctx.params.get("amendment_effective_date"):
                from app.tools.temporal import parse_date_text
                parsed = parse_date_text(out.amendment_effective_date.raw_text or out.amendment_effective_date.iso_date)
                if parsed.value:
                    ctx.params["amendment_effective_date"] = parsed.value
            for ins in out.instructions:
                ev = resolve_all(ins.evidence, w.labels)
                if not any(e.verified for e in ev):
                    warnings.append(f"Amendment instruction '{ins.summary[:60]}' has no verifiable quote; flagged for review.")
                fld = TERM_TO_FIELD.get(ins.affected_term)
                report.instructions.append({
                    "target_section": ins.target_section, "action": ins.action.value, "affected_term": ins.affected_term, "summary": ins.summary,
                    "old": ins.old_value_text, "new": ins.new_value_text, "new_int_value": ins.new_int_value, "field": fld,
                    "evidence": [e.to_dict() for e in ev], "verified": any(e.verified for e in ev),
                })
                if fld and ins.new_int_value is not None and any(e.verified for e in ev):
                    base = (ctx.params.get("base_snapshot") or {}).get(fld)
                    report.field_changes.append(FieldChange(fld, base, ins.new_int_value,
                                                            f"{COMPARED_FIELDS.get(fld, fld)}: {base if base is not None else 'not stated'} → {ins.new_int_value} (proposed by amendment)"))

    def _narrate(self, ctx: AgentContext, report: AmendmentReport, warnings: list[str]) -> None:
        """Optional LLM pass that adds neutral summaries/materiality; deterministic text is kept if it fails."""
        if not ctx.llm.available or not report.clause_diffs:
            return
        try:
            self.need(ctx, Capability.LLM_STRUCTURED)
            items = []
            for i, d in enumerate(report.clause_diffs[:25]):
                items.append(f"change_id={i} type={d.change_type} section={d.section_new or d.section_old}\n" + wrap_untrusted(
                    f"OLD: {d.old_text[:700]}\nNEW: {d.new_text[:700]}", "CHANGE"))
            out = ctx.llm.structured(system=CHANGE_NARRATIVE_SYSTEM, user="\n\n".join(items), schema=ChangeNarratives, purpose="change_narratives")
            for n in out.items:
                try:
                    d = report.clause_diffs[int(n.change_id)]
                except (ValueError, IndexError):
                    continue
                d.summary, d.materiality = n.summary, n.materiality.value
        except ContractLensError as exc:
            warnings.append(f"Change narratives unavailable ({exc.user_message}); deterministic summaries shown.")

    @staticmethod
    def _summary(r: AmendmentReport) -> str:
        parts = []
        if r.clause_diffs:
            c = {t: sum(1 for d in r.clause_diffs if d.change_type == t) for t in ("added", "removed", "modified")}
            parts.append(f"{c['modified']} modified, {c['added']} added, {c['removed']} removed clause(s)")
        if r.field_changes:
            parts.append(f"{len(r.field_changes)} changed term(s)")
        if r.instructions:
            parts.append(f"{len(r.instructions)} amendment instruction(s)")
        if r.deadline_shifts:
            parts.append(f"{len(r.deadline_shifts)} deadline(s) move")
        return "; ".join(parts) or "No differences detected."


_ = ClauseDiff
