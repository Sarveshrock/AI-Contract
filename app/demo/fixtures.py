"""Curated extraction fixtures for the bundled sample contracts.

``FixtureLLM`` implements the ``LLMClient`` interface with *hand-authored* answers for the five sample
documents. It exists only for **demo seeding and tests**; it is never attached to the running app as a
substitute for a real model. The whole downstream pipeline (evidence verification, deterministic
deadline engine, QA, risk rules, persistence) runs for real on these answers.

One fixture item is deliberately unsupported (a fabricated quote in the SaaS contract) so the demo
shows the QA agent catching an unverifiable claim.
"""
from __future__ import annotations

import re
from typing import Any

from app.agents.evidence import normalise
from app.models.enums import (
    ChangeType, ClauseType, ContractType, DeadlineKind, FindingType, ObligationCategory, PartyRole, PartyType, Severity,
)
from app.schemas import llm_outputs as o
from app.tools.temporal import Direction, Frequency, Unit

_BLOCK = re.compile(r"\[(C\d+)\][^\n]*\n<<<UNTRUSTED_PASSAGE id=[0-9a-f]+>>>\n(.*?)\n<<<END_UNTRUSTED_PASSAGE id=[0-9a-f]+>>>", re.S)


class _Labels:
    def __init__(self, prompt: str) -> None:
        self.blocks = [(m.group(1), normalise(m.group(2))) for m in _BLOCK.finditer(prompt)]

    def find(self, quote: str | None) -> str | None:
        if not quote:
            return None
        nq = normalise(quote)
        return next((label for label, text in self.blocks if nq in text), None)

    def refs(self, *quotes: str) -> list[o.QuoteRef]:
        out = []
        for q in quotes:
            label = self.find(q)
            if label:
                out.append(o.QuoteRef(chunk_label=label, quote=q))
        return out


def _date(lab: _Labels, iso: str | None, raw: str | None, *quotes: str) -> o.DateFact:
    ev = lab.refs(*quotes)
    return o.DateFact(iso_date=iso if ev else None, raw_text=raw if ev else None, evidence=ev)


def _int(lab: _Labels, v: int | None, raw: str | None, *quotes: str) -> o.IntFact:
    ev = lab.refs(*quotes)
    return o.IntFact(value=v if ev else None, raw_text=raw if ev else None, evidence=ev)


def _text(lab: _Labels, v: str | None, *quotes: str) -> o.TextFact:
    ev = lab.refs(*quotes)
    return o.TextFact(value=v if ev else None, evidence=ev)


def _bool(lab: _Labels, v: bool | None, *quotes: str) -> o.BoolFact:
    ev = lab.refs(*quotes)
    return o.BoolFact(value=v if ev else None, evidence=ev)


def _num(lab: _Labels, v: float | None, raw: str | None, *quotes: str) -> o.NumberFact:
    ev = lab.refs(*quotes)
    return o.NumberFact(value=v if ev else None, raw_text=raw if ev else None, evidence=ev)


def _term(kind: DeadlineKind, raw: str | None, *, anchor=None, offset=None, unit=None, direction=Direction.AFTER, freq=None, nth=None, dom=None, explicit=None) -> o.TemporalTermOut:
    return o.TemporalTermOut(kind=kind, raw_text=raw, explicit_date_text=explicit, anchor=anchor, offset=offset, unit=unit, direction=direction,
                             frequency=freq, nth_business_day=nth, day_of_month=dom)


def _ob(lab: _Labels, title: str, action: str, cat: ObligationCategory, resp: str | None, ben: str | None, quote: str, section: str, *, term=None, trigger=None,
        cond=None, freq=None, deadline=None, conf=0.92, unc=None, extra_quotes: tuple[str, ...] = (), force: bool = False) -> o.ObligationOut | None:
    ev = lab.refs(quote, *extra_quotes)
    if not ev and not force:
        return None
    if not ev and force:  # deliberately unsupported item (demo of the QA agent)
        ev = [o.QuoteRef(chunk_label="C1", quote=quote)]
    return o.ObligationOut(title=title, action=action, category=cat, responsible_party=resp, beneficiary=ben, trigger=trigger, conditions=cond, frequency_text=freq,
                           deadline_text=deadline, temporal=term, section_reference=section, evidence=ev, confidence=conf, uncertainty=unc)


CU, VE = "Customer", "Vendor"
K = DeadlineKind


