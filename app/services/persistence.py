"""Writes analysis results to the digital twin. The *only* writer for agent output.

Idempotent: clauses, obligations, deadlines and findings are matched by fingerprint, so re-running an
analysis updates rather than duplicates, and rows a human has edited or reviewed are never overwritten.
Only *verified* evidence is stored. Multi-step writes are atomic on SQLite and compensated on Supabase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.agents.evidence import ResolvedEvidence
from app.agents.state import AnalysisState, DeadlineDraft, FindingDraft, ObligationDraft
from app.agents.util import party_key
from app.core.logging import get_logger
from app.database.store import F
from app.models.base import TableModel
from app.models.entities import (
    Amendment,
    AnalysisFinding,
    Clause,
    Contract,
    ContractParty,
    ContractVersion,
    Deadline,
    Evidence,
    Obligation,
    Party,
    ReviewCase,
)
from app.models.enums import (
    AnalysisStatus,
    ContractStatus,
    DeadlineCategory,
    DeadlineKind,
    DeadlineStatus,
    DocumentRole,
    FindingStatus,
    ReviewCaseStatus,
    ReviewStatus,
    Severity,
    SubjectType,
    ValidationStatus,
)
from app.repositories import Repositories
from app.tools.temporal import ComputationStatus

log = get_logger(__name__)
_LOCKED_REVIEW = (ReviewStatus.APPROVED, ReviewStatus.REJECTED, ReviewStatus.CORRECTED)


@dataclass
class PersistSummary:
    parties: int = 0
    clauses: int = 0
    obligations: int = 0
    deadlines: int = 0
    findings: int = 0
    evidence: int = 0
    review_cases: int = 0
    skipped_human_edited: int = 0
    notes: list[str] = field(default_factory=list)


class AnalysisPersistence:
    def __init__(self, repos: Repositories) -> None:
        self._r = repos
        self._org = repos.org_id

    # ------------------------------------------------------------------
    def persist(self, *, run_id: UUID, contract: Contract, version: ContractVersion, state: AnalysisState, role: DocumentRole,
                needs_review: bool, is_new_contract: bool = False, base_snapshot: dict | None = None) -> PersistSummary:
        summary = PersistSummary()
        created: list[tuple[str, UUID]] = []
        try:
            with self._r.store.transaction():
                self._persist(run_id, contract, version, state, role, needs_review, is_new_contract, summary, created)
        except Exception:
            self._compensate(created)
            raise
        return summary

    def _compensate(self, created: list[tuple[str, UUID]]) -> None:
        """Supabase has no client-side transaction: remove rows created by the failed run."""
        for table, id_ in reversed(created):
            try:
                self._r.store.delete(table, [F.eq("org_id", self._org), F.eq("id", str(id_))])
            except Exception:  # noqa: BLE001
                log.exception("compensation failed for %s %s", table, id_)

    # ------------------------------------------------------------------
    def _persist(self, run_id, contract, version, state, role, needs_review, is_new, summary, created) -> None:
        r = self._r
        partial = role in (DocumentRole.AMENDMENT, DocumentRole.ADDENDUM, DocumentRole.SCHEDULE)
        ex = state.contract
        clause_ids: dict[str, UUID] = {}
        party_ids: dict[str, UUID] = {}

        def track(table: str, item: TableModel) -> None:
            created.append((table, item.id))

        # ---- contract fields & version snapshot
        if ex is not None and not partial:
            upd: dict[str, Any] = {}
            if is_new and ex.title:
                upd["title"] = ex.title[:200]
            if ex.contract_type.value != "unknown":
                upd["contract_type"] = ex.contract_type
            for name in ("effective_date", "expiration_date", "auto_renews", "renewal_notice_days", "renewal_term_months", "governing_law",
                         "payment_terms", "currency", "total_value"):
                v = getattr(ex, name).value
                if v is not None:
                    upd[name] = v
            if "expiration_date" not in upd:
                exp = next((d.computation.due_date for d in state.deadlines if d.label == "Contract expiration" and d.computation.due_date), None)
                if exp:
                    upd["expiration_date"] = exp
            upd["summary"] = self._summary(state)
            r.contracts.update(contract.id, **upd)
            r.versions.update(version.id, extraction=ex.snapshot(), effective_date=ex.effective_date.value)
        elif partial and state.amendment:
            r.versions.update(version.id, extraction={"amendment": {"summary": state.amendment.summary, "instructions": state.amendment.instructions}})

        # ---- parties
        if ex is not None:
            existing_parties = {p.normalized_name: p for p in r.parties.list()}
            for pd in ex.parties:
                key = party_key(pd.name)
                party = existing_parties.get(key)
                if party is None:
                    party = r.parties.add(Party(org_id=self._org, name=pd.name, normalized_name=key, party_type=pd.party_type))
                    existing_parties[key] = party
                    track("parties", party)
                party_ids[key] = party.id
                cp = r.contract_parties.first([F.eq("contract_id", str(contract.id)), F.eq("party_id", str(party.id)), F.eq("role", pd.role)])
                if cp is None:
                    cp = r.contract_parties.add(ContractParty(org_id=self._org, contract_id=contract.id, party_id=party.id, role=pd.role,
                                                              confidence=1.0 if any(e.verified for e in pd.evidence) else 0.3,
                                                              review_status=ReviewStatus.UNREVIEWED if any(e.verified for e in pd.evidence) else ReviewStatus.NEEDS_REVIEW))
                    track("contract_parties", cp)
                summary.parties += 1
                summary.evidence += self._evidence(contract, version, SubjectType.PARTY, cp.id, "party", pd.evidence, run_id, created)

        # ---- clauses
        if ex is not None:
            have = {c.fingerprint: c for c in r.clauses.list([F.eq("contract_version_id", str(version.id))])}
            for cd in ex.clauses:
                ev0 = next((e for e in cd.evidence if e.verified), None)
                values = dict(clause_type=cd.clause_type, heading=cd.heading, section_reference=cd.section_reference, page_number=cd.page_number, text=cd.text,
                              summary=cd.summary, confidence=1.0 if ev0 else 0.3, analysis_run_id=run_id,
                              document_id=ev0.document_id if ev0 else None)
                row = have.get(cd.fingerprint)
                if row is None:
                    row = r.clauses.add(Clause(org_id=self._org, contract_id=contract.id, contract_version_id=version.id, fingerprint=cd.fingerprint,
                                               review_status=ReviewStatus.UNREVIEWED if ev0 else ReviewStatus.NEEDS_REVIEW, **values))
                    track("clauses", row)
                elif row.human_modified or row.review_status in _LOCKED_REVIEW:
                    summary.skipped_human_edited += 1
                else:
                    row = r.clauses.update(row.id, **values)
                clause_ids[cd.section_reference or cd.fingerprint] = row.id
                summary.clauses += 1
                summary.evidence += self._evidence(contract, version, SubjectType.CLAUSE, row.id, "clause", cd.evidence, run_id, created)

        # ---- obligations
        ob_ids: dict[str, UUID] = {}
        have_o = {o.fingerprint: o for o in r.obligations.list([F.eq("contract_version_id", str(version.id))])}
        # a new full version inherits human decisions (owner, completion, dispute, review) from the version it supersedes
        prior_o: dict[str, Obligation] = {}
        if version.supersedes_version_id and not partial:
            prior_o = {o.fingerprint: o for o in r.obligations.list([F.eq("contract_version_id", str(version.supersedes_version_id))]) if o.human_modified}
        for od in state.obligations:
            summary.obligations += 1
            row = have_o.get(od.fingerprint)
            needs = od.fingerprint in state.unsupported_fingerprints or od.confidence < 0.6 or "schema_invalid" in od.flags
            values = dict(
                title=od.title, description=od.description, category=od.category, responsible_party_name=od.responsible, beneficiary_name=od.beneficiary,
                responsible_party_id=party_ids.get(party_key(od.responsible_party_match or "")) if od.responsible_party_match else None,
                beneficiary_party_id=party_ids.get(party_key(od.beneficiary_match or "")) if od.beneficiary_match else None,
                trigger=od.trigger, conditions=od.conditions, frequency=od.frequency_text, deadline_text=od.deadline_text, confidence=od.confidence,
                uncertainty=od.uncertainty, is_recurring=("recurring" in od.flags) or bool(od.temporal and od.temporal.kind is DeadlineKind.RECURRING),
                clause_id=clause_ids.get(od.section_reference or ""), analysis_run_id=run_id,
            )
            if row is None:
                inherit: dict[str, Any] = {"review_status": ReviewStatus.NEEDS_REVIEW if needs else ReviewStatus.UNREVIEWED}
                prior = prior_o.get(od.fingerprint)
                if prior is not None:
                    inherit.update(owner_id=prior.owner_id, status=prior.status, human_modified=True, dispute_flag=prior.dispute_flag,
                                   completed_at=prior.completed_at, completed_by=prior.completed_by)
                    if prior.review_status in _LOCKED_REVIEW:
                        inherit["review_status"] = prior.review_status
                row = r.obligations.add(Obligation(org_id=self._org, contract_id=contract.id, contract_version_id=version.id, fingerprint=od.fingerprint,
                                                   **inherit, **values))
                track("obligations", row)
            elif row.human_modified or row.review_status in _LOCKED_REVIEW:
                summary.skipped_human_edited += 1
            else:
                row = r.obligations.update(row.id, review_status=ReviewStatus.NEEDS_REVIEW if needs else row.review_status, **values)
            ob_ids[od.fingerprint] = row.id
            summary.evidence += self._evidence(contract, version, SubjectType.OBLIGATION, row.id, "obligation", od.evidence, run_id, created)

        # ---- deadlines
        events = {e.event_type.value: e for e in r.events.list([F.eq("contract_id", str(contract.id))])}
        have_d = {d.fingerprint: d for d in r.deadlines.list([F.eq("contract_version_id", str(version.id))])}
        dl_ids: dict[str, UUID] = {}
        for dd in state.deadlines:
            summary.deadlines += 1
            comp = dd.computation
            values = dict(
                obligation_id=ob_ids.get(dd.obligation_fingerprint or ""), kind=comp.kind, category=DeadlineCategory(dd.category), label=dd.label,
                due_date=comp.due_date, due_at=comp.due_at, timezone=comp.rule.timezone or "UTC", anchor_event=comp.anchor_name, anchor_date=comp.anchor_date,
                trigger_event_id=events[dd.anchor_event].id if dd.anchor_event and dd.anchor_event in events else None,
                calculation_rule=comp.rule.to_dict(), calculation_trace=list(comp.trace), assumptions=list(comp.assumptions) + [f"Note: {n}" for n in comp.notes],
                missing_anchors=list(comp.missing_anchors),
                recurrence=self._recurrence(dd), source_clause_id=clause_ids.get(dd.section_reference or ""),
                validation_status=comp.validation_status, analysis_run_id=run_id,
            )
            row = have_d.get(dd.fingerprint)
            if row is None:
                row = r.deadlines.add(Deadline(org_id=self._org, contract_id=contract.id, contract_version_id=version.id, fingerprint=dd.fingerprint, **values))
                track("deadlines", row)
            elif row.human_modified or row.validation_status in (ValidationStatus.CONFIRMED, ValidationStatus.REJECTED):
                summary.skipped_human_edited += 1
            else:
                keep_status = row.status if row.status is not DeadlineStatus.OPEN else DeadlineStatus.OPEN
                row = r.deadlines.update(row.id, status=keep_status, **values)
            dl_ids[dd.fingerprint] = row.id
            summary.evidence += self._evidence(contract, version, SubjectType.DEADLINE, row.id, "deadline", dd.evidence, run_id, created)

        # ---- findings
        have_f = {f.fingerprint: f for f in r.findings.list([F.eq("contract_id", str(contract.id))])}
        finding_rows: dict[str, AnalysisFinding] = {}
        for fd in state.findings:
            summary.findings += 1
            values = dict(
                contract_version_id=version.id, run_id=run_id, finding_type=fd.finding_type, severity=fd.severity, signal_category=fd.category, title=fd.title,
                description=fd.description, weight=fd.weight, confidence=fd.confidence, obligation_id=ob_ids.get(fd.obligation_fingerprint or ""),
                deadline_id=dl_ids.get(fd.deadline_fingerprint or ""), contributions={"signal": fd.signal, "weight": fd.weight, "category": fd.category.value},
            )
            row = have_f.get(fd.fingerprint)
            if row is None:
                row = r.findings.add(AnalysisFinding(org_id=self._org, contract_id=contract.id, fingerprint=fd.fingerprint, **values))
                track("analysis_findings", row)
            else:
                row = r.findings.update(row.id, **values, **({"status": FindingStatus.OPEN} if row.status is FindingStatus.RESOLVED and fd.severity in (Severity.HIGH, Severity.CRITICAL) else {}))
            finding_rows[fd.fingerprint] = row
            summary.evidence += self._evidence(contract, version, SubjectType.FINDING, row.id, "finding", fd.evidence, run_id, created)

        self._write_amendment(run_id, contract, state, created)

        # ---- review cases
        summary.review_cases += self._review_cases(contract, state, ob_ids, finding_rows, run_id, created, needs_review, role)
        r.contracts.update(
            contract.id, analysis_status=AnalysisStatus.NEEDS_REVIEW if needs_review else AnalysisStatus.COMPLETED,
            review_status=ReviewStatus.NEEDS_REVIEW if needs_review else contract.review_status,
            status=ContractStatus.IN_REVIEW if contract.status is ContractStatus.DRAFT else contract.status,
        )

    def persist_amendment(self, *, run_id: UUID, contract: Contract, state: AnalysisState) -> None:
        """Comparison-only write: stores the amendment record and nothing else."""
        created: list[tuple[str, UUID]] = []
        try:
            with self._r.store.transaction():
                self._write_amendment(run_id, contract, state, created)
        except Exception:
            self._compensate(created)
            raise

    def _write_amendment(self, run_id, contract, state: AnalysisState, created) -> None:
        r = self._r
        def track(table: str, item: TableModel) -> None:
            created.append((table, item.id))

        if state.amendment:
            am = state.amendment
            payload = [
                *[{"kind": "clause", **{k: getattr(d, k) for k in ("change_type", "section_old", "section_new", "title", "clause_type", "similarity", "summary", "materiality")},
                   "old_text": d.old_text[:1500], "new_text": d.new_text[:1500]} for d in am.clause_diffs],
                *[{"kind": "field", "field": f.field, "old": _plain(f.old), "new": _plain(f.new), "summary": f.summary} for f in am.field_changes],
                *[{"kind": "instruction", **i} for i in am.instructions],
                *[{"kind": "deadline", **s} for s in am.deadline_shifts],
            ]
            existing = r.amendments.first([F.eq("base_version_id", am.base_version_id), F.eq("amendment_version_id", am.new_version_id)])
            if existing is None:
                row = r.amendments.add(Amendment(org_id=self._org, contract_id=contract.id, base_version_id=UUID(am.base_version_id),
                                                 amendment_version_id=UUID(am.new_version_id), summary=am.summary, changes=payload, run_id=run_id))
                track("amendments", row)
            else:
                r.amendments.update(existing.id, summary=am.summary, changes=payload, run_id=run_id)


    # ------------------------------------------------------------------
    def _evidence(self, contract, version, subject_type: SubjectType, subject_id: UUID, field_name: str, evidence: list[ResolvedEvidence],
                  run_id: UUID, created) -> int:
        self._r.evidence.delete_where([F.eq("subject_type", subject_type), F.eq("subject_id", str(subject_id)), F.eq("field_name", field_name)])
        rows = []
        for e in evidence:
            if not e.verified or e.chunk_id is None:
                continue  # unverifiable quotes are never stored as evidence
            rows.append(Evidence(org_id=self._org, contract_id=contract.id, contract_version_id=version.id, document_id=e.document_id, chunk_id=e.chunk_id,
                                 subject_type=subject_type, subject_id=subject_id, field_name=field_name, page_number=e.page_number,
                                 section_reference=e.section_reference, quote=e.quote, quote_hash=e.quote_hash, char_start=e.char_start, char_end=e.char_end,
                                 verified=True, verification_method=e.method, analysis_run_id=run_id))
        if rows:
            self._r.evidence.add_many(rows)
            created.extend(("evidence", x.id) for x in rows)
        return len(rows)

    @staticmethod
    def _recurrence(dd: DeadlineDraft) -> dict[str, Any]:
        rule = dd.computation.rule
        if rule.kind is not DeadlineKind.RECURRING:
            return {}
        return {"frequency": rule.frequency.value if rule.frequency else None, "nth_business_day": rule.nth_business_day, "day_of_month": rule.day_of_month,
                "occurrences": [d.isoformat() for d in dd.computation.occurrences[:36]]}

    @staticmethod
    def _summary(state: AnalysisState) -> str:
        ex = state.contract
        if ex is None:
            return ""
        parts = []
        if ex.parties:
            parts.append(" / ".join(p.name for p in ex.parties[:3]))
        if ex.effective_date.value:
            parts.append(f"effective {ex.effective_date.value.isoformat()}")
        if ex.initial_term_months.value:
            parts.append(f"{ex.initial_term_months.value}-month term")
        if ex.auto_renews.value:
            parts.append("auto-renews" + (f" ({ex.renewal_notice_days.value}-day notice)" if ex.renewal_notice_days.value else ""))
        return "; ".join(parts) + (f". {len(state.obligations)} obligation(s) extracted." if state.obligations else ".")

    def _review_cases(self, contract, state: AnalysisState, ob_ids, finding_rows, run_id, created, needs_review: bool, role: DocumentRole) -> int:
        r = self._r
        open_cases = r.reviews.list([F.eq("contract_id", str(contract.id)), F.in_("status", [ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW])])
        keyed = {(c.subject_type, str(c.subject_id), c.title) for c in open_cases}
        made = 0

        def add(title: str, reason: str, priority: Severity, subject_type: SubjectType, subject_id: UUID | None, finding_id: UUID | None = None,
                proposed: dict | None = None) -> None:
            nonlocal made
            key = (subject_type, str(subject_id), title)
            if key in keyed:
                return
            keyed.add(key)
            case = r.reviews.add(ReviewCase(org_id=self._org, contract_id=contract.id, finding_id=finding_id, run_id=run_id, subject_type=subject_type,
                                            subject_id=subject_id, title=title[:200], reason=reason, priority=priority, proposed_change=proposed or {}))
            created.append(("review_cases", case.id))
            made += 1

        for od in state.obligations:
            if od.fingerprint in state.unsupported_fingerprints:
                add(f"Verify obligation: {od.title}", "The AI could not tie this obligation to verbatim contract text.", Severity.MEDIUM, SubjectType.OBLIGATION, ob_ids.get(od.fingerprint))
            elif od.confidence < 0.6:
                add(f"Check obligation: {od.title}", f"Low extraction confidence ({od.confidence:.0%}). {od.uncertainty or ''}".strip(), Severity.LOW, SubjectType.OBLIGATION, ob_ids.get(od.fingerprint))
        for fd in state.findings:
            row = finding_rows.get(fd.fingerprint)
            if row and fd.severity in (Severity.HIGH, Severity.CRITICAL):
                add(fd.title, fd.description, fd.severity, SubjectType.FINDING, row.id, row.id)
        if state.amendment and role in (DocumentRole.AMENDMENT, DocumentRole.ADDENDUM, DocumentRole.SCHEDULE):
            for fc in state.amendment.field_changes:
                if fc.new is not None:
                    add(f"Apply amendment: {fc.field.replace('_', ' ')}", fc.summary + " The amendment is not applied to the contract until you approve.", Severity.MEDIUM, SubjectType.AMENDMENT,
                        UUID(state.amendment.new_version_id), proposed={"kind": "contract_field", "field": fc.field, "value": _plain(fc.new), "old": _plain(fc.old)})
        if needs_review and state.qa and made == 0:
            add("Review AI analysis quality", f"Quality score {state.qa.score:.2f}; evidence coverage {state.qa.coverage:.0%}. Issues: " + "; ".join(i.message for i in state.qa.issues[:3]),
                Severity.MEDIUM, SubjectType.CONTRACT, contract.id)
        return made


def _plain(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


_ = (ComputationStatus, timezone)
