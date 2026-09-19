"""Risk Triage Agent: produces *review signals* (not legal conclusions), separating business risk from extraction uncertainty."""
from __future__ import annotations

from datetime import date, timedelta

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.evidence import resolve_all
from app.agents.prompts import RISK_SYSTEM
from app.agents.state import ExtractedContract, FindingDraft
from app.agents.util import fingerprint, norm_key
from app.agents.windows import key_window
from app.config.risk import SEVERITY_WEIGHT, SIGNALS, RiskConfig
from app.core.errors import AIUnavailableError, ContractLensError
from app.models.enums import ClauseType, ContractType, DeadlineCategory, DeadlineKind, FindingType, PlaybookRuleType, Severity, SignalCategory
from app.schemas.llm_outputs import RiskSignals
from app.security.tool_policy import Capability
from app.tools.temporal import ComputationStatus


class RiskTriageAgent(Agent):
    name = "risk_triage"
    title = "Risk Triage"
    description = "Flags ambiguity, missing information, unusual terms, deadline exposure, unassigned obligations and playbook deviations for human review."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.COMPUTE)
        cfg: RiskConfig = ctx.params.get("risk_config") or RiskConfig()
        today: date = ctx.params.get("today") or date.today()
        warnings: list[str] = []
        drafts: list[FindingDraft] = []

        def add(key: str, description: str, *, detail: str = "", evidence=None, severity: Severity | None = None, obligation_fp: str | None = None,
                deadline_fp: str | None = None, weight: float | None = None, title: str | None = None, confidence: float = 1.0) -> None:
            if not cfg.enabled(key):
                return
            sig = SIGNALS[key]
            drafts.append(FindingDraft(
                finding_type=sig.finding_type, severity=severity or sig.severity, category=sig.category, title=title or sig.title, description=description,
                weight=weight if weight is not None else cfg.weight_for(key), evidence=list(evidence or []),
                fingerprint=fingerprint("risk", key, norm_key(detail) or norm_key(title or "")), obligation_fingerprint=obligation_fp,
                deadline_fingerprint=deadline_fp, confidence=confidence, signal=key,
            ))

        ex = ctx.state.contract
        if ex is not None:
            self._contract_rules(ex, cfg, today, add)
            self._playbook(ctx, ex, add)
        self._deadline_rules(ctx, cfg, today, add)
        obs = ctx.state.obligations
        if obs:  # owners are assigned by humans after ingestion
            n = len(obs)
            add("unassigned_obligations", f"{n} extracted obligation(s) have no business owner yet. Assign owners in Obligation Operations.",
                weight=min(10.0, cfg.weight_for("unassigned_obligations") + 0.4 * n), detail="agg")
        if ctx.state.profile and ctx.state.profile.ocr_used and ctx.state.profile.ocr_confidence is not None and ctx.state.profile.ocr_confidence < 0.8:
            add("low_ocr_confidence", f"Average OCR confidence is {ctx.state.profile.ocr_confidence:.0%}. Verify extracted values against the original scan.")
        if ctx.state.amendment:
            for fc in ctx.state.amendment.field_changes[:8]:
                add("amendment_change", fc.summary, detail=fc.field, title=f"Amendment changes: {fc.field.replace('_', ' ')}")
        self._llm_signals(ctx, cfg, drafts, warnings)
        ctx.state.findings.extend(drafts)
        return AgentResult(AgentStatus.WARNING if warnings else AgentStatus.OK,
                           {"signals": len(drafts), "business": sum(1 for d in drafts if d.category is SignalCategory.BUSINESS_RISK),
                            "extraction": sum(1 for d in drafts if d.category is SignalCategory.EXTRACTION_UNCERTAINTY)}, warnings)

    # ------------------------------------------------------------------ deterministic rules
    def _contract_rules(self, ex: ExtractedContract, cfg: RiskConfig, today: date, add) -> None:
        if len(ex.parties) < 2:
            add("missing_parties", f"{len(ex.parties)} party/parties were identified. Contracts normally name at least two.")
        if not ex.effective_date.present:
            add("missing_effective_date", "No unambiguous effective date could be established, so date-based deadlines may be unresolved.")
        if not ex.expiration_date.present and not ex.initial_term_months.present:
            add("missing_term", "Neither an expiration date nor a term length could be established.")
        if not ex.governing_law.present:
            add("missing_governing_law", "No governing-law provision was found. Confirm whether the contract is silent or the clause was missed.")
        if ex.contract_type is not ContractType.NDA and not ex.payment_terms.present and not ex.payment_days.present:
            add("missing_payment_terms", "No payment terms were found. Confirm whether the contract is silent or the clause was missed.")
        has_term_clause = any(c.clause_type is ClauseType.TERMINATION for c in ex.clauses)
        if not has_term_clause and not ex.termination_convenience_notice_days.present and not ex.termination_cure_days.present:
            add("missing_termination", "No termination provision was identified.")
        notice = ex.renewal_notice_days.value
        if ex.auto_renews.value:
            if notice is None:
                add("auto_renewal_no_notice", "The contract renews automatically, but no notice period to prevent renewal was found.", evidence=ex.auto_renews.evidence)
            else:
                ev = ex.renewal_notice_days.evidence + ex.auto_renews.evidence
                if notice <= 30:
                    add("auto_renewal_short_notice", f"Only {notice} days' notice is available to stop the automatic renewal.", evidence=ev)
                elif notice >= 90:
                    add("auto_renewal_long_notice", f"{notice} days' notice is required to stop the automatic renewal.", evidence=ev)
                else:
                    add("auto_renewal_notice", f"The contract renews automatically unless notice is given {notice} days before term end.", evidence=ev)
        exp = ex.expiration_date.value
        if exp:
            if exp < today and not (ex.auto_renews.value and ex.renewal_term_months.value):
                add("past_expiration", f"The stated expiration date ({exp.isoformat()}) has passed. Confirm renewal or termination status.", evidence=ex.expiration_date.evidence)
            elif today <= exp <= today + timedelta(days=cfg.expiring_days):
                add("expiring_soon", f"The contract expires on {exp.isoformat()} ({(exp - today).days} days).", evidence=ex.expiration_date.evidence)

    def _deadline_rules(self, ctx: AgentContext, cfg: RiskConfig, today: date, add) -> None:
        unresolved = [d for d in ctx.state.deadlines if d.computation.status is ComputationStatus.UNRESOLVED and d.computation.rule.kind is not DeadlineKind.EVENT_TRIGGERED]
        if unresolved:
            names = ", ".join(sorted({d.label for d in unresolved})[:5])
            add("unresolved_deadlines", f"{len(unresolved)} deadline(s) could not be calculated because required dates or terms are missing: {names}.",
                detail="agg")
        with_assumptions = [d for d in ctx.state.deadlines if d.computation.assumptions and d.computation.status is not ComputationStatus.UNRESOLVED]
        if with_assumptions:
            add("deadline_assumptions", f"{len(with_assumptions)} calculated deadline(s) depend on stated assumptions (see each calculation trace).", detail="agg")
        for d in ctx.state.deadlines:
            due = d.computation.due_date
            if not due or d.computation.status is ComputationStatus.UNRESOLVED:
                continue
            if d.category == DeadlineCategory.RENEWAL_NOTICE.value and today <= due <= today + timedelta(days=cfg.renewal_window_days):
                add("renewal_window_approaching", f"The notice deadline is {due.isoformat()} ({(due - today).days} days away).", detail=d.fingerprint,
                    evidence=d.evidence, deadline_fp=d.fingerprint, title="Renewal notice deadline is approaching")
            elif due < today and d.category != DeadlineCategory.EXPIRATION.value:
                add("overdue_deadline", f"'{d.label}' was due {due.isoformat()} ({(today - due).days} days ago). Confirm whether it was completed.", detail=d.fingerprint,
                    evidence=d.evidence, deadline_fp=d.fingerprint, obligation_fp=d.obligation_fingerprint, title=f"Overdue: {d.label}")

    def _playbook(self, ctx: AgentContext, ex: ExtractedContract, add) -> None:
        for rule in ctx.data.playbook_rules():
            p = rule.params or {}
            sev, w = rule.severity, SEVERITY_WEIGHT[rule.severity]
            title = f"Playbook: {rule.name}"

            def flag(msg: str, evidence=None) -> None:
                add("playbook_deviation", msg, detail=rule.name, severity=sev, weight=w, title=title, evidence=evidence)

            t = rule.rule_type
            if t is PlaybookRuleType.REQUIRE_CLAUSE and rule.clause_type:
                if not any(c.clause_type == rule.clause_type for c in ex.clauses):
                    flag(f"No '{rule.clause_type.value}' clause was identified, but the playbook requires one.")
            elif t is PlaybookRuleType.MAX_NOTICE_DAYS:
                for fact, label in ((ex.renewal_notice_days, "renewal notice"), (ex.termination_convenience_notice_days, "termination notice")):
                    if fact.value is not None and fact.value > int(p.get("days", 0)):
                        flag(f"{label.capitalize()} of {fact.value} days exceeds the playbook maximum of {p.get('days')} days.", fact.evidence)
            elif t is PlaybookRuleType.MIN_NOTICE_DAYS:
                for fact, label in ((ex.renewal_notice_days, "renewal notice"), (ex.termination_convenience_notice_days, "termination notice")):
                    if fact.value is not None and fact.value < int(p.get("days", 0)):
                        flag(f"{label.capitalize()} of {fact.value} days is below the playbook minimum of {p.get('days')} days.", fact.evidence)
            elif t is PlaybookRuleType.FORBID_AUTO_RENEWAL and ex.auto_renews.value:
                flag("The contract renews automatically, which the playbook does not permit.", ex.auto_renews.evidence)
            elif t is PlaybookRuleType.MAX_PAYMENT_DAYS and ex.payment_days.value is not None and ex.payment_days.value > int(p.get("days", 0)):
                flag(f"Payment period of {ex.payment_days.value} days exceeds the playbook maximum of {p.get('days')} days.", ex.payment_days.evidence)
            elif t is PlaybookRuleType.REQUIRE_GOVERNING_LAW:
                allowed = [str(a).lower() for a in p.get("allowed", [])]
                gl = (ex.governing_law.value or "").lower()
                if not gl:
                    flag("Governing law was not identified; the playbook restricts governing law.")
                elif allowed and not any(a in gl for a in allowed):
                    flag(f"Governing law '{ex.governing_law.value}' is outside the approved list ({', '.join(p.get('allowed', []))}).", ex.governing_law.evidence)
            elif t is PlaybookRuleType.FORBID_PHRASE:
                phrases = [str(x).lower() for x in p.get("phrases", [])]
                for c in ctx.data.chunks(ctx.version_id):
                    hit = next((ph for ph in phrases if ph and ph in c.text.lower()), None)
                    if hit:
                        flag(f"The document contains '{hit}', which the playbook forbids (section {c.section_reference or '-'}, page {c.page_number}).")
                        break

    # ------------------------------------------------------------------ optional LLM pass
    def _llm_signals(self, ctx: AgentContext, cfg: RiskConfig, drafts: list[FindingDraft], warnings: list[str]) -> None:
        if not ctx.llm.available or ctx.version_id is None:
            return
        try:
            self.need(ctx, Capability.LLM_STRUCTURED)
            chunks = ctx.data.chunks(ctx.version_id)
            window = key_window(chunks, budget=20000)
            out = ctx.llm.structured(system=RISK_SYSTEM, user=f"Contract passages:\n\n{window.text}\n\nReport review signals now.", schema=RiskSignals, purpose="risk_signals")
        except AIUnavailableError:
            return
        except ContractLensError as exc:
            warnings.append(f"AI risk-signal pass skipped: {exc.user_message}")
            return
        mapping = {FindingType.AMBIGUITY: "ambiguous_language", FindingType.CONFLICT: "conflicting_provisions",
                   FindingType.MISSING_INFORMATION: "missing_termination", FindingType.RISK_SIGNAL: "unusual_term"}
        for s in out.signals:
            ev = resolve_all(s.evidence, window.labels)
            if not any(e.verified for e in ev):
                warnings.append(f"Dropped AI risk signal '{s.title[:50]}': it could not be tied to quoted text.")
                continue
            key = mapping.get(s.signal_type, "unusual_term")
            if key == "missing_termination":
                key = "unusual_term"
            if not cfg.enabled(key):
                continue
            sig = SIGNALS[key]
            drafts.append(FindingDraft(
                finding_type=s.signal_type if s.signal_type in (FindingType.AMBIGUITY, FindingType.CONFLICT, FindingType.RISK_SIGNAL) else FindingType.RISK_SIGNAL,
                severity=s.severity, category=sig.category, title=s.title.strip()[:160], description=s.description.strip(),
                weight=cfg.weight_for(key) * max(0.3, min(1.0, s.confidence)), evidence=ev, confidence=max(0.0, min(1.0, s.confidence)),
                fingerprint=fingerprint("risk", key, norm_key(s.title)), signal=key,
            ))