# =============================================================================================
# MSA (v1 and restated v2)
# =============================================================================================
def _msa_extraction(lab: _Labels, v2: bool) -> o.ContractExtraction:
    pay = "Customer shall pay each undisputed invoice within " + ("forty-five (45) days" if v2 else "thirty (30) days") + " after receipt of the invoice"
    notice = "unless either party gives the other written notice of non-renewal at least " + ("sixty (60) days" if v2 else "ninety (90) days") + " before the end of the then-current term"
    conv = "Customer may terminate this Agreement for convenience by giving Vendor " + ("ninety (90) days" if v2 else "sixty (60) days") + "' prior written notice"
    title_q = "This Amended and Restated Master Services Agreement (the" if v2 else "This Master Services Agreement (the"
    clauses = [
        ("2.1", ClauseType.TERM, "Initial Term", "The agreement runs for 24 months from the Effective Date unless terminated earlier.", "continues for twenty-four (24) months (the \"Initial Term\")"),
        ("2.2", ClauseType.RENEWAL, "Renewal", "Renews automatically for 12-month terms unless either party gives non-renewal notice.", notice),
        ("3.2", ClauseType.PAYMENT, "Invoicing and Payment", "Monthly invoicing in arrears; undisputed invoices payable after receipt.", pay),
        ("4.1", ClauseType.SLA, "Availability", "Vendor must maintain at least 99.9% monthly uptime.", "Vendor shall maintain Monthly Uptime of at least 99.9% for the production Services"),
        ("4.2", ClauseType.SLA, "Service Credits", "Service credits apply per 0.1% uptime shortfall, capped at 30% of monthly fees.", "Customer is entitled to a service credit equal to five percent (5%) of the monthly fees for each 0.1% shortfall"),
        ("5.1", ClauseType.DATA_PROTECTION, "Security Incidents", "Vendor must notify Customer of security incidents within 72 hours of awareness.", "Vendor shall notify Customer in writing within seventy-two (72) hours after becoming aware of a Security Incident affecting Customer Data"),
        ("6.2", ClauseType.CONFIDENTIALITY, "Survival", "Confidentiality obligations survive for five years.", "These confidentiality obligations survive for five (5) years after termination or expiration of this Agreement"),
        ("7.1", ClauseType.INDEMNITY, "Indemnity", "Vendor indemnifies Customer for third-party IP infringement claims.", "Vendor shall defend and indemnify Customer against third-party claims alleging that the Services infringe intellectual property rights"),
        ("7.2", ClauseType.LIABILITY, "Limitation of Liability", "Liability is capped at fees paid or payable in the prior 12 months, with carve-outs.", "each party's aggregate liability is limited to the fees paid or payable in the twelve (12) months preceding the claim"),
        ("8.1", ClauseType.TERMINATION, "Termination for Convenience", "Customer may terminate for convenience on prior written notice.", conv),
        ("8.2", ClauseType.TERMINATION, "Termination for Cause", "Either party may terminate for uncured material breach after 30 days.", "fails to cure the breach within thirty (30) days after written notice of the breach"),
        ("9.1", ClauseType.INSURANCE, "Insurance", "Vendor must maintain CGL insurance of at least USD 2,000,000.", "Vendor shall maintain commercial general liability insurance of at least USD 2,000,000"),
        ("9.2", ClauseType.AUDIT, "Audit", "Customer may audit annually on 15 Business Days' notice.", "Customer may audit Vendor's compliance with this Agreement once per year upon fifteen (15) Business Days' prior written notice"),
        ("10.1", ClauseType.GOVERNING_LAW, "Governing Law", "Delaware law governs.", "This Agreement is governed by the laws of the State of Delaware"),
        ("10.2", ClauseType.ASSIGNMENT, "Assignment", "Assignment requires written consent.", "Neither party may assign this Agreement without the other party's prior written consent"),
    ]
    if v2:
        clauses.insert(6, ("5.3", ClauseType.DATA_PROTECTION, "Data Residency", "Customer Data may be stored only in US data centers.", "Vendor shall store Customer Data only in data centers located in the United States"))
    clause_out = []
    for ref, ct, heading, summary, quote in clauses:
        ev = lab.refs(quote)
        if ev:
            clause_out.append(o.ClauseOut(clause_type=ct, heading=heading, section_reference=ref, summary=summary, evidence=ev))
    return o.ContractExtraction(
        title="Amended and Restated Master Services Agreement" if v2 else "Master Services Agreement", contract_type=ContractType.MSA,
        parties=[
            o.PartyOut(name="Acme Manufacturing Inc.", role=PartyRole.CUSTOMER, party_type=PartyType.COMPANY, evidence=lab.refs('Acme Manufacturing Inc., a Delaware corporation ("Customer")')),
            o.PartyOut(name="Northwind Cloud Systems LLC", role=PartyRole.VENDOR, party_type=PartyType.COMPANY, evidence=lab.refs('Northwind Cloud Systems LLC, a Texas limited liability company ("Vendor")')),
        ],
        effective_date=_date(lab, "2025-01-15", "January 15, 2025", 'is entered into as of January 15, 2025 (the "Effective Date")'),
        expiration_date=_date(lab, None, None),
        initial_term_months=_int(lab, 24, "twenty-four (24) months", 'continues for twenty-four (24) months (the "Initial Term")'),
        auto_renews=_bool(lab, True, 'this Agreement automatically renews for successive twelve (12) month periods (each a "Renewal Term")'),
        renewal_term_months=_int(lab, 12, "twelve (12) month", 'this Agreement automatically renews for successive twelve (12) month periods (each a "Renewal Term")'),
        renewal_notice_days=_int(lab, 60 if v2 else 90, "sixty (60) days" if v2 else "ninety (90) days", notice),
        payment_terms=_text(lab, "Invoiced monthly in arrears; undisputed invoices payable within " + ("45" if v2 else "30") + " days after receipt", pay),
        payment_days=_int(lab, 45 if v2 else 30, "forty-five (45) days" if v2 else "thirty (30) days", pay),
        currency=_text(lab, "USD", "The initial annual subscription fee is USD 240,000"),
        total_value=_num(lab, 240000.0, "USD 240,000", "The initial annual subscription fee is USD 240,000"),
        governing_law=_text(lab, "State of Delaware", "This Agreement is governed by the laws of the State of Delaware"),
        termination_convenience_notice_days=_int(lab, 90 if v2 else 60, "ninety (90) days" if v2 else "sixty (60) days", conv),
        termination_cure_days=_int(lab, 30, "thirty (30) days", "fails to cure the breach within thirty (30) days after written notice of the breach"),
        sla_summary=_text(lab, "99.9% monthly uptime with service credits of 5% per 0.1% shortfall, capped at 30% of monthly fees", "Vendor shall maintain Monthly Uptime of at least 99.9% for the production Services"),
        clauses=clause_out, missing_information=["No force majeure clause was identified."],
    ) if lab.refs(title_q) else o.ContractExtraction(
        title=None, contract_type=ContractType.UNKNOWN, parties=[], effective_date=_date(lab, None, None), expiration_date=_date(lab, None, None),
        initial_term_months=_int(lab, None, None), auto_renews=_bool(lab, None), renewal_term_months=_int(lab, None, None), renewal_notice_days=_int(lab, None, None),
        payment_terms=_text(lab, None), payment_days=_int(lab, None, None), currency=_text(lab, None), total_value=_num(lab, None, None), governing_law=_text(lab, None),
        termination_convenience_notice_days=_int(lab, None, None), termination_cure_days=_int(lab, None, None), sla_summary=_text(lab, None), clauses=[], missing_information=[])


