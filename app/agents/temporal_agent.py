"""Temporal Reasoning Agent: turns extracted time terms into *computed* deadlines with traces.

The LLM never calculates a date. This agent maps structured terms onto :class:`TemporalRule` and calls
the deterministic engine. Missing anchors stay unresolved; nothing is guessed.
"""
from __future__ import annotations

import re
from datetime import date

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.state import DeadlineDraft, ObligationDraft
from app.agents.util import fingerprint, norm_key
from app.models.enums import DeadlineCategory, DeadlineKind, EventType, ObligationCategory
from app.schemas.llm_outputs import TemporalTermOut
from app.security.tool_policy import Capability
from app.tools.temporal import (
    ANCHOR_AMENDMENT,
    ANCHOR_CURRENT_TERM_END,
    ANCHOR_EFFECTIVE,
    ANCHOR_EXPIRATION,
    ANCHOR_TERM_END,
    ComputationStatus,
    Direction,
    Frequency,
    TemporalRule,
    Unit,
    compute_deadline,
    roll_term_end,
)

FIXED_ANCHORS = {ANCHOR_EFFECTIVE, ANCHOR_EXPIRATION, ANCHOR_TERM_END, ANCHOR_AMENDMENT, ANCHOR_CURRENT_TERM_END}
_ANCHOR_SYNONYMS = {
    "invoice_receipt": "invoice_received", "receipt_of_invoice": "invoice_received", "invoice_date": "invoice_received",
    "end_of_term": ANCHOR_TERM_END, "expiry_date": ANCHOR_EXPIRATION, "expiration": ANCHOR_EXPIRATION, "effective": ANCHOR_EFFECTIVE,
    "commencement_date": ANCHOR_EFFECTIVE, "start_date": ANCHOR_EFFECTIVE, "delivery": "delivery_accepted", "notice": "notice_received",
}
_CATEGORY_MAP = {
    ObligationCategory.PAYMENT: DeadlineCategory.PAYMENT, ObligationCategory.DELIVERY: DeadlineCategory.DELIVERY,
    ObligationCategory.REPORTING: DeadlineCategory.REPORTING, ObligationCategory.RENEWAL: DeadlineCategory.RENEWAL_NOTICE,
    ObligationCategory.TERMINATION: DeadlineCategory.TERMINATION_NOTICE,
}


def normalize_anchor(anchor: str | None) -> str | None:
    if not anchor:
        return None
    key = re.sub(r"[^a-z0-9]+", "_", anchor.strip().lower()).strip("_")
    return _ANCHOR_SYNONYMS.get(key, key)


def rule_from_term(term: TemporalTermOut, label: str, source_ref: str | None) -> TemporalRule:
    """Map an extracted term onto an engine rule. Incomplete terms become rules that resolve to UNRESOLVED."""
    anchor = normalize_anchor(term.anchor)
    kind = term.kind
    if kind is DeadlineKind.UNRESOLVED:
        return TemporalRule(DeadlineKind.UNRESOLVED, label, source_ref=source_ref)
    if kind is DeadlineKind.EXPLICIT:
        return TemporalRule(DeadlineKind.EXPLICIT, label, source_ref=source_ref, explicit_text=term.explicit_date_text or term.raw_text)
    if kind is DeadlineKind.RECURRING:
        return TemporalRule(DeadlineKind.RECURRING, label, source_ref=source_ref, anchor=anchor or ANCHOR_EFFECTIVE,
                            frequency=term.frequency, nth_business_day=term.nth_business_day, day_of_month=term.day_of_month)
    if anchor and anchor not in FIXED_ANCHORS:
        kind = DeadlineKind.EVENT_TRIGGERED
    elif kind is DeadlineKind.EVENT_TRIGGERED and anchor in FIXED_ANCHORS:
        kind = DeadlineKind.DERIVED
    return TemporalRule(kind, label, source_ref=source_ref, anchor=anchor, offset=term.offset, unit=term.unit,
                        direction=term.direction or Direction.AFTER)


