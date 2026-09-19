"""AI Copilot service: routes questions to deterministic tools over authorised data or to grounded RAG."""
from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from app.agents.base import AgentContext
from app.agents.copilot import INSUFFICIENT, CopilotAnswer, GroundedCopilotAgent, Intent, RoutedIntent, SourceRef
from app.agents.data import AgentData
from app.agents.llm import LLMClient, MeteredLLM
from app.config.settings import Settings
from app.core.errors import AIUnavailableError, ContractLensError
from app.core.logging import get_logger
from app.database.store import F
from app.models.entities import Evidence
from app.models.enums import DeadlineKind, DeadlineStatus, FindingType, SubjectType, ValidationStatus
from app.rag.retriever import HybridRetriever, RetrievalScope
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.security.redaction import redact_text
from app.security.tool_policy import ToolPolicy
from app.services.audit import AuditService
from app.services.deadlines import DeadlineService
from app.services.obligations import ObligationService
from app.services.renewals import RenewalService
from app.rag.text_utils import tokenize

log = get_logger(__name__)


class CopilotService:
    def __init__(self, principal: Principal, repos: Repositories, settings: Settings, llm: LLMClient, retriever: HybridRetriever, audit: AuditService,
                 obligations: ObligationService, deadlines: DeadlineService, compare=None) -> None:
        self._p, self._r, self._settings, self._llm = principal, repos, settings, llm
        self._retriever, self._audit, self._obligations, self._deadlines = retriever, audit, obligations, deadlines
        self._agent = GroundedCopilotAgent()
        self._compare = compare  # callable(contract_id) -> summary text | None

    def ask(self, question: str, *, contract_id: UUID | str | None = None, today: date | None = None) -> CopilotAnswer:
        self._p.require(Permission.CONTRACTS_READ)
        q = question.strip()
        if not q:
            return CopilotAnswer(question, Intent.CONTRACT_QA, "Please enter a question.", insufficient_evidence=True, mode="refused")
        if len(q) > 2000:
            q = q[:2000]
        today = today or date.today()
        llm = MeteredLLM(self._llm)
        ctx = AgentContext(principal=self._p, settings=self._settings, llm=llm, data=AgentData(self._r), policy=ToolPolicy(), retriever=self._retriever)
        routed = self._agent.route(ctx, q)
        try:
            ans = self._dispatch(ctx, routed, q, contract_id, today)
        except AIUnavailableError as exc:
            ans = CopilotAnswer(q, routed.intent, exc.user_message, insufficient_evidence=True, mode="refused")
        except ContractLensError as exc:
            ans = CopilotAnswer(q, routed.intent, f"The question could not be answered: {exc.user_message}", insufficient_evidence=True, mode="refused")
        self._audit.record("copilot.ask", "copilot", None, intent=routed.intent.value, routed_by=routed.method, mode=ans.mode, question=redact_text(q[:200]),
                           sources=len(ans.sources), scope="contract" if contract_id else "portfolio")
        return ans

    # ------------------------------------------------------------------
    def _dispatch(self, ctx: AgentContext, routed: RoutedIntent, q: str, contract_id, today: date) -> CopilotAnswer:
        i = routed.intent
        if i is Intent.LIST_EXPIRING:
            return self._expiring(q, routed.days_horizon or 90, today, contract_id)
        if i is Intent.OBLIGATIONS_BY_PARTY and routed.party:
            return self._by_party(q, routed.party, contract_id, today)
        if i is Intent.OBLIGATIONS_WITHOUT_DEADLINE:
            return self._without_deadline(q, contract_id, today)
        if i is Intent.EVENT_DEPENDENT_DEADLINES:
            return self._event_deadlines(q, routed.topic or "", contract_id, today)
        if i is Intent.EXPLAIN_DEADLINE:
            return self._explain_deadline(q, contract_id, today)
        if i is Intent.UNRESOLVED_INFORMATION:
            return self._unresolved(q, contract_id)
        if i is Intent.COMPARE_VERSIONS:
            return self._compare_versions(q, contract_id, routed.contract_hint)
        return self._rag(ctx, q, contract_id)

    def _visible_contracts(self, contract_id=None):
        cs = self._r.contracts.list([F.is_null("deleted_at")])
        return [c for c in cs if not contract_id or str(c.id) == str(contract_id)]

    def _sources_for(self, subject_type: SubjectType, ids: list[UUID], titles: dict[UUID, str], limit: int = 8) -> list[SourceRef]:
        out: list[SourceRef] = []
        if not ids:
            return out
        for e in self._r.evidence.list([F.eq("subject_type", subject_type), F.in_("subject_id", [str(i) for i in ids])], limit=200):
            out.append(self._source(e, titles.get(e.contract_id, "Contract"), f"E{len(out) + 1}"))
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _source(e: Evidence, title: str, label: str) -> SourceRef:
        return SourceRef(label, str(e.contract_id), title, str(e.document_id) if e.document_id else "", str(e.chunk_id) if e.chunk_id else "", e.page_number,
                         e.section_reference, e.quote, e.verified, e.char_start, e.char_end, str(e.id))

    # -- structured intents ------------------------------------------------------------------
    def _expiring(self, q: str, days: int, today: date, contract_id) -> CopilotAnswer:
        rows = [r for r in RenewalService(self._r).radar(today) if not contract_id or str(r.contract.id) == str(contract_id)]
        hits = [r for r in rows if r.days_to_end is not None and 0 <= r.days_to_end <= days]
        titles = {r.contract.id: r.contract.title for r in rows}
        if not hits:
            return CopilotAnswer(q, Intent.LIST_EXPIRING, f"No contract in the portfolio reaches the end of its current term within the next {days} days, based on the extracted dates.",
                                 uncertainty="Only contracts with an established expiration date are included.", mode="structured")
        exp_dl = [d.id for d in self._r.live_deadlines([F.eq("label", "Contract expiration")]) if d.contract_id in {h.contract.id for h in hits}]
        sources = self._sources_for(SubjectType.DEADLINE, exp_dl, titles)
        lines = [f"{r.contract.title}: term ends {r.current_term_end.isoformat()} ({r.days_to_end} days)"
                 + (f"; non-renewal notice due {r.notice_deadline.due_date.isoformat()}" if r.notice_deadline and r.notice_deadline.due_date else "")
                 + ("; auto-renews" if r.auto_renews else "") for r in hits]
        unc = "Dates are AI-extracted and calculated; " + ("some assume automatic renewal occurred. " if any(r.renewed_terms_assumed for r in hits) else "") + "confirm them in the Renewal Radar."
        return CopilotAnswer(q, Intent.LIST_EXPIRING, f"{len(hits)} contract(s) reach the end of their current term within {days} days:\n" + "\n".join("• " + line for line in lines),
                             sources, unc, False, "structured", rows=[{"contract": r.contract.title, "term_end": r.current_term_end.isoformat(), "days": r.days_to_end} for r in hits])

    def _by_party(self, q: str, party: str, contract_id, today: date) -> CopilotAnswer:
        rows = self._obligations.list(contract_id=contract_id, party=party, view="all", today=today)
        rows = [r for r in rows if party.lower() in (r.obligation.responsible_party_name or "").lower()]
        if not rows:
            return CopilotAnswer(q, Intent.OBLIGATIONS_BY_PARTY, f"No obligations assigned to '{party}' were found in the extracted data. {INSUFFICIENT}", insufficient_evidence=True, mode="structured")
        titles = {r.obligation.contract_id: r.contract_title for r in rows}
        sources = self._sources_for(SubjectType.OBLIGATION, [r.obligation.id for r in rows[:12]], titles, limit=10)
        body = "\n".join(f"• [{r.contract_title}] {r.obligation.title}" + (f" — next due {r.next_due.isoformat()}" if r.next_due else "") for r in rows[:25])
        return CopilotAnswer(q, Intent.OBLIGATIONS_BY_PARTY, f"{len(rows)} obligation(s) are assigned to '{party}':\n{body}", sources,
                             "Obligations are AI-extracted; unreviewed items may need correction.", False, "structured",
                             rows=[{"contract": r.contract_title, "obligation": r.obligation.title, "next_due": r.next_due.isoformat() if r.next_due else None} for r in rows])

    def _without_deadline(self, q: str, contract_id, today: date) -> CopilotAnswer:
        rows = self._obligations.list(contract_id=contract_id, view="no_deadline", today=today)
        if not rows:
            return CopilotAnswer(q, Intent.OBLIGATIONS_WITHOUT_DEADLINE, "Every open obligation has an explicit or derived deadline.", mode="structured")
        titles = {r.obligation.contract_id: r.contract_title for r in rows}
        body = "\n".join(f"• [{r.contract_title}] {r.obligation.title}" + (" (awaiting event)" if r.deadline_kind is DeadlineKind.EVENT_TRIGGERED else "") for r in rows[:30])
        return CopilotAnswer(q, Intent.OBLIGATIONS_WITHOUT_DEADLINE, f"{len(rows)} open obligation(s) have no explicit or calculated deadline:\n{body}",
                             self._sources_for(SubjectType.OBLIGATION, [r.obligation.id for r in rows[:10]], titles), "Some depend on events that have not been recorded yet.", False, "structured",
                             rows=[{"contract": r.contract_title, "obligation": r.obligation.title} for r in rows])

    def _event_deadlines(self, q: str, topic: str, contract_id, today: date) -> CopilotAnswer:
        toks = {t for t in tokenize(topic)} or {"invoice"}
        titles = {c.id: c.title for c in self._visible_contracts(contract_id)}
        hits = []
        for d in self._r.live_deadlines([F.eq("kind", DeadlineKind.EVENT_TRIGGERED), F.eq("status", DeadlineStatus.OPEN)]):
            if d.contract_id not in titles:
                continue
            hay = set(tokenize(" ".join([d.anchor_event or "", " ".join(map(str, d.missing_anchors)), d.label])))
            if toks & hay:
                hits.append(d)
        if not hits:
            return CopilotAnswer(q, Intent.EVENT_DEPENDENT_DEADLINES, f"No deadlines depending on '{topic}' were found. {INSUFFICIENT}", insufficient_evidence=True, mode="structured")
        body = "\n".join(f"• [{titles[d.contract_id]}] {d.label}: " + (f"due {d.due_date.isoformat()}" if d.due_date else f"awaiting event ({', '.join(map(str, d.missing_anchors)) or 'not recorded'})") for d in hits)
        return CopilotAnswer(q, Intent.EVENT_DEPENDENT_DEADLINES, f"{len(hits)} deadline(s) depend on '{topic}':\n{body}", self._sources_for(SubjectType.DEADLINE, [d.id for d in hits], titles),
                             "These deadlines resolve when the event date is recorded on the contract.", False, "structured",
                             rows=[{"contract": titles[d.contract_id], "deadline": d.label, "due": d.due_date.isoformat() if d.due_date else None} for d in hits])

    def _explain_deadline(self, q: str, contract_id, today: date) -> CopilotAnswer:
        titles = {c.id: c.title for c in self._visible_contracts(contract_id)}
        qt = set(tokenize(q)) - {"deadline", "calculated", "calculation", "explain", "logic", "work", "works"}
        best, score = None, 0
        for d in self._r.live_deadlines():
            if d.contract_id not in titles:
                continue
            s = len(qt & set(tokenize(d.label + " " + titles[d.contract_id])))
            if s > score:
                best, score = d, s
        if best is None:
            return CopilotAnswer(q, Intent.EXPLAIN_DEADLINE, f"I could not tell which deadline you mean. Name the deadline or contract. {INSUFFICIENT}", insufficient_evidence=True, mode="structured")
        trace = "\n".join(f"{n}. {t}" for n, t in enumerate(best.calculation_trace, 1)) or "No calculation trace is stored."
        assumptions = "\n".join(f"• {a}" for a in best.assumptions)
        text = (f"'{best.label}' ({titles[best.contract_id]}) — result: {best.due_date.isoformat() if best.due_date else 'not yet calculable'} "
                f"[{best.validation_status.value.replace('_', ' ')}]\nCalculation:\n{trace}" + (f"\nAssumptions:\n{assumptions}" if assumptions else "") +
                (f"\nMissing: {', '.join(map(str, best.missing_anchors))}" if best.missing_anchors else ""))
        return CopilotAnswer(q, Intent.EXPLAIN_DEADLINE, text, self._sources_for(SubjectType.DEADLINE, [best.id], titles),
                             None if best.validation_status is ValidationStatus.CONFIRMED else "This date has not been confirmed by a person.", False, "structured")

    def _unresolved(self, q: str, contract_id) -> CopilotAnswer:
        titles = {c.id: c.title for c in self._visible_contracts(contract_id)}
        lines: list[str] = []
        for f in self._r.findings.list([F.eq("finding_type", FindingType.MISSING_INFORMATION), F.eq("status", "open")]):
            if f.contract_id in titles:
                lines.append(f"[{titles[f.contract_id]}] {f.title}")
        for d in self._r.live_deadlines([F.eq("validation_status", ValidationStatus.UNRESOLVED), F.eq("status", DeadlineStatus.OPEN)]):
            if d.contract_id in titles:
                lines.append(f"[{titles[d.contract_id]}] Deadline '{d.label}' is unresolved (missing: {', '.join(map(str, d.missing_anchors)) or 'terms'})")
        if not lines:
            return CopilotAnswer(q, Intent.UNRESOLVED_INFORMATION, "No unresolved or missing information is currently flagged.", mode="structured")
        return CopilotAnswer(q, Intent.UNRESOLVED_INFORMATION, f"{len(lines)} item(s) are missing or unresolved:\n" + "\n".join("• " + line for line in lines[:40]), [],
                             "Missing items may mean the contract is silent or that extraction missed a clause; check the source document.", False, "structured")

    def _compare_versions(self, q: str, contract_id, hint: str | None) -> CopilotAnswer:
        cands = self._visible_contracts(contract_id)
        if hint:
            cands = [c for c in cands if hint.lower() in c.title.lower()] or cands
        cands = [c for c in cands if len(self._r.versions.list([F.eq("contract_id", str(c.id))])) >= 2]
        if not cands:
            return CopilotAnswer(q, Intent.COMPARE_VERSIONS, f"No contract with two or more versions was found. {INSUFFICIENT}", insufficient_evidence=True, mode="structured")
        c = cands[0]
        am = self._r.amendments.list([F.eq("contract_id", str(c.id))], order_by=[("created_at", True)], limit=1)
        summary = am[0] if am else None
        if summary is None and self._compare:
            self._compare(c.id)
            am = self._r.amendments.list([F.eq("contract_id", str(c.id))], order_by=[("created_at", True)], limit=1)
            summary = am[0] if am else None
        if summary is None:
            return CopilotAnswer(q, Intent.COMPARE_VERSIONS, "A comparison could not be produced for this contract.", insufficient_evidence=True, mode="structured")
        lines = [ch.get("summary", "") for ch in summary.changes if ch.get("kind") in ("field", "clause")][:20]
        return CopilotAnswer(q, Intent.COMPARE_VERSIONS, f"{c.title}: {summary.summary}\n" + "\n".join("• " + s for s in lines), [], "Comparison is deterministic on stored text; review the amendment record for full wording.", False, "structured",
                             rows=summary.changes[:50])

    # -- RAG ----------------------------------------------------------------------------------
    def _rag(self, ctx: AgentContext, q: str, contract_id) -> CopilotAnswer:
        visible = self._visible_contracts(contract_id)
        scope = RetrievalScope(contract_ids=[str(c.id) for c in visible])  # authorised, non-deleted contracts only
        titles = {str(c.id): c.title for c in visible}
        return self._agent.answer(ctx, q, scope, titles=titles)


_ = (re, Any)
