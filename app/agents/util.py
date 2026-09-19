"""Small deterministic helpers shared by agents."""
from __future__ import annotations

import hashlib
import re
from datetime import date

from app.agents.evidence import ResolvedEvidence, normalise
from app.agents.state import PartyDraft
from app.models.enums import PartyRole

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|gmbh|plc|co|company|sa|ag|the)\b\.?", re.IGNORECASE)


def fingerprint(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def norm_key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" {number_words(r)}" if r else "")
    return str(n)


def number_supported(value: float | int, quotes: list[str]) -> bool:
    """Is ``value`` written (as digits or words) in any of the quotes?"""
    text = " ".join(normalise(q) for q in quotes)
    if not text:
        return False
    v = int(value) if float(value).is_integer() else value
    digits = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if any(d.replace(",", "") == str(v) for d in digits):
        return True
    return isinstance(v, int) and 0 <= v < 1000 and number_words(v) in text


def date_supported(d: date, quotes: list[str]) -> bool:
    text = " ".join(normalise(q) for q in quotes)
    return str(d.year) in text and bool(text)


def party_key(name: str) -> str:
    return norm_key(_SUFFIX.sub(" ", name))


_ROLE_ALIASES = {
    "customer": PartyRole.CUSTOMER, "client": PartyRole.CUSTOMER, "buyer": PartyRole.CUSTOMER, "subscriber": PartyRole.CUSTOMER,
    "vendor": PartyRole.VENDOR, "provider": PartyRole.VENDOR, "supplier": PartyRole.VENDOR, "seller": PartyRole.VENDOR, "contractor": PartyRole.VENDOR,
    "licensor": PartyRole.LICENSOR, "licensee": PartyRole.LICENSEE, "landlord": PartyRole.LANDLORD, "tenant": PartyRole.TENANT,
    "disclosing party": PartyRole.DISCLOSING, "receiving party": PartyRole.RECEIVING,
}


def match_party(name: str | None, parties: list[PartyDraft]) -> PartyDraft | None:
    """Match an obligation's party reference to an extracted party by name, defined-term role or fuzzy tokens."""
    if not name:
        return None
    key = party_key(name)
    if not key:
        return None
    for p in parties:
        pk = party_key(p.name)
        if key == pk or (len(key) > 3 and (key in pk or pk in key)):
            return p
    role = _ROLE_ALIASES.get(key)
    if role:
        for p in parties:
            if p.role == role:
                return p
    kt = set(key.split())
    for p in parties:
        pt = set(party_key(p.name).split())
        if kt and pt and len(kt & pt) / len(kt | pt) >= 0.6:
            return p
    return None


def is_party_reference_generic(name: str | None) -> bool:
    """'each party', 'both parties', 'either party' refer to all parties and need no single match."""
    return bool(name) and bool(re.match(r"^(each|both|either|the|all)?\s*part(y|ies)\b", (name or "").lower()))


def primary_evidence(evidence: list[ResolvedEvidence]) -> ResolvedEvidence | None:
    return next((e for e in evidence if e.verified), evidence[0] if evidence else None)
