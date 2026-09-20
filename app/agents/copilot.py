"""Grounded Contract Copilot: intent routing and evidence-checked question answering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.evidence import ChunkView, resolve_all
from app.agents.prompts import INTENT_SYSTEM, QA_SYSTEM
from app.core.errors import AIUnavailableError, ContractLensError
from app.rag.context import build_context
from app.rag.retriever import RetrievalScope
from app.schemas.llm_outputs import AnswerOut, IntentOut
from app.security import prompt_guard
from app.security.tool_policy import Capability

INSUFFICIENT = "The answer cannot be established from the available contract content."


class Intent(StrEnum):
    LIST_EXPIRING = "list_expiring"
    OBLIGATIONS_BY_PARTY = "obligations_by_party"
    OBLIGATIONS_WITHOUT_DEADLINE = "obligations_without_deadline"
    EVENT_DEPENDENT_DEADLINES = "event_dependent_deadlines"
    COMPARE_VERSIONS = "compare_versions"
    EXPLAIN_DEADLINE = "explain_deadline"
    UNRESOLVED_INFORMATION = "unresolved_information"
    CONTRACT_QA = "contract_qa"


@dataclass
class RoutedIntent:
    intent: Intent
    days_horizon: int | None = None
    topic: str | None = None
    party: str | None = None
    contract_hint: str | None = None
    method: str = "heuristic"


@dataclass
class SourceRef:
    label: str
    contract_id: str
    contract_title: str
    document_id: str
    chunk_id: str
    page_number: int | None
    section_reference: str | None
    excerpt: str
    verified: bool = True
    char_start: int | None = None
    char_end: int | None = None
    evidence_id: str | None = None


@dataclass
class CopilotAnswer:
    question: str
    intent: Intent
    answer: str
    sources: list[SourceRef] = field(default_factory=list)
    uncertainty: str | None = None
    insufficient_evidence: bool = False
    mode: str = "rag"  # rag | structured | extractive | refused
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_DAYS = re.compile(r"(?:next|within|in the next|over the next)\s+(\d{1,4})\s*(day|week|month|year)s?", re.I)


def heuristic_intent(question: str) -> RoutedIntent:
    """Deterministic router used when no LLM is available (and as a cross-check)."""
    q = question.lower()
    m = _DAYS.search(q)
    if re.search(r"\bexpir|\bexpiring|\bend(s|ing)?\b.*\b(days|weeks|months)", q) and (m or "expire" in q):
        days = None
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        return RoutedIntent(Intent.LIST_EXPIRING, days_horizon=days or 90)
    if re.search(r"without an? (explicit )?deadline|no (explicit )?deadline|lack(ing)? a deadline", q):
        return RoutedIntent(Intent.OBLIGATIONS_WITHOUT_DEADLINE)
    dep = re.search(r"depend(s|ent)? on ([a-z ]+?)(?:\?|$| in | for )", q)
    if dep and "deadline" in q:
        return RoutedIntent(Intent.EVENT_DEPENDENT_DEADLINES, topic=dep.group(2).strip())
    if "compare" in q and ("version" in q or "amendment" in q or "v1" in q):
        return RoutedIntent(Intent.COMPARE_VERSIONS)
    if re.search(r"(how|explain).*(deadline).*(calculat|derive|work)|deadline calculation", q) or re.search(r"explain.*deadline", q):
        return RoutedIntent(Intent.EXPLAIN_DEADLINE, topic=question)
    if re.search(r"unresolved|missing information|not stated|what is missing", q):
        return RoutedIntent(Intent.UNRESOLVED_INFORMATION)
    p = re.search(r"obligations?\s+(?:does|do|of|for|assigned to|has|have)\s+(?:the\s+)?([a-z][a-z .&-]{1,40}?)(?:\s+(?:have|under|in)\b|\?|$)", q)
    if p and "obligation" in q:
        return RoutedIntent(Intent.OBLIGATIONS_BY_PARTY, party=p.group(1).strip())
    return RoutedIntent(Intent.CONTRACT_QA)


class GroundedCopilotAgent(Agent):
    name = "grounded_copilot"
    title = "Grounded Copilot"
    description = "Answers questions from retrieved contract passages with verified citations; refuses when evidence is insufficient."

    def run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover - the copilot is driven through answer()
        return AgentResult(AgentStatus.SKIPPED, {"reason": "use answer()"})

    # ------------------------------------------------------------------ routing
    def route(self, ctx: AgentContext, question: str) -> RoutedIntent:
        base = heuristic_intent(question)
        if not ctx.llm.available or getattr(ctx.llm, "offline_rules", False):
            return base
        try:
            self.need(ctx, Capability.LLM_STRUCTURED)
            out = ctx.llm.structured(system=INTENT_SYSTEM, user=prompt_guard.wrap_untrusted(question, "QUESTION"), schema=IntentOut, purpose="intent_routing")
        except ContractLensError:
            return base
        try:
            intent = Intent(out.intent.strip())
        except ValueError:
            intent = Intent.CONTRACT_QA  # unknown intents can never select a tool
        if base.intent is not Intent.CONTRACT_QA and intent is Intent.CONTRACT_QA:
            return base  # deterministic router is more specific
        return RoutedIntent(intent, out.days_horizon or base.days_horizon, out.topic or base.topic, out.party or base.party, out.contract_hint, method="llm")

    # ------------------------------------------------------------------ RAG answer
    def answer(self, ctx: AgentContext, question: str, scope: RetrievalScope | None, *, titles: dict[str, str], intent: Intent = Intent.CONTRACT_QA) -> CopilotAnswer:
        self.need(ctx, Capability.RETRIEVAL_READ)
        assert ctx.retriever is not None
        result = ctx.retriever.retrieve(question, scope, k=8)
        relevant = result.relevant_chunks
        if not relevant:
            return CopilotAnswer(question, intent, INSUFFICIENT, [], "No passage in the searched contracts matched the question.", True, "rag",
                                 notes=[f"Searched {result.diagnostics.get('corpus', 0)} passage(s)."])
        context_text, citations = build_context(relevant, titles)
        labels = {c.label: ChunkView(id=_uuid(c.chunk_id), document_id=_uuid(c.document_id), contract_id=_uuid(c.contract_id), version_id=_uuid(c.chunk_id), index=i,
                                     text=c.text, page_number=c.page_number or 0, section_reference=c.section_reference, section_title=c.section_title,
                                     clause_type="other", char_start=0, char_end=len(c.text)) for i, c in enumerate(citations)}
        by_label = {c.label: c for c in citations}
        if not ctx.llm.available or getattr(ctx.llm, "offline_rules", False):
            return CopilotAnswer(question, intent, "AI answer generation is not configured, so no answer was written. The most relevant passages are shown below.",
                                 [self._source(c, c.text[:500], True) for c in citations[:4]], "Passages were found by search only and have not been interpreted.", False, "extractive")
        self.need(ctx, Capability.LLM_STRUCTURED)
        out = ctx.llm.structured(
            system=QA_SYSTEM, purpose="grounded_qa",
            user=f"Question (untrusted user text):\n{prompt_guard.wrap_untrusted(question, 'QUESTION')}\n\nPassages:\n\n{context_text}\n\nAnswer using only these passages.",
            schema=AnswerOut,
        )
        resolved = resolve_all(out.citations, labels)
        sources: list[SourceRef] = []
        for ev in resolved:
            if not ev.verified:
                continue
            cit = next((c for c in citations if str(c.chunk_id) == str(ev.chunk_id)), None)
            if cit:
                sources.append(self._source(cit, ev.quote, True, ev.char_start, ev.char_end))
        dropped = len(resolved) - len(sources)
        if out.insufficient_evidence:
            ans = out.answer.strip()
            text = ans if INSUFFICIENT.lower() in ans.lower() else f"{INSUFFICIENT} {ans}".strip()
            return CopilotAnswer(question, intent, text, sources, out.uncertainty, True, "rag")
        if not sources:
            return CopilotAnswer(
                question, intent,
                "The model produced an answer that could not be supported by verbatim quotes from the contract, so it is withheld. Review the passages below directly.",
                [self._source(c, c.text[:500], True) for c in citations[:3]], "Answer withheld: citation verification failed.", True, "rag",
                notes=[f"{len(out.citations)} citation(s) returned; none verified."],
            )
        notes = [f"{dropped} citation(s) were dropped because their quotes could not be verified."] if dropped else []
        _ = by_label
        return CopilotAnswer(question, intent, out.answer.strip(), sources, out.uncertainty, False, "rag", notes=notes)

    @staticmethod
    def _source(c, excerpt: str, verified: bool, s: int | None = None, e: int | None = None) -> SourceRef:
        return SourceRef(c.label, c.contract_id, c.contract_title, c.document_id, c.chunk_id, c.page_number, c.section_reference, excerpt, verified, s, e)


def _uuid(value: str):
    from uuid import UUID
    try:
        return UUID(str(value))
    except ValueError:
        from uuid import uuid5, NAMESPACE_URL
        return uuid5(NAMESPACE_URL, str(value))


_ = (AIUnavailableError,)