def _msa_obligations(lab: _Labels, v2: bool) -> list[o.ObligationOut | None]:
    pay = "Customer shall pay each undisputed invoice within " + ("forty-five (45) days" if v2 else "thirty (30) days") + " after receipt of the invoice"
    n = 45 if v2 else 30
    notice_raw = "within " + ("forty-five (45) days" if v2 else "thirty (30) days") + " after receipt of the invoice"
    obs = [
        _ob(lab, "Provide contracted services", "Provide the cloud hosting, monitoring and support services described in each Statement of Work.", ObligationCategory.DELIVERY, VE, CU,
            "Vendor shall provide the cloud hosting, monitoring and support services described in each Statement of Work", "1.1"),
        _ob(lab, "Pay undisputed invoices", "Pay each undisputed invoice after receipt.", ObligationCategory.PAYMENT, CU, VE, pay, "3.2",
            term=_term(K.EVENT_TRIGGERED, notice_raw, anchor="invoice_received", offset=n, unit=Unit.DAY), trigger="Receipt of an invoice", deadline=notice_raw),
        _ob(lab, "Invoice Customer monthly", "Invoice Customer monthly in arrears.", ObligationCategory.PAYMENT, VE, CU, "Vendor shall invoice Customer monthly in arrears", "3.2",
            term=_term(K.RECURRING, "monthly in arrears", freq=Frequency.MONTHLY, anchor="effective_date"), freq="monthly", unc="The specific invoicing day of the month is not stated."),
        _ob(lab, "Maintain 99.9% uptime", "Maintain Monthly Uptime of at least 99.9% for the production Services.", ObligationCategory.SERVICE_LEVEL, VE, CU,
            "Vendor shall maintain Monthly Uptime of at least 99.9% for the production Services", "4.1", freq="monthly"),
        _ob(lab, "Deliver monthly performance report", "Deliver a written service performance report on or before the fifth Business Day of each calendar month.", ObligationCategory.REPORTING, VE, CU,
            "Vendor shall deliver a written service performance report to Customer on or before the fifth (5th) Business Day of each calendar month", "4.3",
            term=_term(K.RECURRING, "on or before the fifth (5th) Business Day of each calendar month", freq=Frequency.MONTHLY, nth=5, anchor="effective_date"), freq="monthly"),
        _ob(lab, "Notify security incidents", "Notify Customer in writing of a Security Incident affecting Customer Data.", ObligationCategory.COMPLIANCE, VE, CU,
            "Vendor shall notify Customer in writing within seventy-two (72) hours after becoming aware of a Security Incident affecting Customer Data", "5.1",
            term=_term(K.EVENT_TRIGGERED, "within seventy-two (72) hours after becoming aware of a Security Incident", anchor="security_incident_awareness", offset=72, unit=Unit.HOUR),
            trigger="Vendor becomes aware of a Security Incident"),
        _ob(lab, "Return or delete Customer Data", "Return or delete all Customer Data at Customer's written request within 30 days after expiration or termination.", ObligationCategory.COMPLIANCE, VE, CU,
            "Vendor shall return or delete all Customer Data at Customer's written request", "5.2",
            term=_term(K.DERIVED, "Within thirty (30) days after expiration or termination of this Agreement", anchor="expiration_date", offset=30, unit=Unit.DAY),
            unc="The period runs from expiration or termination; only the expiration anchor could be calculated. An earlier termination would change the date.", conf=0.85),
        _ob(lab, "Protect Confidential Information", "Hold the other party's Confidential Information in confidence and use it only to perform the Agreement.", ObligationCategory.CONFIDENTIALITY, "Each party", "Each party",
            "Each party shall hold the other party's Confidential Information in confidence and use it only to perform this Agreement", "6.1"),
        _ob(lab, "Indemnify Customer for IP claims", "Defend and indemnify Customer against third-party IP infringement claims.", ObligationCategory.COMPLIANCE, VE, CU,
            "Vendor shall defend and indemnify Customer against third-party claims alleging that the Services infringe intellectual property rights", "7.1"),
        _ob(lab, "Maintain liability insurance", "Maintain commercial general liability insurance of at least USD 2,000,000.", ObligationCategory.INSURANCE, VE, CU,
            "Vendor shall maintain commercial general liability insurance of at least USD 2,000,000", "9.1"),
        _ob(lab, "Deliver certificate of insurance", "Deliver a certificate of insurance to Customer within ten days after the Effective Date.", ObligationCategory.INSURANCE, VE, CU,
            "shall deliver a certificate of insurance to Customer within ten (10) days after the Effective Date", "9.1",
            term=_term(K.DERIVED, "within ten (10) days after the Effective Date", anchor="effective_date", offset=10, unit=Unit.DAY)),
        _ob(lab, "Give notice of fee increase", "If fees increase for a Renewal Term, give written notice at least 60 days before that Renewal Term starts.", ObligationCategory.NOTICE, VE, CU,
            "by giving Customer written notice at least sixty (60) days before the start of that Renewal Term", "3.4", cond="Only if Vendor increases fees.",
            term=_term(K.DERIVED, "at least sixty (60) days before the start of that Renewal Term", anchor="current_term_end", offset=60, unit=Unit.DAY, direction=Direction.BEFORE),
            unc="Applies only when Vendor chooses to raise fees."),
    ]
    if v2:
        obs.append(_ob(lab, "Store Customer Data in the United States", "Store Customer Data only in data centers located in the United States.", ObligationCategory.COMPLIANCE, VE, CU,
                       "Vendor shall store Customer Data only in data centers located in the United States", "5.3"))
    return obs


