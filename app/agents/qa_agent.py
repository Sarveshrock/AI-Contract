"""Evidence & QA Agent: schema, evidence coverage, citation integrity, date/party consistency, contradictions.

Low-quality results are routed to human review. Nothing here calls an LLM: validation must be independent
of the model that produced the output.
"""
from __future__ import annotations

from datetime import timedelta

from pydantic import ValidationError

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.state import FindingDraft, QAIssue, QAReport
from app.agents.util import fingerprint, is_party_reference_generic, match_party
from app.config.risk import SIGNALS, RiskConfig
from app.models.enums import DeadlineKind, ObligationCategory, Severity
from app.schemas.records import ClauseRecord, DeadlineRecord, ObligationRecord
from app.security.tool_policy import Capability
from app.tools.temporal import ComputationStatus, Unit

_PENALTY = {Severity.INFO: 0.0, Severity.LOW: 0.03, Severity.MEDIUM: 0.08, Severity.HIGH: 0.18, Severity.CRITICAL: 0.3}


class EvidenceQAAgent(Agent):
    name = "evidence_qa"
    title = "Evidence & QA"
    description = "Validates schema, evidence coverage, citation integrity, date and party consistency, contradictions and unsupported claims."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.COMPUTE)
        st = ctx.state
        cfg: RiskConfig = ctx.params.get("risk_config") or RiskConfig()
        threshold: float = ctx.settings.quality_threshold
        issues: list[QAIssue] = []
        items_total = items_ok = 0
        ev_total = ev_ok = 0

        def count(evidence) -> bool:
            nonlocal ev_total, ev_ok
            ev_total += len(evidence)
            ev_ok += sum(1 for e in evidence if e.verified)
            return any(e.verified for e in evidence)

        ex = st.contract
        if ex is not None:
            for name, fact in ex.facts().items():
                if fact.present:
                    items_total += 1
                    items_ok += 1 if count(fact.evidence) else 0
                    if not fact.verified:
                        issues.append(QAIssue("unsupported", Severity.MEDIUM, name, f"'{name}' has no verified source quote."))
                    for flag in fact.flags:
                        if flag in ("value_not_in_quote", "model_date_disagrees_with_text", "raw_text_not_in_quote", "date_not_supported_by_quote"):
                            issues.append(QAIssue("citation", Severity.MEDIUM, name, f"'{name}': {flag.replace('_', ' ')}."))
            for p in ex.parties:
                items_total += 1
                items_ok += 1 if count(p.evidence) else 0
            for c in ex.clauses:
                items_total += 1
                items_ok += 1 if count(c.evidence) else 0
                try:
                    ClauseRecord(clause_type=c.clause_type, summary=c.summary, fingerprint=c.fingerprint)
                except ValidationError as exc:
                    issues.append(QAIssue("schema", Severity.HIGH, f"clause {c.heading}", str(exc.errors()[0]["msg"])))

        unsupported_obs = 0
        for o in st.obligations:
            items_total += 1
            ok = count(o.evidence)
            items_ok += 1 if ok else 0
            try:
                ObligationRecord(title=o.title, description=o.description, category=o.category, confidence=o.confidence, fingerprint=o.fingerprint)
            except ValidationError as exc:
                issues.append(QAIssue("schema", Severity.HIGH, o.title, str(exc.errors()[0]["msg"])))
                o.flags.append("schema_invalid")
            if not ok:
                unsupported_obs += 1
                st.unsupported_fingerprints.add(o.fingerprint)
                issues.append(QAIssue("unsupported", Severity.MEDIUM, o.title, "Obligation has no verified source quote."))
        for d in st.deadlines:
            try:
                DeadlineRecord(label=d.label, kind=d.computation.kind, due_date=d.computation.due_date,
                               calculation_trace=d.computation.trace or ["(none)"], fingerprint=d.fingerprint)
            except ValidationError as exc:
                issues.append(QAIssue("schema", Severity.HIGH, d.label, str(exc.errors()[0]["msg"])))

        self._dates(ctx, issues)
        parties_bad = self._parties(ctx, issues)
        self._contradictions(ctx, issues)

        coverage = (items_ok / items_total) if items_total else 0.0
        integrity = (ev_ok / ev_total) if ev_total else 0.0
        if st.contract is None and not st.obligations:  # e.g. nothing extracted at all
            coverage = integrity = 0.0
        penalty = min(0.5, sum(_PENALTY[i.severity] for i in issues))
        score = round(0.45 * coverage + 0.25 * integrity + 0.30 * (1.0 - penalty / 0.5), 3)
        needs_review = score < threshold or any(i.severity in (Severity.HIGH, Severity.CRITICAL) for i in issues) or unsupported_obs > 0
        st.qa = QAReport(score=score, coverage=round(coverage, 3), citation_integrity=round(integrity, 3), issues=issues, needs_review=needs_review,
                         evidence_total=ev_total, evidence_verified=ev_ok)
        self._emit_findings(ctx, cfg, issues, unsupported_obs, parties_bad, coverage, threshold)
        return AgentResult(AgentStatus.WARNING if needs_review else AgentStatus.OK,
                           {"score": score, "coverage": round(coverage, 3), "citation_integrity": round(integrity, 3), "issues": len(issues), "needs_review": needs_review})

    # ------------------------------------------------------------------
    def _dates(self, ctx: AgentContext, issues: list[QAIssue]) -> None:
        ex = ctx.state.contract
        if ex is None:
            return
        eff, exp = ex.effective_date.value, ex.expiration_date.value
        if eff and exp and exp < eff:
            issues.append(QAIssue("date", Severity.HIGH, "expiration_date", f"Expiration ({exp}) is before the effective date ({eff})."))
        term = ex.initial_term_months.value
        if eff and exp and term:
            from app.tools.temporal import add_months
            approx = add_months(eff, term)
            if abs((approx - exp).days) > 3:
                issues.append(QAIssue("date", Severity.MEDIUM, "term", f"Stated expiration ({exp}) does not match effective date + {term} months ({approx})."))
        notice = ex.renewal_notice_days.value
        if notice and term and notice > term * 31:
            issues.append(QAIssue("date", Severity.MEDIUM, "renewal_notice_days", f"Renewal notice ({notice} days) is longer than the initial term ({term} months)."))
        for d in ctx.state.deadlines:
            due = d.computation.due_date
            if due and eff and due < eff - timedelta(days=1) and d.computation.rule.kind is not DeadlineKind.EXPLICIT:
                issues.append(QAIssue("date", Severity.MEDIUM, d.label, f"Calculated deadline {due} is before the contract's effective date."))

    def _parties(self, ctx: AgentContext, issues: list[QAIssue]) -> int:
        ex = ctx.state.contract
        if ex is None or not ex.parties:
            return 0
        from app.agents.obligation_agent import attach_party_matches
        attach_party_matches(ctx.state.obligations, ex.parties)
        bad = 0
        for o in ctx.state.obligations:
            for attr, match in (("responsible", "responsible_party_match"), ("beneficiary", "beneficiary_match")):
                name = getattr(o, attr)
                if name and not is_party_reference_generic(name) and getattr(o, match) is None and match_party(name, ex.parties) is None:
                    bad += 1
                    issues.append(QAIssue("party", Severity.LOW, o.title, f"{attr.capitalize()} '{name}' does not match any identified party."))
        return bad

    def _contradictions(self, ctx: AgentContext, issues: list[QAIssue]) -> None:
        ex = ctx.state.contract
        days: dict[int, list[str]] = {}
        if ex is not None and ex.payment_days.value:
            days.setdefault(ex.payment_days.value, []).append("contract terms")
        notice: dict[int, list[str]] = {}
        if ex is not None and ex.renewal_notice_days.value:
            notice.setdefault(ex.renewal_notice_days.value, []).append("contract terms")
        for o in ctx.state.obligations:
            t = o.temporal
            if not t or t.offset is None or t.unit is not Unit.DAY:
                continue
            if o.category is ObligationCategory.PAYMENT and (t.anchor or "").startswith("invoice"):
                days.setdefault(t.offset, []).append(o.section_reference or o.title)
            if o.category in (ObligationCategory.RENEWAL, ObligationCategory.NOTICE) and (t.anchor or "") in ("expiration_date", "term_end"):
                notice.setdefault(t.offset, []).append(o.section_reference or o.title)
        if len(days) > 1:
            issues.append(QAIssue("contradiction", Severity.HIGH, "payment period", "Different payment periods are stated: " + "; ".join(f"{k} days ({', '.join(v)})" for k, v in days.items())))
        if len(notice) > 1:
            issues.append(QAIssue("contradiction", Severity.HIGH, "renewal notice", "Different renewal-notice periods are stated: " + "; ".join(f"{k} days ({', '.join(v)})" for k, v in notice.items())))

    def _emit_findings(self, ctx: AgentContext, cfg: RiskConfig, issues: list[QAIssue], unsupported_obs: int, parties_bad: int, coverage: float, threshold: float) -> None:
        def add(key: str, description: str, detail: str, severity: Severity | None = None) -> None:
            if not cfg.enabled(key):
                return
            s = SIGNALS[key]
            ctx.state.findings.append(FindingDraft(s.finding_type, severity or s.severity, s.category, s.title, description, cfg.weight_for(key),
                                                   fingerprint=fingerprint("qa", key, detail), signal=key))

        unsupported = [i for i in issues if i.kind == "unsupported"]
        if unsupported:
            add("unsupported_claims", f"{len(unsupported)} extracted item(s) could not be tied to verbatim source text: " + ", ".join(sorted({i.subject for i in unsupported})[:6]) + ".", "agg")
        if coverage and coverage < threshold:
            add("low_evidence_coverage", f"Only {coverage:.0%} of extracted items have verified evidence.", "coverage")
        for i in issues:
            if i.kind == "contradiction":
                add("conflicting_provisions", i.message, i.subject, i.severity)
            elif i.kind == "date":
                add("date_inconsistency", i.message, f"{i.subject}:{i.message[:30]}", i.severity)
        if parties_bad:
            add("party_reference_mismatch", f"{parties_bad} obligation(s) refer to a party name that matches no identified party.", "agg")
        _ = ComputationStatus
