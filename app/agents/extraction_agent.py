"""Contract Extraction Agent: parties, dates, financial/renewal/termination terms, clauses — all evidence-backed."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.evidence import ResolvedEvidence, normalise, resolve_all
from app.agents.prompts import EXTRACTION_SYSTEM
from app.agents.state import ClauseDraft, ExtractedContract, Fact, PartyDraft
from app.agents.util import date_supported, fingerprint, norm_key, number_supported, party_key
from app.agents.windows import Window, key_window
from app.core.errors import DocumentProcessingError
from app.schemas.llm_outputs import BoolFact, ContractExtraction, DateFact, IntFact, NumberFact, TextFact
from app.security.tool_policy import Capability
from app.tools.temporal import parse_date_text


class ContractExtractionAgent(Agent):
    name = "contract_extraction"
    title = "Contract Extraction"
    description = "Extracts parties, dates, financial, renewal and termination terms and key clauses with source evidence."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.LLM_STRUCTURED)
        assert ctx.version_id is not None
        chunks = ctx.data.chunks(ctx.version_id)
        if not chunks:
            raise DocumentProcessingError("no chunks", user_message="This version has no indexed text to analyse.")
        window = key_window(chunks)
        ctx.emit("agent.progress", {"agent": self.name, "message": f"Analysing {len(window.chunks)} key passages"})
        out = ctx.llm.structured(
            system=EXTRACTION_SYSTEM,
            user=f"Contract passages:\n\n{window.text}\n\nExtract the structured contract facts now.",
            schema=ContractExtraction, purpose="contract_extraction",
        )
        warnings: list[str] = []
        ex = self.resolve(out, window, warnings)
        ctx.state.contract = ex
        verified = sum(1 for f in ex.facts().values() if f.present and f.verified)
        present = sum(1 for f in ex.facts().values() if f.present)
        return AgentResult(AgentStatus.WARNING if warnings else AgentStatus.OK,
                           {"parties": len(ex.parties), "facts_present": present, "facts_verified": verified, "clauses": len(ex.clauses),
                            "missing_information": len(ex.missing_information)}, warnings)

    # ------------------------------------------------------------------ resolution
    def resolve(self, out: ContractExtraction, window: Window, warnings: list[str]) -> ExtractedContract:
        ex = ExtractedContract(title=(out.title or None), contract_type=out.contract_type, missing_information=[m.strip() for m in out.missing_information if m.strip()])
        seen: set[str] = set()
        for p in out.parties:
            key = party_key(p.name)
            if not key or key in seen:
                continue
            seen.add(key)
            ev = resolve_all(p.evidence, window.labels)
            if not any(e.verified for e in ev):
                warnings.append(f"Party '{p.name}' has no verifiable source quote.")
            ex.parties.append(PartyDraft(p.name.strip(), p.role, p.party_type, ev))
        ex.effective_date = self._date(out.effective_date, window, "effective date", warnings)
        ex.expiration_date = self._date(out.expiration_date, window, "expiration date", warnings)
        for name, label in (("initial_term_months", "initial term"), ("renewal_term_months", "renewal term"), ("renewal_notice_days", "renewal notice period"),
                            ("payment_days", "payment period"), ("termination_convenience_notice_days", "termination-for-convenience notice"),
                            ("termination_cure_days", "cure period")):
            setattr(ex, name, self._int(getattr(out, name), window, label, warnings))
        ex.total_value = self._number(out.total_value, window, "total value", warnings)
        ex.auto_renews = self._bool(out.auto_renews, window, "auto-renewal", warnings)
        for name, label in (("payment_terms", "payment terms"), ("currency", "currency"), ("governing_law", "governing law"), ("sla_summary", "service levels")):
            setattr(ex, name, self._text(getattr(out, name), window, label, warnings))
        for c in out.clauses:
            ev = resolve_all(c.evidence, window.labels)
            if not ev:
                warnings.append(f"Clause '{c.heading or c.clause_type.value}' has no evidence and was dropped.")
                continue
            first = next((e for e in ev if e.verified), ev[0])
            ex.clauses.append(ClauseDraft(
                clause_type=c.clause_type, heading=c.heading, section_reference=c.section_reference or first.section_reference, summary=c.summary.strip(),
                evidence=ev, fingerprint=fingerprint("cl", c.clause_type.value, c.section_reference or first.section_reference or "", norm_key(c.heading)),
                text=" … ".join(e.quote for e in ev if e.verified) or first.quote, page_number=first.page_number,
                flags=[] if any(e.verified for e in ev) else ["no_verified_evidence"],
            ))
        return ex

    # -- typed fact resolvers ----------------------------------------------------------------
    def _evidence(self, refs, window: Window) -> list[ResolvedEvidence]:
        return resolve_all(refs, window.labels)

    def _date(self, f: DateFact, window: Window, label: str, warnings: list[str]) -> Fact[date]:
        fact: Fact[date] = Fact(raw_text=f.raw_text, evidence=self._evidence(f.evidence, window))
        if not f.iso_date and not f.raw_text:
            return fact
        if not fact.verified:
            warnings.append(f"Dropped {label}: no verifiable source quote.")
            fact.flags.append("dropped_unverified")
            return fact
        quotes = [e.quote for e in fact.evidence if e.verified]
        parsed_raw = parse_date_text(f.raw_text) if f.raw_text else None
        model_iso: date | None = None
        if f.iso_date:
            try:
                model_iso = date.fromisoformat(f.iso_date)
            except ValueError:
                fact.flags.append("invalid_iso")
        if parsed_raw and parsed_raw.value:
            if f.raw_text and normalise(f.raw_text) not in " ".join(normalise(q) for q in quotes):
                fact.flags.append("raw_text_not_in_quote")
            fact.value = parsed_raw.value
            if model_iso and model_iso != parsed_raw.value:
                fact.flags.append("model_date_disagrees_with_text")
                warnings.append(f"{label.capitalize()}: model returned {model_iso} but the text reads {parsed_raw.value}; text used.")
        elif parsed_raw and parsed_raw.ambiguous:
            fact.flags.append("ambiguous_date")
            warnings.append(f"{label.capitalize()} '{f.raw_text}' is ambiguous (day/month order); left unresolved for human review.")
        elif model_iso and date_supported(model_iso, quotes):
            fact.value = model_iso
        elif model_iso:
            fact.flags.append("date_not_supported_by_quote")
            warnings.append(f"Dropped {label}: the quoted text does not support {model_iso}.")
        return fact

    def _int(self, f: IntFact, window: Window, label: str, warnings: list[str]) -> Fact[int]:
        fact: Fact[int] = Fact(raw_text=f.raw_text, evidence=self._evidence(f.evidence, window))
        if f.value is None:
            return fact
        quotes = [e.quote for e in fact.evidence if e.verified]
        if not quotes:
            warnings.append(f"Dropped {label}: no verifiable source quote.")
            fact.flags.append("dropped_unverified")
            return fact
        if not number_supported(f.value, quotes):
            fact.flags.append("value_not_in_quote")
            warnings.append(f"{label.capitalize()} value {f.value} is not visible in its quote; flagged for review.")
        fact.value = int(f.value)
        return fact

    def _number(self, f: NumberFact, window: Window, label: str, warnings: list[str]) -> Fact[float]:
        fact: Fact[float] = Fact(raw_text=f.raw_text, evidence=self._evidence(f.evidence, window))
        if f.value is None:
            return fact
        quotes = [e.quote for e in fact.evidence if e.verified]
        if not quotes:
            warnings.append(f"Dropped {label}: no verifiable source quote.")
            fact.flags.append("dropped_unverified")
            return fact
        if not number_supported(f.value, quotes):
            fact.flags.append("value_not_in_quote")
        fact.value = float(f.value)
        return fact

    def _bool(self, f: BoolFact, window: Window, label: str, warnings: list[str]) -> Fact[bool]:
        fact: Fact[bool] = Fact(evidence=self._evidence(f.evidence, window))
        if f.value is None:
            return fact
        if not fact.verified:
            warnings.append(f"Dropped {label}: no verifiable source quote.")
            fact.flags.append("dropped_unverified")
            return fact
        fact.value = f.value
        return fact

    def _text(self, f: TextFact, window: Window, label: str, warnings: list[str]) -> Fact[str]:
        fact: Fact[str] = Fact(evidence=self._evidence(f.evidence, window))
        if not f.value:
            return fact
        if not fact.verified:
            warnings.append(f"Dropped {label}: no verifiable source quote.")
            fact.flags.append("dropped_unverified")
            return fact
        fact.value = f.value.strip()
        return fact


_ = Any