# =============================================================================================
# SaaS subscription
# =============================================================================================
_SAAS_CLAUSES = [
    ("2.1", ClauseType.TERM, "Subscription Term", "Initial term of twelve months.", "The initial term is twelve (12) months from the Effective Date"),
    ("2.2", ClauseType.RENEWAL, "Auto-Renewal", "Renews automatically for 12-month periods unless Customer gives 30 days' notice.", "unless Customer gives written notice of non-renewal at least thirty (30) days before the end of the then-current term"),
    ("3.2", ClauseType.PAYMENT, "Payment", "Invoices payable within 15 days of invoice date.", "Customer shall pay each invoice within fifteen (15) days after the invoice date"),
    ("3.3", ClauseType.PAYMENT, "Price Changes", "Provider may change the fee at any time by notice.", "Provider may change the subscription fee at any time by notice to Customer"),
    ("4.1", ClauseType.SLA, "Availability", "Best-efforts 99.5% monthly availability.", "Provider will use reasonable endeavours to make the Platform available 99.5% of the time each month"),
    ("5.1", ClauseType.LIABILITY, "Liability Cap", "Provider liability capped at 2x trailing 12-month fees.", "Provider's total liability is limited to two times (2x) the fees paid in the twelve (12) months before the claim"),
    ("5.2", ClauseType.INDEMNITY, "Customer Indemnity", "Customer indemnifies Provider for all losses from use of the Platform, without limit.", "Customer shall indemnify Provider against all losses arising from Customer's use of the Platform, without limit"),
    ("6.1", ClauseType.CONFIDENTIALITY, "Confidentiality", "Confidentiality during the term and three years after.", "Each party shall keep the other party's Confidential Information confidential during the term and for three (3) years afterwards"),
    ("7.1", ClauseType.GOVERNING_LAW, "Governing Law", "English law governs.", "This Agreement is governed by the laws of England and Wales"),
    ("7.2", ClauseType.ASSIGNMENT, "Assignment", "Provider may assign to an affiliate without notice.", "Provider may assign this Agreement to an affiliate without notice to Customer"),
]


