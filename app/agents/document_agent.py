"""Document Intelligence Agent: structure, OCR quality, related documents, embedded-instruction scan."""
from __future__ import annotations

import re

from app.agents.base import Agent, AgentContext, AgentResult, AgentStatus
from app.agents.state import DocumentProfile, FindingDraft
from app.agents.util import fingerprint
from app.core.errors import DocumentProcessingError
from app.models.enums import DocumentRole, FindingType, Severity, SignalCategory
from app.security import prompt_guard
from app.security.tool_policy import Capability

_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], DocumentRole], ...] = (
    (re.compile(r"amended\s+and\s+restated|restated\s+(?:master\s+)?(?:services\s+)?agreement", re.I), DocumentRole.REVISED),
    (re.compile(r"\bamendment\s+(?:no\.?|number|#)?\s*\d*\b", re.I), DocumentRole.AMENDMENT),
    (re.compile(r"\baddendum\b", re.I), DocumentRole.ADDENDUM),
    (re.compile(r"^\s*(?:schedule|exhibit|annex|appendix)\s+\w+", re.I | re.M), DocumentRole.SCHEDULE),
)
_TYPE_PATTERNS = (
    (re.compile(r"non-?disclosure|confidentiality agreement", re.I), "nda"),
    (re.compile(r"master services agreement", re.I), "msa"),
    (re.compile(r"software-as-a-service|saas|subscription agreement", re.I), "saas_subscription"),
    (re.compile(r"statement of work", re.I), "sow"),
    (re.compile(r"\blease\b", re.I), "lease"),
    (re.compile(r"employment agreement", re.I), "employment"),
    (re.compile(r"licen[sc]e agreement", re.I), "license"),
)


class DocumentIntelligenceAgent(Agent):
    name = "document_intelligence"
    title = "Document Intelligence"
    description = "Identifies document role and structure, checks OCR quality, finds related documents, scans for embedded instructions."

    def run(self, ctx: AgentContext) -> AgentResult:
        self.need(ctx, Capability.DATA_READ)
        self.need(ctx, Capability.COMPUTE)
        assert ctx.version_id is not None
        chunks = ctx.data.chunks(ctx.version_id)
        if not chunks:
            raise DocumentProcessingError("no chunks for version", user_message="This version has no indexed text. Re-upload or re-index the document.")
        doc = ctx.data.document_for_version(ctx.version_id)
        head = " ".join(c.text for c in chunks[:2])[:1200]
        role = next((r for rx, r in _ROLE_PATTERNS if rx.search(head[:500])), DocumentRole.ORIGINAL)
        type_hint = next((t for rx, t in _TYPE_PATTERNS if rx.search(head)), None)

        seen: set[str] = set()
        outline: list[dict] = []
        for c in chunks:
            key = c.section_reference or ""
            if key and key not in seen:
                seen.add(key)
                outline.append({"reference": key, "title": c.section_title, "page": c.page_number, "clause_type": c.clause_type})

        meta = (doc.metadata if doc else {}) or {}
        warnings = list(meta.get("warnings", []))
        if doc is not None and doc.ocr_used and doc.ocr_confidence is not None and doc.ocr_confidence < 0.8:
            warnings.append(f"Low OCR confidence ({doc.ocr_confidence:.0%}); extracted values need careful review.")

        signals = []
        seen_sig: set[tuple[str, str]] = set()
        for s in prompt_guard.scan("\n".join(c.text for c in chunks)):
            k = (s.pattern, s.excerpt[:60])
            if k not in seen_sig:
                seen_sig.add(k)
                signals.append({"pattern": s.pattern, "excerpt": s.excerpt})
        profile = DocumentProfile(
            page_count=doc.page_count if doc else max(c.page_number for c in chunks), section_count=len(outline),
            table_count=int(meta.get("tables", 0)), ocr_used=bool(doc and doc.ocr_used), ocr_confidence=doc.ocr_confidence if doc else None,
            document_role=role, type_hint=type_hint, outline=outline, related_documents=list(meta.get("related", [])),
            injection_signals=signals, warnings=warnings,
        )
        ctx.state.profile = profile
        for s in signals[:5]:
            ctx.state.findings.append(FindingDraft(
                finding_type=FindingType.PROMPT_INJECTION, severity=Severity.MEDIUM, category=SignalCategory.BUSINESS_RISK,
                title="Text resembling AI instructions found in the document",
                description=f"The document contains wording that looks like an instruction to an AI system ({s['pattern'].replace('_', ' ')}). "
                            "It was treated strictly as contract text and did not influence the analysis. Review the passage.",
                weight=5.0, fingerprint=fingerprint("inj", s["pattern"], s["excerpt"][:60]), signal="prompt_injection", needs_review=True,
            ))
        status = AgentStatus.WARNING if warnings or signals else AgentStatus.OK
        return AgentResult(status, {"pages": profile.page_count, "sections": len(outline), "role": role.value, "type_hint": type_hint,
                                    "related": len(profile.related_documents), "injection_signals": len(signals)}, warnings)
