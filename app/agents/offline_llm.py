"""Offline analysis: pattern rules that fill the same structured outputs an AI model would.

``RuleBasedLLM`` implements the ``LLMClient`` interface without any network or model. It reads the labelled passages in the
prompt, finds facts with regular expressions and returns *verbatim quotes* for each one, so the normal pipeline still runs:
quotes are verified against the source, dates are computed by the deterministic engine and everything starts as "needs review".

It is deliberately conservative and much less capable than an AI model: it understands common English contract phrasing
(dates, "within thirty (30) days after ...", "shall", renewal and termination wording) and nothing else. Every obligation it
produces carries an uncertainty note saying it came from rules, not AI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import AIUnavailableError
from app.models.enums import ChangeType, ClauseType, ContractType, DeadlineKind, FindingType, ObligationCategory, PartyRole, PartyType, Severity
from app.schemas import llm_outputs as o
from app.tools.temporal import Direction, Frequency, Unit, parse_date_text

_BLOCK = re.compile(r"\[(C\d+)\] section=\"([^\"]*)\" title=\"([^\"]*)\" page=(\d+)\n<<<UNTRUSTED_PASSAGE id=[0-9a-f]+>>>\n(.*?)\n<<<END_UNTRUSTED_PASSAGE id=[0-9a-f]+>>>", re.S)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
DATE = rf"(?:(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})"
NUM = r"(?:[A-Za-z][A-Za-z-]*\s+)?\(?(\d{1,4})\)?"
NUMN = r"(?:[A-Za-z][A-Za-z-]*\s+)?\(?(?P<n>\d{1,4})\)?"
UNIT = r"(business\s+days?|calendar\s+days?|days?|weeks?|months?|years?|hours?)"
_ABBREV = {"inc", "ltd", "llc", "corp", "co", "no", "sec", "st", "mr", "mrs", "dr", "e.g", "i.e", "u.s", "vs", "etc", "s.a", "l.p"}
UNCERTAINTY = "Found by offline pattern rules, not AI. Check the wording against the clause."


@dataclass
class Block:
    label: str
    section: str | None
    title: str | None
    page: int
    text: str


def _blocks(prompt: str) -> list[Block]:
    return [Block(m.group(1), None if m.group(2) == "-" else m.group(2), None if m.group(3) == "-" else m.group(3), int(m.group(4)), m.group(5)) for m in _BLOCK.finditer(prompt)]


def _sentences(text: str) -> list[tuple[int, int]]:
    """Spans of sentences. A boundary is '.' or ';' + space + capital/digit, unless the word before is an abbreviation."""
    spans, start = [], 0
    for m in re.finditer(r"[.;:]\s+(?=[A-Z0-9(])", text):
        before = re.findall(r"[A-Za-z.]+$", text[start:m.start()])
        word = (before[0].lower().strip(".") if before else "")
        if word in _ABBREV or (len(word) == 1 and word.isalpha()) or re.search(r"\d$", text[max(start, m.start() - 3):m.start()]) and re.match(r"\s+\d", text[m.end() - 1:m.end() + 1]):
            continue
        spans.append((start, m.start() + 1))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _strip_num(text: str, a: int, b: int) -> tuple[str, str | None]:
    seg = text[a:b]
    m = re.match(r"\s*(\d+(?:\.\d+)*)\.?\s+", seg)
    if m:
        return seg[m.end():].strip(), m.group(1)
    return seg.strip(), None


def _cut(text: str, limit: int = 300) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _window(text: str, s: int, e: int, before: int = 110, after: int = 70) -> str:
    a, b = max(0, s - before), min(len(text), e + after)
    if a > 0:
        nxt = text.find(" ", a)
        a = nxt + 1 if 0 <= nxt < s else s
    if b < len(text):
        prv = text.rfind(" ", e, b)
        b = prv if prv > e else e
    return _cut(text[a:b].strip())


class _Doc:
    def __init__(self, prompt: str) -> None:
        self.blocks = _blocks(prompt)

    def refs(self, block: Block, quote: str) -> list[o.QuoteRef]:
        return [o.QuoteRef(chunk_label=block.label, quote=quote)] if quote and quote in block.text else []

    def search(self, pattern: str, flags: int = re.I):
        rx = re.compile(pattern, flags)
        for b in self.blocks:
            for m in rx.finditer(b.text):
                yield b, m

    def first(self, pattern: str, flags: int = re.I):
        return next(self.search(pattern, flags), None)

    def sentence_at(self, block: Block, pos: int) -> str:
        for a, b in _sentences(block.text):
            if a <= pos < b or a <= pos <= b:
                seg, _ = _strip_num(block.text, a, b)
                return _cut(seg)
        return _window(block.text, pos, pos)


# ------------------------------------------------------------------------------------------ small helpers
def _empty_date() -> o.DateFact:
    return o.DateFact(iso_date=None, raw_text=None, evidence=[])


def _empty_int() -> o.IntFact:
    return o.IntFact(value=None, raw_text=None, evidence=[])


def _empty_text() -> o.TextFact:
    return o.TextFact(value=None, evidence=[])


def _to_days(n: int, unit: str) -> int | None:
    u = unit.lower()
    if u.startswith("day") or u.startswith("calendar"):
        return n
    if u.startswith("week"):
        return n * 7
    return None


def _date_fact(doc: _Doc, patterns: list[str]) -> o.DateFact:
    for pat in patterns:
        hit = doc.first(pat)
        if hit:
            b, m = hit
            raw = m.group("date")
            parsed = parse_date_text(raw)
            quote = _window(b.text, m.start(), m.end())
            if raw in quote:
                return o.DateFact(iso_date=parsed.value.isoformat() if parsed and parsed.value else None, raw_text=raw, evidence=doc.refs(b, quote))
    return _empty_date()


def _int_fact(doc: _Doc, pattern: str, *, unit_to_months: bool = False, unit_to_days: bool = False, context: str | None = None) -> o.IntFact:
    for b, m in doc.search(pattern):
        if context and not re.search(context, b.text[max(0, m.start() - 160): m.end() + 120], re.I):
            continue
        n = int(m.group("n"))
        unit = (m.groupdict().get("unit") or "").lower()
        if unit_to_months:
            n = n * 12 if unit.startswith("year") else n if unit.startswith("month") else None
        elif unit_to_days:
            n = _to_days(n, unit) if unit else n
        if n is None:
            continue
        return o.IntFact(value=n, raw_text=m.group(0).strip(), evidence=doc.refs(b, _window(b.text, m.start(), m.end())))
    return _empty_int()


def _title_case(s: str) -> str:
    small = {"and", "of", "for", "the", "to", "in", "on", "a", "an"}
    words = s.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


_TYPE_KEYS = [("master services", ContractType.MSA), ("statement of work", ContractType.SOW), ("software-as-a-service", ContractType.SAAS), ("subscription", ContractType.SAAS),
              ("non-disclosure", ContractType.NDA), ("confidentiality agreement", ContractType.NDA), ("employment", ContractType.EMPLOYMENT), ("lease", ContractType.LEASE),
              ("license", ContractType.LICENSE), ("licence", ContractType.LICENSE), ("purchase", ContractType.PURCHASE), ("amendment", ContractType.AMENDMENT),
              ("services agreement", ContractType.SERVICES), ("development", ContractType.SERVICES), ("support agreement", ContractType.SERVICES)]
_ROLE = {"customer": PartyRole.CUSTOMER, "client": PartyRole.CUSTOMER, "buyer": PartyRole.CUSTOMER, "vendor": PartyRole.VENDOR, "supplier": PartyRole.VENDOR, "provider": PartyRole.VENDOR,
         "service provider": PartyRole.VENDOR, "contractor": PartyRole.VENDOR, "consultant": PartyRole.VENDOR, "seller": PartyRole.VENDOR, "licensor": PartyRole.LICENSOR,
         "licensee": PartyRole.LICENSEE, "disclosing party": PartyRole.DISCLOSING, "receiving party": PartyRole.RECEIVING, "landlord": PartyRole.LANDLORD, "tenant": PartyRole.TENANT,
         "company": PartyRole.OTHER, "employer": PartyRole.OTHER, "employee": PartyRole.OTHER, "partner": PartyRole.PARTNER}
_CLAUSE_KEYS: list[tuple[ClauseType, str]] = [
    (ClauseType.RENEWAL, r"\brenew"), (ClauseType.TERMINATION, r"\bterminat"), (ClauseType.PAYMENT, r"\binvoice|\bpayment|\bfees?\b|\bpay\b"),
    (ClauseType.SLA, r"service level|availability|uptime|service credit"), (ClauseType.CONFIDENTIALITY, r"confidential"), (ClauseType.INDEMNITY, r"indemnif"),
    (ClauseType.LIABILITY, r"liabilit"), (ClauseType.GOVERNING_LAW, r"governed by|governing law"), (ClauseType.DISPUTE_RESOLUTION, r"arbitrat|jurisdiction|dispute"),
    (ClauseType.IP, r"intellectual property|copyright|patent"), (ClauseType.DATA_PROTECTION, r"personal data|data protection|security incident|customer data"),
    (ClauseType.FORCE_MAJEURE, r"force majeure"), (ClauseType.ASSIGNMENT, r"\bassign(?:ment)?\b"), (ClauseType.WARRANTY, r"warrant"), (ClauseType.INSURANCE, r"insurance"),
    (ClauseType.AUDIT, r"\baudit"), (ClauseType.TERM, r"\bterm\b|initial term"), (ClauseType.DEFINITIONS, r"\bmeans\b|\bdefinitions?\b"),
]
_CATEGORY_KEYS: list[tuple[ObligationCategory, str]] = [
    (ObligationCategory.CONFIDENTIALITY, r"confidential"), (ObligationCategory.INSURANCE, r"insurance"), (ObligationCategory.AUDIT, r"\baudit"),
    (ObligationCategory.PAYMENT, r"\bpay\b|payment|invoice|\bfees?\b|reimburse"), (ObligationCategory.SERVICE_LEVEL, r"availab|uptime|respond|resolve|service level|incident"),
    (ObligationCategory.REPORTING, r"\breport"), (ObligationCategory.RENEWAL, r"\brenew"), (ObligationCategory.TERMINATION, r"\bterminat"),
    (ObligationCategory.NOTICE, r"\bnotif|\bnotice"), (ObligationCategory.COMPLIANCE, r"comply|complian|personal data|customer data|\bprocess\b|\breturn\b|destroy|\bstore\b"),
    (ObligationCategory.DELIVERY, r"\bdeliver(?:s|y|ed)?\b|\bprovide\b|\bperform|\bcomplete\b|\bbuild\b|\bdesign\b|\brelease\b|\bmaintain\b|\bcorrect\b|\breview\b"),
]
_MODAL = re.compile(r"\b(shall not|shall|must not|must|is required to|are required to|agrees? to|will)\b", re.I)
_SUBJECT = re.compile(r"(?P<s>(?:Each|Either|Both)\s+[Pp]art(?:y|ies)|[Tt]he\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\s*$")
_NOT_OBLIGATION = re.compile(r"shall (?:be|not be)\s+(?:governed|construed|deemed|interpreted|effective|binding|entitled|liable|considered|treated)|shall (?:survive|apply|prevail|constitute|continue)|\bmeans\b", re.I)


def _event_anchor(phrase: str) -> tuple[str, DeadlineKind]:
    p = phrase.lower()
    if "effective date" in p:
        return "effective_date", DeadlineKind.DERIVED
    if "renewal term" in p:
        return "current_term_end", DeadlineKind.DERIVED
    if re.search(r"expir|end of the (?:then-current |current |initial )?term|termination", p):
        return "expiration_date", DeadlineKind.DERIVED
    if "invoice" in p:
        return "invoice_received", DeadlineKind.EVENT_TRIGGERED
    if re.search(r"deliver|accept", p):
        return "delivery_accepted", DeadlineKind.EVENT_TRIGGERED
    slug = re.sub(r"[^a-z0-9]+", "_", re.sub(r"^(?:the|a|an|its|such)\s+", "", p)).strip("_")[:40]
    return (slug or "triggering_event"), DeadlineKind.EVENT_TRIGGERED


def _temporal(sentence: str) -> o.TemporalTermOut | None:
    m = re.search(r"(\w+)\s+\((\d+)(?:st|nd|rd|th)\)\s+Business\s+Day\s+of\s+each\s+(?:calendar\s+)?month|(\d+)(?:st|nd|rd|th)\s+business\s+day\s+of\s+each\s+(?:calendar\s+)?month", sentence, re.I)
    if m:
        nth = int(m.group(2) or m.group(3))
        return o.TemporalTermOut(kind=DeadlineKind.RECURRING, raw_text=m.group(0), explicit_date_text=None, anchor="effective_date", offset=None, unit=None, direction=None,
                                 frequency=Frequency.MONTHLY, nth_business_day=nth, day_of_month=None)
    m = re.search(rf"\b(within|no later than|not later than|at least|not less than|no less than)\s+{NUM}\s+{UNIT}\b(?P<rest>[^.;]*)", sentence, re.I)
    if not m:
        return None
    n, unit_word = int(m.group(2)), m.group(3).lower()
    unit = (Unit.BUSINESS_DAY if "business" in unit_word else Unit.HOUR if unit_word.startswith("hour") else Unit.WEEK if unit_word.startswith("week")
            else Unit.MONTH if unit_word.startswith("month") else Unit.YEAR if unit_word.startswith("year") else Unit.DAY)
    rest = m.group("rest") or ""
    lead = m.group(1).lower()
    if lead in ("at least", "not less than", "no less than") and not re.search(r"\bbefore\b|\bprior to\b", rest, re.I):
        return None
    direction = Direction.BEFORE if (lead in ("at least", "not less than", "no less than") and re.search(r"\bbefore\b|\bprior to\b", rest, re.I)) else Direction.AFTER
    ph = re.search(r"^\s*(?:after|following|of|from|before|prior to)\s+(.+?)\s*(?:,|$)", rest, re.I)
    raw = " ".join(m.group(0).split()).rstrip(",")
    if not ph:
        return o.TemporalTermOut(kind=DeadlineKind.UNRESOLVED, raw_text=raw, explicit_date_text=None, anchor=None, offset=n, unit=unit, direction=direction, frequency=None,
                                 nth_business_day=None, day_of_month=None)
    anchor, kind = _event_anchor(ph.group(1))
    return o.TemporalTermOut(kind=kind, raw_text=raw, explicit_date_text=None, anchor=anchor, offset=n, unit=unit, direction=direction, frequency=None, nth_business_day=None, day_of_month=None)


def _frequency_text(sentence: str) -> str | None:
    m = re.search(r"\b(monthly|weekly|quarterly|annually|each (?:calendar )?month|each (?:calendar )?year|each quarter|per year|once per year)\b", sentence, re.I)
    return m.group(1) if m else None


# ------------------------------------------------------------------------------------------ extractors
def _parties(doc: _Doc) -> list[o.PartyOut]:
    rx = re.compile(r"(?P<name>[A-Z][\w&.,'\- ]{2,70}?)(?:,?\s+(?:a|an)\s+(?P<desc>[^()“”\"]{3,110}?))?\s*\(\s*(?:the\s+)?[“\"](?P<term>[A-Z][A-Za-z ]{1,30})[”\"]\s*\)")
    out, seen = [], set()
    for b in doc.blocks[:6]:
        for m in rx.finditer(b.text):
            term = m.group("term").strip().lower()
            if term not in _ROLE:
                continue
            name = re.split(r"\bbetween\b", m.group("name"))[-1].strip(" ,")
            name = re.sub(r"^(?:and|by)\s+", "", name).strip(" ,")
            if len(name) < 3 or name.lower() in seen:
                continue
            start = m.start("name") + m.group("name").rfind(name)
            quote = _cut(b.text[start:m.end()])
            seen.add(name.lower())
            desc = (m.group("desc") or "").lower()
            out.append(o.PartyOut(name=name, role=_ROLE[term], party_type=PartyType.INDIVIDUAL if re.search(r"individual|resident of", desc) else PartyType.COMPANY, evidence=doc.refs(b, quote)))
    return out[:4]


def extract_contract(prompt: str) -> o.ContractExtraction:
    doc = _Doc(prompt)
    if not doc.blocks:
        raise AIUnavailableError("no passages", user_message="There is no text to analyse.")
    head = doc.blocks[0].text.strip().splitlines()[0].strip() if doc.blocks[0].text.strip() else ""
    title = _title_case(head) if head and len(head) <= 110 and re.search(r"agreement|contract|amendment|addendum", head, re.I) else None
    hay = (head + " " + " ".join((b.title or "") for b in doc.blocks[:3])).lower()
    ctype = next((t for key, t in _TYPE_KEYS if key in hay), ContractType.OTHER if title else ContractType.UNKNOWN)

    effective = _date_fact(doc, [rf"(?P<date>{DATE})\s*\(\s*(?:the\s+)?[“\"]Effective Date[”\"]\s*\)", rf"(?:entered into|made|dated|effective)\s+(?:as of|on)\s+(?P<date>{DATE})",
                                 rf"(?:Effective Date\s+(?:is|shall be)\s+)(?P<date>{DATE})"])
    expiration = _date_fact(doc, [rf"(?:expires?|shall expire|expiring|terminates?|shall terminate|ends?)\s+on\s+(?P<date>{DATE})"])
    term = _int_fact(doc, rf"(?:initial term of|continues for|for a term of|for an initial term of|term of)\s+{NUMN}\s*(?P<unit>months?|years?)", unit_to_months=True)
    if term.value is None:
        term = _int_fact(doc, rf"\b{NUMN}\s*[-\s]?(?P<unit>month|year)s?\s+(?:initial\s+)?term\b", unit_to_months=True)
    term = _rename_group(term)

    auto = doc.first(r"renews?\s+automatically|automatically\s+renews?|auto-?renew|automatic\s+renewal")
    no_auto = doc.first(r"shall\s+not\s+(?:automatically\s+)?renew|does\s+not\s+(?:automatically\s+)?renew")
    if auto:
        auto_fact = o.BoolFact(value=True, evidence=doc.refs(auto[0], _window(auto[0].text, auto[1].start(), auto[1].end())))
    elif no_auto:
        auto_fact = o.BoolFact(value=False, evidence=doc.refs(no_auto[0], _window(no_auto[0].text, no_auto[1].start(), no_auto[1].end())))
    else:
        auto_fact = o.BoolFact(value=None, evidence=[])
    renewal_term = _int_fact(doc, rf"successive\s+(?:renewal\s+)?(?:terms?|periods?)\s+of\s+{NUMN}\s*(?P<unit>months?|years?)", unit_to_months=True)
    notice = _int_fact(doc, rf"at\s+least\s+{NUMN}\s+(?P<unit>days?|weeks?)\s+(?:before|prior\s+to)", unit_to_days=True, context=r"renew|non-renewal")
    pay_days = _int_fact(doc, rf"within\s+{NUMN}\s+(?P<unit>days?)\s+(?:after|of|from)\s+(?:the\s+)?(?:receipt|date|invoice)", unit_to_days=True, context=r"invoice|pay")
    pay_hit = next(((b, m) for b, m in doc.search(rf"\b(?:pay|payable|payment)\b[^.]*?within\s+{NUM}\s+days?") ), None)
    payment_terms = o.TextFact(value=" ".join(doc.sentence_at(pay_hit[0], pay_hit[1].start()).split()), evidence=doc.refs(pay_hit[0], doc.sentence_at(pay_hit[0], pay_hit[1].start()))) if pay_hit else _empty_text()
    conv = _int_fact(doc, rf"(?:for\s+convenience)[^.]*?(?:giving|by|upon)[^.]*?{NUMN}\s+(?P<unit>days?)", unit_to_days=True)
    cure = _int_fact(doc, rf"cure[^.]*?within\s+{NUMN}\s+(?P<unit>days?)", unit_to_days=True)

    cur = doc.first(r"\b(USD|EUR|GBP|CAD|AUD|INR|CHF|JPY)\s?\$?\s?[\d,]+|[$€£]\s?[\d,]+")
    currency, value = _empty_text(), o.NumberFact(value=None, raw_text=None, evidence=[])
    if cur:
        b, m = cur
        code = {"$": "USD", "€": "EUR", "£": "GBP"}.get(m.group(0)[0], (m.group(1) or "USD").upper())
        amt = re.search(r"[\d,]+(?:\.\d+)?", m.group(0))
        quote = _window(b.text, m.start(), m.end())
        currency = o.TextFact(value=code, evidence=doc.refs(b, quote))
        fee = doc.first(rf"(?:fee|price|total|fixed)[^.]*?(?:{code}|[$€£])\s?\$?\s?(?P<amt>[\d,]+(?:\.\d+)?)")
        pick_b, pick_m, raw = (fee[0], fee[1], fee[1].group("amt")) if fee else (b, m, amt.group(0) if amt else "")
        if raw:
            value = o.NumberFact(value=float(raw.replace(",", "")), raw_text=raw, evidence=doc.refs(pick_b, _window(pick_b.text, pick_m.start(), pick_m.end())))
    law = doc.first(r"governed\s+by\s+(?:and\s+construed\s+in\s+accordance\s+with\s+)?the\s+laws?\s+of\s+(?P<law>(?:the\s+)?(?:State\s+of\s+|Commonwealth\s+of\s+)?[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)")
    governing = o.TextFact(value=law[1].group("law").strip(), evidence=doc.refs(law[0], doc.sentence_at(law[0], law[1].start()))) if law else _empty_text()
    sla_hit = next(((b, m) for b, m in doc.search(r"(?:availab|uptime)[^.]*?\d+(?:\.\d+)?\s?%|\d+(?:\.\d+)?\s?%[^.]*?(?:availab|uptime)")), None)
    sla = o.TextFact(value=" ".join(doc.sentence_at(sla_hit[0], sla_hit[1].start()).split())[:220], evidence=doc.refs(sla_hit[0], doc.sentence_at(sla_hit[0], sla_hit[1].start()))) if sla_hit else _empty_text()

    clauses = _clauses(doc)
    kinds = {c.clause_type for c in clauses}
    missing = []
    for ctype_, msg in ((ClauseType.GOVERNING_LAW, "No governing law clause was identified."), (ClauseType.TERMINATION, "No termination clause was identified."),
                        (ClauseType.LIABILITY, "No limitation of liability clause was identified."), (ClauseType.FORCE_MAJEURE, "No force majeure clause was identified."),
                        (ClauseType.CONFIDENTIALITY, "No confidentiality clause was identified.")):
        if ctype_ not in kinds:
            missing.append(msg)
    if not effective.evidence:
        missing.append("No effective date could be read from the text.")
    return o.ContractExtraction(
        title=title, contract_type=ctype, parties=_parties(doc), effective_date=effective, expiration_date=expiration, initial_term_months=term, auto_renews=auto_fact,
        renewal_term_months=renewal_term, renewal_notice_days=notice, payment_terms=payment_terms, payment_days=pay_days, currency=currency, total_value=value, governing_law=governing,
        termination_convenience_notice_days=conv, termination_cure_days=cure, sla_summary=sla, clauses=clauses, missing_information=missing)


def _rename_group(fact: o.IntFact) -> o.IntFact:
    return fact


def _clauses(doc: _Doc) -> list[o.ClauseOut]:
    out: list[o.ClauseOut] = []
    for b in doc.blocks:
        per_type: dict[ClauseType, list[tuple[str, str | None]]] = {}
        for a, e in _sentences(b.text):
            sent, num = _strip_num(b.text, a, e)
            if len(sent) < 25:
                continue
            for ct, pat in _CLAUSE_KEYS:
                if re.search(pat, sent, re.I) and not (ct is ClauseType.DEFINITIONS and len(per_type) > 0):
                    per_type.setdefault(ct, []).append((sent, num))
                    break
        title_type = next((ct for ct, pat in _CLAUSE_KEYS if b.title and re.search(pat, b.title, re.I)), None)
        for ct, sents in per_type.items():
            if title_type and ct is not title_type and len(per_type) > 2 and ct in (ClauseType.PAYMENT, ClauseType.TERM):
                continue
            first, num = sents[0]
            quote = _cut(first)
            heading = b.title if (b.title and ct is (title_type or ct)) else ct.value.replace("_", " ").title()
            sec = num or (b.section if b.section and re.fullmatch(r"\d+(?:\.\d+)*\.?", b.section) else None)
            out.append(o.ClauseOut(clause_type=ct, heading=heading, section_reference=sec, summary=" ".join(_cut(first, 220).split()), evidence=doc.refs(b, quote)))
    seen: set[tuple[ClauseType, str | None]] = set()
    unique = []
    for c in out:
        key = (c.clause_type, c.section_reference)
        if key not in seen and c.evidence:
            seen.add(key)
            unique.append(c)
    return unique[:60]


def extract_obligations(prompt: str) -> o.ObligationExtraction:
    doc = _Doc(prompt)
    party_terms = [m.group("term").strip() for b in doc.blocks[:4] for m in re.finditer(r"[“\"](?:the\s+)?(?P<term>[A-Z][A-Za-z ]{1,30})[”\"]", b.text) if m.group("term").strip().lower() in _ROLE]
    party_terms = list(dict.fromkeys(party_terms))
    out: list[o.ObligationOut] = []
    for b in doc.blocks:
        for a, e in _sentences(b.text):
            sent, num = _strip_num(b.text, a, e)
            if len(sent) < 20 or _NOT_OBLIGATION.search(sent):
                continue
            mm = _MODAL.search(sent)
            if not mm:
                continue
            sm = _SUBJECT.search(sent[: mm.start()].rstrip())
            if not sm:
                continue
            subject = re.sub(r"^[Tt]he\s+", "", sm.group("s")).strip()
            if re.match(r"(?i)this|agreement|section|nothing|any|no|all|such|it|there", subject.split()[0]):
                continue
            negative = mm.group(1).lower().endswith("not")
            after = sent[mm.end():].strip()
            phrase = re.split(r"\s+(?:within|no later than|not later than|at least|by|on or before|before|after|upon|if|unless|provided that|during|throughout)\b|;", after, maxsplit=1)[0]
            first_part = phrase.split(",", 1)[0]
            words = (first_part if len(first_part.split()) >= 3 else phrase).split()[:9]
            while words and words[-1].lower().strip(",") in {"of", "to", "in", "a", "an", "the", "and", "or", "at", "on", "with", "for", "from", "any", "each", "its", "their", "without", "than", "as"}:
                words.pop()
            if not words:
                continue
            title = ("Do not " if negative else "") + " ".join(words)
            title = title[0].upper() + title[1:]
            cat = next((c for c, pat in _CATEGORY_KEYS if re.search(pat, sent, re.I)), ObligationCategory.OTHER)
            resp = subject
            ben = None
            if party_terms and resp in party_terms and len(party_terms) > 1:
                ben = next((t for t in party_terms if t != resp), None)
            elif re.match(r"(?i)each party|either party|both part", resp):
                ben = "the other party"
            temporal = _temporal(sent)
            trig = None
            if temporal and temporal.kind is DeadlineKind.EVENT_TRIGGERED:
                ph = re.search(r"(?:after|following|of|from|upon)\s+(.{3,70}?)(?:[,;.]|$)", sent[mm.end():], re.I)
                trig = ph.group(1).strip() if ph else None
            cond = None
            cm = re.search(r"\b(?:if|unless|provided that|subject to)\s+(.{5,120}?)(?:[,;.]|$)", sent, re.I)
            if cm:
                cond = cm.group(0).strip()
            quote = _cut(sent)
            ev = doc.refs(b, quote)
            if not ev:
                continue
            conf = 0.7 + (0.05 if temporal else 0.0) - (0.07 if re.match(r"(?i)each|either|both", resp) else 0.0)
            out.append(o.ObligationOut(title=title[:120], action=_cut(sent, 400), category=cat, responsible_party=resp, beneficiary=ben, trigger=trig, conditions=cond,
                                       frequency_text=_frequency_text(sent), deadline_text=temporal.raw_text if temporal else None, temporal=temporal,
                                       section_reference=num or (b.section if b.section and re.fullmatch(r"\d+(?:\.\d+)*\.?", b.section) else None), evidence=ev, confidence=round(min(conf, 0.85), 2), uncertainty=UNCERTAINTY))
    return o.ObligationExtraction(obligations=out)


def extract_amendment(prompt: str) -> o.AmendmentExtraction:
    doc = _Doc(prompt)
    head = " ".join(b.text[:400] for b in doc.blocks[:2])
    is_amend = bool(re.search(r"\bamendment\b|\baddendum\b|hereby\s+amend", head, re.I))
    base = doc.first(rf"(?:agreement|contract)[^.]{{0,80}}?(?:dated|entered into)\s+(?:as of\s+)?(?P<date>{DATE})")
    eff = _date_fact(doc, [rf"(?P<date>{DATE})\s*\(\s*(?:the\s+)?[“\"](?:Amendment\s+)?Effective Date[”\"]\s*\)", rf"(?:entered into|made|effective)\s+(?:as of|on)\s+(?P<date>{DATE})"])
    ins: list[o.AmendmentInstructionOut] = []
    for b in doc.blocks:
        for a, e in _sentences(b.text):
            sent, num = _strip_num(b.text, a, e)
            sm = re.search(r"\bSection\s+(\d+(?:\.\d+)*)", sent)
            if not sm or not re.search(r"amended|replaced|deleted|removed|added|restated|substituted|modified", sent, re.I):
                continue
            low = sent.lower()
            action = ChangeType.ADDED if re.search(r"new section|is added|are added|is inserted", low) else ChangeType.REMOVED if re.search(r"deleted|removed", low) else ChangeType.MODIFIED
            term = ("payment_terms" if re.search(r"pay|invoice", low) else "renewal_notice" if "renew" in low and "notice" in low else "renewal_term" if "renew" in low else
                    "termination_notice" if "terminat" in low else "governing_law" if "governing" in low or "governed" in low else "liability" if "liabilit" in low else
                    "effective_date" if "effective date" in low else "expiration_date" if "expir" in low else "other")
            rep = re.search(r"[“\"]([^”\"]{2,80})[”\"]\s+(?:is|are|shall be)\s+(?:hereby\s+)?(?:replaced|substituted)\s+with\s+[“\"]([^”\"]{2,80})[”\"]", sent)
            n = re.search(r"\((\d{1,4})\)", rep.group(2) if rep else sent)
            ev = doc.refs(b, _cut(sent))
            if ev:
                ins.append(o.AmendmentInstructionOut(target_section=sm.group(1), action=action, affected_term=term, summary=_cut(sent, 200), old_value_text=rep.group(1) if rep else None,
                                                     new_value_text=rep.group(2) if rep else None, new_int_value=int(n.group(1)) if n else None, evidence=ev))
    return o.AmendmentExtraction(is_amendment=is_amend and bool(ins) or is_amend, amends_agreement_date_text=base[1].group("date") if base else None, amendment_effective_date=eff, instructions=ins)


_RISK_RULES: list[tuple[re.Pattern, FindingType, str, str, Severity]] = [
    (re.compile(r"may\s+(?:change|modify|increase|amend)[^.]{0,60}(?:fees?|price|pricing|rates?)[^.]{0,60}(?:at any time|unilateral|sole discretion|by notice)|(?:fees?|prices?)[^.]{0,60}may be (?:changed|increased)[^.]{0,40}at any time", re.I),
     FindingType.RISK_SIGNAL, "Fees can be changed at any time", "One party may change fees with no stated notice period or cap.", Severity.HIGH),
    (re.compile(r"without\s+limit|unlimited\s+liabilit|uncapped|liabilit[^.]{0,40}not\s+(?:be\s+)?(?:limited|capped)", re.I), FindingType.RISK_SIGNAL, "Liability or indemnity without a cap",
     "A liability or indemnity is stated without any limit, which may create open-ended exposure.", Severity.HIGH),
    (re.compile(r"sole\s+discretion|absolute\s+discretion", re.I), FindingType.RISK_SIGNAL, "Decision left to one party's sole discretion", "A right or decision is left entirely to one party's discretion.", Severity.MEDIUM),
    (re.compile(r"\b(?:promptly|in a timely manner|as soon as (?:reasonably )?practicable|reasonable efforts|from time to time)\b", re.I), FindingType.AMBIGUITY, "Vague timing or effort standard",
     "The wording has no measurable deadline or standard, so it is hard to enforce or track.", Severity.MEDIUM),
    (re.compile(r"\b(?:perpetual|irrevocable)\b", re.I), FindingType.RISK_SIGNAL, "Perpetual or irrevocable right", "A right is granted without an end date or way to withdraw it.", Severity.MEDIUM),
    (re.compile(r"automatically\s+renews?[^.]{0,200}?(?:ninety|one hundred|\(9\d\)|\(1\d\d\))", re.I), FindingType.RISK_SIGNAL, "Long non-renewal notice period",
     "A long notice period is required to stop automatic renewal, so the window can be missed.", Severity.LOW),
]


def extract_risks(prompt: str) -> o.RiskSignals:
    doc = _Doc(prompt)
    signals, seen = [], set()
    for b in doc.blocks:
        for a, e in _sentences(b.text):
            sent, _num = _strip_num(b.text, a, e)
            for rx, ftype, title, desc, sev in _RISK_RULES:
                if rx.search(sent) and (title, sent[:40]) not in seen:
                    ev = doc.refs(b, _cut(sent))
                    if ev:
                        seen.add((title, sent[:40]))
                        signals.append(o.RiskSignalOut(signal_type=ftype, title=title, description=desc + " Found by offline rules; a person should review it.", severity=sev, evidence=ev, confidence=0.6))
    return o.RiskSignals(signals=signals[:12])


class RuleBasedLLM:
    """Offline stand-in for an AI model. It cannot chat; the Copilot answers from records and passage search instead."""

    model = "offline-rules"
    available = True
    offline_rules = True

    def __init__(self, note: str | None = None) -> None:
        self.note = note

    def structured(self, *, system: str, user: str, schema: type, purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> Any:
        name = schema.__name__
        if name == "ContractExtraction":
            return extract_contract(user)
        if name == "ObligationExtraction":
            return extract_obligations(user)
        if name == "AmendmentExtraction":
            return extract_amendment(user)
        if name == "RiskSignals":
            return extract_risks(user)
        if name == "ChangeNarratives":
            ids = re.findall(r"change_id=(\d+) type=(\w+) section=(\S+)", user)
            return o.ChangeNarratives(items=[o.ChangeNarrative(change_id=i, summary=f"Section {sec} was {t}.", materiality=Severity.MEDIUM) for i, t, sec in ids])
        raise AIUnavailableError(f"offline rules cannot answer {name}", user_message="Offline analysis cannot write free-form answers. Passages matching the question are shown instead.")