def _saas_extraction(lab: _Labels) -> o.ContractExtraction:
    return o.ContractExtraction(
        title="SaaS Subscription Agreement (Helios Insights)", contract_type=ContractType.SAAS,
        parties=[
            o.PartyOut(name="Helios Analytics Inc.", role=PartyRole.VENDOR, party_type=PartyType.COMPANY, evidence=lab.refs('Helios Analytics Inc., a California corporation ("Provider")')),
            o.PartyOut(name="Contoso Retail Ltd.", role=PartyRole.CUSTOMER, party_type=PartyType.COMPANY, evidence=lab.refs('Contoso Retail Ltd., a company registered in England and Wales ("Customer")')),
        ],
        effective_date=_date(lab, "2025-03-01", "March 1, 2025", 'is made on March 1, 2025 (the "Effective Date")'),
        expiration_date=_date(lab, None, None),
        initial_term_months=_int(lab, 12, "twelve (12) months", "The initial term is twelve (12) months from the Effective Date"),
        auto_renews=_bool(lab, True, "The subscription renews automatically for successive periods of twelve (12) months"),
        renewal_term_months=_int(lab, 12, "twelve (12) months", "The subscription renews automatically for successive periods of twelve (12) months"),
        renewal_notice_days=_int(lab, 30, "thirty (30) days", "unless Customer gives written notice of non-renewal at least thirty (30) days before the end of the then-current term"),
        payment_terms=_text(lab, "Annual fee invoiced in advance; each invoice payable within 15 days of the invoice date", "Customer shall pay each invoice within fifteen (15) days after the invoice date"),
        payment_days=_int(lab, 15, "fifteen (15) days", "Customer shall pay each invoice within fifteen (15) days after the invoice date"),
        currency=_text(lab, "USD", "The annual subscription fee is USD 84,000"),
        total_value=_num(lab, 84000.0, "USD 84,000", "The annual subscription fee is USD 84,000"),
        governing_law=_text(lab, "England and Wales", "This Agreement is governed by the laws of England and Wales"),
        termination_convenience_notice_days=_int(lab, None, None), termination_cure_days=_int(lab, None, None),
        sla_summary=_text(lab, "Reasonable endeavours to provide 99.5% monthly availability", "Provider will use reasonable endeavours to make the Platform available 99.5% of the time each month"),
        clauses=[o.ClauseOut(clause_type=c[1], heading=c[2], section_reference=c[0], summary=c[3], evidence=lab.refs(c[4])) for c in _SAAS_CLAUSES if lab.refs(c[4])],
        missing_information=["No termination clause was identified.", "No data-protection or security-incident clause was identified."],
    )


