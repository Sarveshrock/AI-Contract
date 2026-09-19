"""Obligation Intelligence Agent: who must do what, triggered by what, by when — with evidence and uncertainty."""
from __future__ import annotations

from collections import defaultdict

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.evidence import resolve_all
from app.agents.prompts import OBLIGATION_SYSTEM
from app.agents.state import ObligationDraft
from app.agents.util import fingerprint, match_party, norm_key, primary_evidence, is_party_reference_generic
from app.agents.windows import Window, make_windows
from app.schemas.llm_outputs import ObligationExtraction, ObligationOut
from app.security.tool_policy import Capability

WINDOW_CHARS = 22000


class ObligationIntelligenceAgent(Agent):
    name = "obligation_intelligence"
    title = "Obligation Intelligence"
    description = "Extracts obligations with responsible party, beneficiary, trigger, conditions, frequency, deadline text, evidence and uncertainty."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.LLM_STRUCTURED)
        assert ctx.version_id is not None
        chunks = ctx.data.chunks(ctx.version_id)
        windows = make_windows(chunks, WINDOW_CHARS)
        drafts: list[ObligationDraft] = []
        warnings: list[str] = []
        for i, w in enumerate(windows, start=1):
            if ctx.cancel():
                warnings.append("Cancelled before all passages were analysed.")
                break
            ctx.emit("agent.progress", {"agent": self.name, "message": f"Reading passages {i}/{len(windows)}"})
            out = ctx.llm.structured(
                system=OBLIGATION_SYSTEM,
                user=f"Contract passages (part {i} of {len(windows)}):\n\n{w.text}\n\nList every obligation found in these passages.",
                schema=ObligationExtraction, purpose=f"obligations[{i}/{len(windows)}]",
            )
            for o in out.obligations:
                d = self._resolve(o, w, ctx, warnings)
                if d:
                    drafts.append(d)
        merged = self._merge(drafts)
        self._assign_fingerprints(merged)
        ctx.state.obligations = merged
        unverified = sum(1 for d in merged if not d.verified)
        return AgentResult(AgentStatus.WARNING if warnings or unverified else AgentStatus.OK,
                           {"obligations": len(merged), "windows": len(windows), "unverified": unverified,
                            "with_deadline_text": sum(1 for d in merged if d.deadline_text or d.temporal)}, warnings)

    def _resolve(self, o: ObligationOut, w: Window, ctx: AgentContext, warnings: list[str]) -> ObligationDraft | None:
        if not o.action.strip():
            return None
        ev = resolve_all(o.evidence, w.labels)
        conf = max(0.0, min(1.0, float(o.confidence)))
        d = ObligationDraft(
            title=o.title.strip()[:160] or o.action[:80], description=o.action.strip(), category=o.category,
            responsible=(o.responsible_party or None), beneficiary=(o.beneficiary or None), trigger=o.trigger, conditions=o.conditions,
            frequency_text=o.frequency_text, deadline_text=o.deadline_text, temporal=o.temporal,
            section_reference=o.section_reference or (primary_evidence(ev).section_reference if primary_evidence(ev) else None),
            evidence=ev, confidence=conf, uncertainty=o.uncertainty,
        )
        if not d.verified:
            d.flags.append("no_verified_evidence")
            d.confidence = min(d.confidence, 0.4)
            warnings.append(f"Obligation '{d.title}' has no verifiable source quote; routed to review.")
        return d

    @staticmethod
    def _overlap(a, b) -> float:
        if a.char_start is None or b.char_start is None:
            return 0.0
        inter = min(a.char_end, b.char_end) - max(a.char_start, b.char_start)
        if inter <= 0:
            return 0.0
        return inter / max(1, min(a.char_end - a.char_start, b.char_end - b.char_start))

    def _merge(self, drafts: list[ObligationDraft]) -> list[ObligationDraft]:
        """Collapse duplicates that come from overlapping chunks or repeated windows."""
        kept: list[ObligationDraft] = []
        for d in sorted(drafts, key=lambda x: -x.confidence):
            pe = primary_evidence(d.evidence)
            dup = None
            for k in kept:
                ke = primary_evidence(k.evidence)
                same_party = norm_key(k.responsible) == norm_key(d.responsible)
                if pe and ke and pe.verified and ke.verified and self._overlap(pe, ke) >= 0.5 and same_party:
                    dup = k
                    break
                if norm_key(k.description) == norm_key(d.description):
                    dup = k
                    break
            if dup is None:
                kept.append(d)
            else:
                known = {e.quote for e in dup.evidence}
                dup.evidence.extend(e for e in d.evidence if e.quote not in known)
        kept.sort(key=lambda d: ((primary_evidence(d.evidence).char_start if primary_evidence(d.evidence) and primary_evidence(d.evidence).char_start is not None else 10**9), d.title))
        return kept

    @staticmethod
    def _assign_fingerprints(drafts: list[ObligationDraft]) -> None:
        """Stable ids: section + category + party + ordinal, so a re-run maps onto the same rows."""
        counters: dict[tuple, int] = defaultdict(int)
        for d in drafts:
            base = (d.section_reference or "", d.category.value, norm_key(d.responsible))
            counters[base] += 1
            d.fingerprint = fingerprint("obl", *base, counters[base])


def attach_party_matches(drafts: list[ObligationDraft], parties) -> None:
    for d in drafts:
        for attr, target in (("responsible", "responsible_party_match"), ("beneficiary", "beneficiary_match")):
            name = getattr(d, attr)
            if not name or is_party_reference_generic(name):
                continue
            m = match_party(name, parties)
            setattr(d, target, m.name if m else None)