class TemporalReasoningAgent(Agent):
    _rolled = 0
    name = "temporal_reasoning"
    title = "Temporal Reasoning"
    description = "Computes explicit, derived, event-triggered and recurring deadlines deterministically, with calculation traces."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.COMPUTE)
        state = ctx.state
        warnings: list[str] = []
        anchors: dict[str, date | None] = self._anchors(ctx, warnings)
        holidays = frozenset(date.fromisoformat(h) for h in ctx.params.get("holidays", []) if h)
        today = ctx.params.get("today")
        drafts: list[DeadlineDraft] = []
        ex = state.contract

        if ex is not None:
            drafts.extend(self._contract_level(ex, anchors, holidays, warnings))
        for ob in state.obligations:
            d = self._obligation_deadline(ob, anchors, holidays, today)
            if d:
                drafts.append(d)
        state.deadlines = drafts
        unresolved = sum(1 for d in drafts if d.computation.status is ComputationStatus.UNRESOLVED)
        assumptions = sum(1 for d in drafts if d.computation.assumptions)
        return AgentResult(AgentStatus.WARNING if unresolved or warnings else AgentStatus.OK,
                           {"deadlines": len(drafts), "unresolved": unresolved, "with_assumptions": assumptions,
                            "anchors": {k: (v.isoformat() if v else None) for k, v in anchors.items()}}, warnings)

    # ------------------------------------------------------------------
    def _anchors(self, ctx: AgentContext, warnings: list[str]) -> dict[str, date | None]:
        ex = ctx.state.contract
        anchors: dict[str, date | None] = {ANCHOR_EFFECTIVE: None, ANCHOR_EXPIRATION: None, ANCHOR_TERM_END: None, ANCHOR_AMENDMENT: None, ANCHOR_CURRENT_TERM_END: None}
        base = ctx.params.get("base_snapshot") or {}
        if ex is not None:
            anchors[ANCHOR_EFFECTIVE] = ex.effective_date.value
            anchors[ANCHOR_EXPIRATION] = ex.expiration_date.value
        else:
            for key, name in (("effective_date", ANCHOR_EFFECTIVE), ("expiration_date", ANCHOR_EXPIRATION)):
                v = base.get(key)
                if v:
                    anchors[name] = date.fromisoformat(v)
        if ctx.params.get("amendment_effective_date"):
            anchors[ANCHOR_AMENDMENT] = ctx.params["amendment_effective_date"]
        # expiration derived from the initial term when not stated as a date
        if anchors[ANCHOR_EXPIRATION] is None and anchors[ANCHOR_EFFECTIVE] and ex is not None and ex.initial_term_months.value:
            comp = compute_deadline(TemporalRule(DeadlineKind.DERIVED, "Contract expiration", anchor=ANCHOR_EFFECTIVE, offset=ex.initial_term_months.value,
                                                 unit=Unit.MONTH, direction=Direction.AFTER, term_end=True), {ANCHOR_EFFECTIVE: anchors[ANCHOR_EFFECTIVE]})
            anchors[ANCHOR_EXPIRATION] = comp.due_date
        anchors[ANCHOR_TERM_END] = anchors[ANCHOR_EXPIRATION]
        anchors[ANCHOR_CURRENT_TERM_END] = anchors[ANCHOR_EXPIRATION]
        self._rolled = 0
        today = ctx.params.get("today") or date.today()
        if anchors[ANCHOR_EXPIRATION] and ex is not None and ex.auto_renews.value and ex.renewal_term_months.value:
            cur, n = roll_term_end(anchors[ANCHOR_EXPIRATION], ex.renewal_term_months.value, today)
            anchors[ANCHOR_CURRENT_TERM_END] = cur
            self._rolled = n
        # events recorded by users become anchors (invoice_received, delivery_accepted, ...)
        if ctx.contract_id:
            for e in ctx.data.events(ctx.contract_id):
                if e.occurred_on:
                    anchors[e.event_type.value] = e.occurred_on
                    anchors[normalize_anchor(e.name) or e.event_type.value] = e.occurred_on
        if ex is not None and anchors[ANCHOR_EFFECTIVE] is None:
            warnings.append("Effective date is not established; date-dependent deadlines cannot be calculated.")
        return anchors

    def _contract_level(self, ex, anchors, holidays, warnings) -> list[DeadlineDraft]:
        out: list[DeadlineDraft] = []
        # expiration
        if ex.expiration_date.value:
            rule = TemporalRule(DeadlineKind.EXPLICIT, "Contract expiration", explicit_date=ex.expiration_date.value)
            ev = ex.expiration_date.evidence
        elif ex.initial_term_months.value:
            rule = TemporalRule(DeadlineKind.DERIVED, "Contract expiration", anchor=ANCHOR_EFFECTIVE, offset=ex.initial_term_months.value,
                                unit=Unit.MONTH, direction=Direction.AFTER, term_end=True)
            ev = ex.initial_term_months.evidence + ex.effective_date.evidence
        else:
            rule, ev = TemporalRule(DeadlineKind.UNRESOLVED, "Contract expiration"), []
        comp = compute_deadline(rule, anchors, holidays=holidays)
        if rule.kind is DeadlineKind.UNRESOLVED:
            comp.trace.append("Neither an expiration date nor an initial term length was found in the contract.")
        out.append(DeadlineDraft("Contract expiration", DeadlineCategory.EXPIRATION.value, comp, None, ev, fingerprint=fingerprint("dl", "expiration", "contract")))
        # renewal notice
        notice = ex.renewal_notice_days.value
        if notice and ex.auto_renews.value is not False:
            rrule = TemporalRule(DeadlineKind.DERIVED, "Renewal / non-renewal notice deadline", anchor=ANCHOR_CURRENT_TERM_END, offset=notice, unit=Unit.DAY, direction=Direction.BEFORE)
            rcomp = compute_deadline(rrule, anchors, holidays=holidays)
            if self._rolled:
                rcomp.assumptions.append(f"The initial term ended {anchors[ANCHOR_EXPIRATION].isoformat()}; assuming the contract auto-renewed {self._rolled} time(s) and was not terminated, the current term ends {anchors[ANCHOR_CURRENT_TERM_END].isoformat()}. Confirm renewal status.")
            if ex.auto_renews.value is None:
                rcomp.assumptions.append("The contract does not state clearly whether it renews automatically; notice deadline shown because a notice period exists.")
            out.append(DeadlineDraft(rrule.label, DeadlineCategory.RENEWAL_NOTICE.value, rcomp, None,
                                     ex.renewal_notice_days.evidence + ex.auto_renews.evidence, fingerprint=fingerprint("dl", "renewal_notice", "contract")))
        elif ex.auto_renews.value and not notice:
            warnings.append("Contract renews automatically but no notice period was found; renewal notice deadline is unresolved.")
            rcomp = compute_deadline(TemporalRule(DeadlineKind.UNRESOLVED, "Renewal / non-renewal notice deadline"), anchors)
            rcomp.missing_anchors.append("renewal_notice_period")
            rcomp.trace.append("Auto-renewal detected without a stated notice period.")
            out.append(DeadlineDraft("Renewal / non-renewal notice deadline", DeadlineCategory.RENEWAL_NOTICE.value, rcomp, None, ex.auto_renews.evidence,
                                     fingerprint=fingerprint("dl", "renewal_notice", "contract")))
        return out

    def _obligation_deadline(self, ob: ObligationDraft, anchors, holidays, today) -> DeadlineDraft | None:
        term = ob.temporal
        if term is None or term.kind is DeadlineKind.UNRESOLVED and not (term.raw_text or ob.deadline_text):
            return None
        rule = rule_from_term(term, ob.title, ob.section_reference)
        comp = compute_deadline(rule, anchors, holidays=holidays, today=today)
        if comp.status is ComputationStatus.UNRESOLVED and not comp.missing_anchors and rule.kind is not DeadlineKind.EXPLICIT:
            comp.trace.append(f"Time requirement as written: '{term.raw_text or ob.deadline_text}'. The structured terms were incomplete, so no date was calculated.")
        else:
            comp.trace.insert(0, f"Contract wording: '{term.raw_text or ob.deadline_text or ''}'".strip())
        ob.flags = [f for f in ob.flags if f != "recurring"] + (["recurring"] if rule.kind is DeadlineKind.RECURRING else [])
        category = _CATEGORY_MAP.get(ob.category, DeadlineCategory.OTHER).value
        return DeadlineDraft(ob.title, category, comp, ob.section_reference, ob.evidence, obligation_fingerprint=ob.fingerprint,
                             fingerprint=fingerprint("dl", norm_key(ob.title), ob.fingerprint), anchor_event=rule.anchor if rule.kind is DeadlineKind.EVENT_TRIGGERED else None)


_ = (EventType, Frequency)