def _saas_obligations(lab: _Labels) -> list[o.ObligationOut | None]:
    return [
        _ob(lab, "Complete customer onboarding", "Complete onboarding of Customer within ten Business Days after the Effective Date.", ObligationCategory.DELIVERY, "Provider", "Customer",
            "Provider shall complete onboarding of Customer within ten (10) Business Days after the Effective Date", "1.2",
            term=_term(K.DERIVED, "within ten (10) Business Days after the Effective Date", anchor="effective_date", offset=10, unit=Unit.BUSINESS_DAY)),
        _ob(lab, "Accept or reject implementation deliverables", "Deliver written acceptance or rejection of the Implementation Deliverables within five Business Days after delivery.", ObligationCategory.DELIVERY, CU, "Provider",
            "Customer shall deliver written acceptance or rejection of the Implementation Deliverables within five (5) Business Days after delivery of the Implementation Deliverables", "1.3",
            term=_term(K.EVENT_TRIGGERED, "within five (5) Business Days after delivery of the Implementation Deliverables", anchor="delivery_accepted", offset=5, unit=Unit.BUSINESS_DAY)),
        _ob(lab, "Pay subscription invoices", "Pay each invoice within fifteen days after the invoice date.", ObligationCategory.PAYMENT, CU, "Provider",
            "Customer shall pay each invoice within fifteen (15) days after the invoice date", "3.2",
            term=_term(K.EVENT_TRIGGERED, "within fifteen (15) days after the invoice date", anchor="invoice_received", offset=15, unit=Unit.DAY)),
        _ob(lab, "Respond to Priority 1 support requests", "Respond to Priority 1 support requests within one hour of notification.", ObligationCategory.SERVICE_LEVEL, "Provider", CU,
            "Provider shall respond to Priority 1 support requests within one (1) hour of notification", "4.2",
            term=_term(K.EVENT_TRIGGERED, "within one (1) hour of notification", anchor="support_request_notified", offset=1, unit=Unit.HOUR)),
        _ob(lab, "Address defects promptly", "Address defects promptly and in a timely manner.", ObligationCategory.SERVICE_LEVEL, "Provider", CU,
            "Provider will address defects promptly and in a timely manner", "4.3", conf=0.7, unc="'Promptly' and 'in a timely manner' are not defined; no time limit is stated."),
        _ob(lab, "Indemnify Provider", "Indemnify Provider against all losses arising from Customer's use of the Platform, without limit.", ObligationCategory.COMPLIANCE, CU, "Provider",
            "Customer shall indemnify Provider against all losses arising from Customer's use of the Platform, without limit", "5.2"),
        _ob(lab, "Keep Confidential Information confidential", "Keep the other party's Confidential Information confidential during the term and for three years afterwards.", ObligationCategory.CONFIDENTIALITY,
            "Each party", "Each party", "Each party shall keep the other party's Confidential Information confidential during the term and for three (3) years afterwards", "6.1"),
        # Deliberately unsupported: the quote does not exist in the contract (demonstrates the QA agent).
        _ob(lab, "Provide 24/7 telephone support", "Provide round-the-clock telephone support to Customer.", ObligationCategory.SERVICE_LEVEL, "Provider", CU,
            "Provider shall provide 24/7 telephone support to Customer at all times", "4.2", conf=0.55, force=True),
    ]


