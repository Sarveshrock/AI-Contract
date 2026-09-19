"""Keyword-based clause type classifier used for chunk metadata and retrieval filters.

This is a *hint* for retrieval and UI grouping; extracted clause records use the LLM classification
and are validated against source text.
"""
from __future__ import annotations

import re

from app.models.enums import ClauseType

_KEYWORDS: dict[ClauseType, tuple[str, ...]] = {
    ClauseType.PAYMENT: ("payment", "invoice", "fees", "pay ", "net 30", "net thirty", "late fee", "interest on", "price", "compensation", "remuneration"),
    ClauseType.RENEWAL: ("renew", "auto-renew", "automatically extend", "evergreen", "non-renewal", "notice of non-renewal"),
    ClauseType.TERMINATION: ("terminat", "cancellation", "for convenience", "for cause", "wind-down", "cure period"),
    ClauseType.TERM: ("initial term", "effective date", "term of this agreement", "commencement date", "expire"),
    ClauseType.SLA: ("service level", "uptime", "availability", "service credit", "response time", "resolution time", "sla"),
    ClauseType.CONFIDENTIALITY: ("confidential", "non-disclosure", "proprietary information", "trade secret"),
    ClauseType.LIABILITY: ("limitation of liability", "liable", "liability", "consequential damages", "aggregate liability", "cap on"),
    ClauseType.INDEMNITY: ("indemnif", "hold harmless", "defend"),
    ClauseType.GOVERNING_LAW: ("governing law", "governed by the laws", "jurisdiction", "venue"),
    ClauseType.DISPUTE_RESOLUTION: ("arbitration", "dispute resolution", "mediation", "escalation of disputes"),
    ClauseType.IP: ("intellectual property", "ownership of", "license grant", "work product", "copyright"),
    ClauseType.DATA_PROTECTION: ("personal data", "data protection", "gdpr", "security incident", "data breach", "privacy"),
    ClauseType.FORCE_MAJEURE: ("force majeure", "act of god"),
    ClauseType.ASSIGNMENT: ("assign", "change of control", "subcontract"),
    ClauseType.WARRANTY: ("warrant", "as is", "disclaim"),
    ClauseType.INSURANCE: ("insurance", "certificate of insurance"),
    ClauseType.AUDIT: ("audit", "inspection rights", "books and records"),
    ClauseType.DEFINITIONS: ("definitions", "means ", "shall mean"),
}


def classify_clause(text: str, heading: str | None = None) -> ClauseType:
    """Score keywords in the heading (x3) and body; return OTHER when nothing is convincing."""
    head = (heading or "").lower()
    body = text.lower()
    best, best_score = ClauseType.OTHER, 0.0
    for ctype, words in _KEYWORDS.items():
        score = 0.0
        for w in words:
            if w in head:
                score += 3.0
            hits = len(re.findall(re.escape(w), body))
            score += min(hits, 3)
        if score > best_score:
            best, best_score = ctype, score
    return best if best_score >= 2.0 else ClauseType.OTHER
