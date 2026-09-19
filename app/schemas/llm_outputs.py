"""Structured-output schemas for LLM calls.

Compatible with OpenAI strict structured outputs: every field is required (no defaults) and optional
values are ``X | None``. Models return *verbatim quotes* tagged with the passage label they came from
(``C1``, ``C2``...); the evidence verifier checks each quote against the source before it is trusted.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ChangeType,
    ClauseType,
    ContractType,
    DeadlineKind,
    FindingType,
    ObligationCategory,
    PartyRole,
    PartyType,
    Severity,
)
from app.tools.temporal import Direction, Frequency, Unit


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuoteRef(_Strict):
    chunk_label: str = Field(description="Label of the passage the quote comes from, e.g. 'C3'.")
    quote: str = Field(description="Exact, verbatim text copied from that passage (max ~300 characters). Never paraphrase.")


# --- contract metadata --------------------------------------------------------------------
class DateFact(_Strict):
    iso_date: str | None = Field(description="Date as YYYY-MM-DD if the contract states it unambiguously, otherwise null.")
    raw_text: str | None = Field(description="The date exactly as written in the contract, or null.")
    evidence: list[QuoteRef]


class IntFact(_Strict):
    value: int | None
    raw_text: str | None
    evidence: list[QuoteRef]


class NumberFact(_Strict):
    value: float | None
    raw_text: str | None
    evidence: list[QuoteRef]


class BoolFact(_Strict):
    value: bool | None
    evidence: list[QuoteRef]


class TextFact(_Strict):
    value: str | None
    evidence: list[QuoteRef]


class PartyOut(_Strict):
    name: str = Field(description="Full legal name exactly as written.")
    role: PartyRole
    party_type: PartyType
    evidence: list[QuoteRef]


class ClauseOut(_Strict):
    clause_type: ClauseType
    heading: str | None
    section_reference: str | None = Field(description="Section number/label as written, e.g. '7.2'.")
    summary: str = Field(description="One or two neutral sentences describing what the clause says.")
    evidence: list[QuoteRef]


class ContractExtraction(_Strict):
    title: str | None
    contract_type: ContractType
    parties: list[PartyOut]
    effective_date: DateFact
    expiration_date: DateFact = Field(description="Only an expiration date stated explicitly. Do not compute one.")
    initial_term_months: IntFact
    auto_renews: BoolFact
    renewal_term_months: IntFact
    renewal_notice_days: IntFact = Field(description="Days of notice required to prevent renewal / terminate at renewal.")
    payment_terms: TextFact
    payment_days: IntFact = Field(description="Number of days allowed to pay an invoice, if stated.")
    currency: TextFact
    total_value: NumberFact
    governing_law: TextFact
    termination_convenience_notice_days: IntFact
    termination_cure_days: IntFact
    sla_summary: TextFact
    clauses: list[ClauseOut]
    missing_information: list[str] = Field(description="Important items the contract does not state (e.g. 'No governing law clause').")


# --- obligations --------------------------------------------------------------------------
class TemporalTermOut(_Strict):
    """A time requirement exactly as the contract states it. Do NOT compute dates."""

    kind: DeadlineKind
    raw_text: str | None = Field(description="The time phrase as written, e.g. 'within thirty (30) days after receipt of the invoice'.")
    explicit_date_text: str | None = Field(description="Only for kind=explicit: the date as written.")
    anchor: str | None = Field(description="What the period counts from: 'effective_date', 'expiration_date', 'amendment_effective_date', or a snake_case event such as 'invoice_received'.")
    offset: int | None
    unit: Unit | None
    direction: Direction | None
    frequency: Frequency | None = Field(description="Only for recurring obligations.")
    nth_business_day: int | None = Field(description="For 'on or before the Nth Business Day of each month'.")
    day_of_month: int | None


class ObligationOut(_Strict):
    title: str = Field(description="Short imperative title, max 10 words.")
    action: str = Field(description="What must be done, in one sentence.")
    category: ObligationCategory
    responsible_party: str | None = Field(description="Who must perform it, using the party's name or defined term as written.")
    beneficiary: str | None
    trigger: str | None = Field(description="Event or condition that starts the obligation, if any.")
    conditions: str | None
    frequency_text: str | None = Field(description="Recurrence as written, e.g. 'monthly'.")
    deadline_text: str | None
    temporal: TemporalTermOut | None
    section_reference: str | None
    evidence: list[QuoteRef]
    confidence: float = Field(description="0.0-1.0 confidence that this is a genuine, correctly attributed obligation.")
    uncertainty: str | None = Field(description="What is unclear or ambiguous about this obligation, if anything.")


class ObligationExtraction(_Strict):
    obligations: list[ObligationOut]


# --- amendments ---------------------------------------------------------------------------
class AmendmentInstructionOut(_Strict):
    target_section: str | None = Field(description="Section of the base agreement being changed, e.g. '3.2'.")
    action: ChangeType
    affected_term: str = Field(description="One of: payment_terms, renewal_notice, renewal_term, termination_notice, effective_date, expiration_date, governing_law, liability, other.")
    summary: str
    old_value_text: str | None
    new_value_text: str | None
    new_int_value: int | None = Field(description="If the change sets a number of days/months, that number.")
    evidence: list[QuoteRef]


class AmendmentExtraction(_Strict):
    is_amendment: bool
    amends_agreement_date_text: str | None
    amendment_effective_date: DateFact
    instructions: list[AmendmentInstructionOut]


class ChangeNarrative(_Strict):
    change_id: str
    summary: str = Field(description="Neutral one-sentence description of what changed and why it may matter.")
    materiality: Severity


class ChangeNarratives(_Strict):
    items: list[ChangeNarrative]


# --- risk ---------------------------------------------------------------------------------
class RiskSignalOut(_Strict):
    signal_type: FindingType = Field(description="ambiguity, conflict, risk_signal (unusual term) or missing_information")
    title: str
    description: str = Field(description="Why this deserves human review. Not a legal conclusion.")
    severity: Severity
    evidence: list[QuoteRef]
    confidence: float


class RiskSignals(_Strict):
    signals: list[RiskSignalOut]


# --- Q&A ----------------------------------------------------------------------------------
class AnswerOut(_Strict):
    answer: str = Field(description="Direct answer using ONLY the provided passages. If they do not establish the answer, say so.")
    insufficient_evidence: bool
    citations: list[QuoteRef]
    uncertainty: str | None = Field(description="Caveats, ambiguity, or conditions the reader must check.")


class IntentOut(_Strict):
    intent: str = Field(description="One of the allowed intent names.")
    days_horizon: int | None
    topic: str | None
    party: str | None
    contract_hint: str | None