# =============================================================================================
# NDA
# =============================================================================================
def _nda_extraction(lab: _Labels) -> o.ContractExtraction:
    dq = "Confidential Information may be disclosed during the two (2) years following the Effective Date"
    clauses = [
        ("2.1", ClauseType.CONFIDENTIALITY, "Protection", "Receiving Party must use at least reasonable care to protect Confidential Information.", "The receiving Party shall protect Confidential Information using at least the same degree of care it uses for its own confidential information"),
        ("2.3", ClauseType.CONFIDENTIALITY, "Return", "Return or destroy Confidential Information within 30 days of written request.", "Within thirty (30) days after the disclosing Party's written request, the receiving Party shall return or destroy all Confidential Information"),
        ("3.1", ClauseType.TERM, "Disclosure Period", "Disclosure may occur during two years after the Effective Date.", dq),
        ("3.2", ClauseType.CONFIDENTIALITY, "Survival", "Obligations survive three years after the disclosure period.", "The obligations in Section 2 survive for three (3) years after the end of the disclosure period"),
        ("4.1", ClauseType.GOVERNING_LAW, "Governing Law", "New York law governs.", "This Agreement is governed by the laws of the State of New York"),
    ]
    return o.ContractExtraction(
        title="Mutual NDA (Acme / Orion Robotics)", contract_type=ContractType.NDA,
        parties=[
            o.PartyOut(name="Acme Manufacturing Inc.", role=PartyRole.PARTNER, party_type=PartyType.COMPANY, evidence=lab.refs('Acme Manufacturing Inc. ("Acme")')),
            o.PartyOut(name="Orion Robotics GmbH", role=PartyRole.PARTNER, party_type=PartyType.COMPANY, evidence=lab.refs('Orion Robotics GmbH ("Orion")')),
        ],
        effective_date=_date(lab, "2025-09-01", "September 1, 2025", 'is effective as of September 1, 2025 (the "Effective Date")'),
        expiration_date=_date(lab, None, None), initial_term_months=_int(lab, 24, "two (2) years", dq),
        auto_renews=_bool(lab, None), renewal_term_months=_int(lab, None, None), renewal_notice_days=_int(lab, None, None),
        payment_terms=_text(lab, None), payment_days=_int(lab, None, None), currency=_text(lab, None), total_value=_num(lab, None, None),
        governing_law=_text(lab, "State of New York", "This Agreement is governed by the laws of the State of New York"),
        termination_convenience_notice_days=_int(lab, None, None), termination_cure_days=_int(lab, None, None), sla_summary=_text(lab, None),
        clauses=[o.ClauseOut(clause_type=c[1], heading=c[2], section_reference=c[0], summary=c[3], evidence=lab.refs(c[4])) for c in clauses if lab.refs(c[4])],
        missing_information=["The contract has no express termination clause."],
    )


def _nda_obligations(lab: _Labels) -> list[o.ObligationOut | None]:
    return [
        _ob(lab, "Protect Confidential Information", "Protect Confidential Information with at least the degree of care used for own confidential information, and no less than reasonable care.", ObligationCategory.CONFIDENTIALITY,
            "The receiving Party", "The disclosing Party", "The receiving Party shall protect Confidential Information using at least the same degree of care it uses for its own confidential information", "2.1"),
        _ob(lab, "Return or destroy Confidential Information", "Return or destroy all Confidential Information within thirty days after the disclosing Party's written request.", ObligationCategory.COMPLIANCE,
            "The receiving Party", "The disclosing Party", "Within thirty (30) days after the disclosing Party's written request, the receiving Party shall return or destroy all Confidential Information", "2.3",
            term=_term(K.EVENT_TRIGGERED, "Within thirty (30) days after the disclosing Party's written request", anchor="written_request_received", offset=30, unit=Unit.DAY), trigger="Disclosing Party's written request"),
    ]


# =============================================================================================
# Amendment No. 1
# =============================================================================================
def _amendment(lab: _Labels) -> o.AmendmentExtraction:
    ins = [
        ("3.2", ChangeType.MODIFIED, "payment_terms", "Payment period for undisputed invoices becomes 45 days.", "thirty (30) days", "forty-five (45) days", 45,
         "Section 3.2 of the Agreement is amended so that Customer shall pay each undisputed invoice within forty-five (45) days after receipt of the invoice"),
        ("2.2", ChangeType.MODIFIED, "renewal_notice", "Non-renewal notice period shortens from 90 to 60 days.", "ninety (90) days", "sixty (60) days", 60,
         'In Section 2.2 of the Agreement, the words "ninety (90) days" are replaced with "sixty (60) days"'),
        ("5.3", ChangeType.ADDED, "other", "New data residency requirement: Customer Data stored only in US data centers.", None, "Vendor shall store Customer Data only in data centers located in the United States", None,
         "A new Section 5.3 is added to the Agreement: Vendor shall store Customer Data only in data centers located in the United States"),
        ("8.1", ChangeType.MODIFIED, "termination_notice", "Termination-for-convenience notice increases to 90 days.", "sixty (60) days", "ninety (90) days", 90,
         "Section 8.1 of the Agreement is amended to require ninety (90) days' prior written notice"),
    ]
    return o.AmendmentExtraction(
        is_amendment=True, amends_agreement_date_text="January 15, 2025",
        amendment_effective_date=_date(lab, "2025-06-01", "June 1, 2025", 'is entered into as of June 1, 2025 (the "Amendment Effective Date")'),
        instructions=[o.AmendmentInstructionOut(target_section=t, action=a, affected_term=term, summary=s, old_value_text=old, new_value_text=new, new_int_value=n, evidence=lab.refs(q))
                      for t, a, term, s, old, new, n, q in ins if lab.refs(q)],
    )


def _amendment_obligations(lab: _Labels) -> list[o.ObligationOut | None]:
    return [_ob(lab, "Store Customer Data in the United States", "Store Customer Data only in data centers located in the United States.", ObligationCategory.COMPLIANCE, VE, CU,
                "Vendor shall store Customer Data only in data centers located in the United States", "3.1")]


# =============================================================================================
# risk signals (LLM-style review signals)
# =============================================================================================
def _risk(lab: _Labels, doc: str) -> o.RiskSignals:
    signals: list[o.RiskSignalOut] = []
    if doc == "saas":
        for ft, title, desc, sev, q in [
            (FindingType.AMBIGUITY, "Undefined time standard for defect fixes", "'Promptly and in a timely manner' has no measurable deadline, so the obligation is hard to enforce or monitor.", Severity.MEDIUM,
             "Provider will address defects promptly and in a timely manner"),
            (FindingType.RISK_SIGNAL, "Provider may change fees at any time", "The Provider can change the subscription fee at any time by notice, with no stated notice period or cap.", Severity.HIGH,
             "Provider may change the subscription fee at any time by notice to Customer"),
            (FindingType.RISK_SIGNAL, "Uncapped Customer indemnity", "The Customer's indemnity is expressly unlimited while the Provider's liability is capped, which is asymmetric.", Severity.HIGH,
             "Customer shall indemnify Provider against all losses arising from Customer's use of the Platform, without limit"),
        ]:
            ev = lab.refs(q)
            if ev:
                signals.append(o.RiskSignalOut(signal_type=ft, title=title, description=desc, severity=sev, evidence=ev, confidence=0.8))
    if doc == "msa":
        ev = lab.refs("Except for indemnification and confidentiality obligations, each party's aggregate liability is limited")
        if ev:
            signals.append(o.RiskSignalOut(signal_type=FindingType.RISK_SIGNAL, title="Liability cap excludes indemnity and confidentiality", severity=Severity.LOW, confidence=0.7, evidence=ev,
                                           description="Indemnification and confidentiality obligations sit outside the liability cap, so exposure for those items is not limited by the cap."))
    return o.RiskSignals(signals=signals)


# =============================================================================================
class FixtureLLM:
    """Hand-authored LLM stand-in for the sample contracts (demo seeding and tests only)."""

    model = "demo-fixtures"
    available = True

    @staticmethod
    def _doc(prompt: str) -> str:
        p = prompt
        if "AMENDED AND RESTATED" in p.upper() and "MASTER SERVICES" in p.upper():
            return "msa_v2"
        if "AMENDMENT NO. 1" in p.upper():
            return "amendment"
        if "SOFTWARE-AS-A-SERVICE" in p.upper() or "Helios" in p:
            return "saas"
        if "NON-DISCLOSURE" in p.upper():
            return "nda"
        if "MASTER SERVICES AGREEMENT" in p.upper():
            return "msa_v1"
        return "unknown"

    def structured(self, *, system: str, user: str, schema: type, purpose: str, temperature: float | None = 0.0, max_tokens: int | None = None) -> Any:
        lab = _Labels(user)
        doc = self._doc(user)
        if schema is o.ContractExtraction:
            if doc in ("msa_v1", "msa_v2"):
                return _msa_extraction(lab, doc == "msa_v2")
            if doc == "saas":
                return _saas_extraction(lab)
            if doc == "nda":
                return _nda_extraction(lab)
            raise ValueError(f"no fixture for document {doc}")
        if schema is o.ObligationExtraction:
            builders = {"msa_v1": lambda: _msa_obligations(lab, False), "msa_v2": lambda: _msa_obligations(lab, True), "saas": lambda: _saas_obligations(lab),
                        "nda": lambda: _nda_obligations(lab), "amendment": lambda: _amendment_obligations(lab)}
            return o.ObligationExtraction(obligations=[x for x in builders.get(doc, lambda: [])() if x is not None])
        if schema is o.AmendmentExtraction:
            return _amendment(lab)
        if schema is o.RiskSignals:
            return _risk(lab, "saas" if doc == "saas" else "msa" if doc.startswith("msa") else "other")
        if schema is o.ChangeNarratives:
            ids = re.findall(r"change_id=(\d+) type=(\w+) section=(\S+)", user)
            return o.ChangeNarratives(items=[o.ChangeNarrative(change_id=i, summary=f"Section {sec} was {t}.", materiality=Severity.MEDIUM) for i, t, sec in ids])
        raise ValueError(f"FixtureLLM cannot answer schema {schema.__name__}")
