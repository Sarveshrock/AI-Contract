"""Human review workbench: decisions on AI findings, obligations, deadlines and proposed amendment changes."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.core.errors import ValidationFailure
from app.database.store import F
from app.models.entities import ReviewCase
from app.models.enums import (
    FindingStatus,
    ObligationStatus,
    ReviewCaseStatus,
    ReviewDecision,
    ReviewStatus,
    SubjectType,
)
from app.repositories import Repositories
from app.security.access import Permission, Principal
from app.services.audit import AuditService
from app.services.deadlines import DeadlineService
from app.services.risk import RiskService

OBLIGATION_EDITABLE = {"title", "description", "category", "responsible_party_name", "beneficiary_name", "trigger", "conditions", "frequency", "deadline_text"}
CONTRACT_FIELD_MAP = {"renewal_notice_days": "renewal_notice_days", "renewal_term_months": "renewal_term_months", "governing_law": "governing_law",
                      "effective_date": "effective_date", "expiration_date": "expiration_date"}


class ReviewService:
    def __init__(self, principal: Principal, repos: Repositories, audit: AuditService, deadlines: DeadlineService, risk: RiskService) -> None:
        self._p, self._r, self._audit, self._deadlines, self._risk = principal, repos, audit, deadlines, risk

    def queue(self, *, status: ReviewCaseStatus | None = None, contract_id: UUID | str | None = None, open_only: bool = True) -> list[ReviewCase]:
        filters = []
        if status:
            filters.append(F.eq("status", status))
        elif open_only:
            filters.append(F.in_("status", [ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW]))
        if contract_id:
            filters.append(F.eq("contract_id", str(contract_id)))
        cases = self._r.reviews.list(filters, order_by=[("created_at", True)])
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        return sorted(cases, key=lambda c: (order[c.priority.value], c.created_at))

    def counts(self) -> dict[str, int]:
        return {s.value: self._r.reviews.count([F.eq("status", s)]) for s in ReviewCaseStatus}

    def assign(self, case_id: UUID | str, user_id: UUID | str | None) -> ReviewCase:
        self._p.require(Permission.REVIEW_DECIDE)
        c = self._r.reviews.update(case_id, assignee_id=str(user_id) if user_id else None, status=ReviewCaseStatus.IN_REVIEW)
        self._audit.record("review.assign", "review_case", c.id, assignee=str(user_id) if user_id else None)
        return c

    def decide(self, case_id: UUID | str, decision: ReviewDecision, notes: str = "", correction: dict[str, Any] | None = None) -> ReviewCase:
        """Record a human decision and apply its (limited, explicit) effect on the reviewed subject."""
        self._p.require(Permission.REVIEW_DECIDE)
        case = self._r.reviews.require(case_id)
        if case.status in (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.DISMISSED):
            raise ValidationFailure("case closed", user_message="This review case has already been closed.")
        if decision in (ReviewDecision.REJECTED, ReviewDecision.CORRECTED) and not notes.strip():
            raise ValidationFailure("notes required", user_message="Please add a note explaining a rejection or correction.")
        if decision is ReviewDecision.CORRECTED and not correction:
            raise ValidationFailure("correction required", user_message="Provide the corrected values.")
        effect = self._apply(case, decision, correction or {})
        now = datetime.now(timezone.utc)
        status = ReviewCaseStatus.IN_REVIEW if decision is ReviewDecision.ESCALATED else ReviewCaseStatus.RESOLVED
        updated = self._r.reviews.update(case.id, decision=decision, decision_notes=notes.strip() or None, decided_by=str(self._p.user_id), decided_at=now, status=status)
        self._audit.record("review.decide", "review_case", case.id, decision=decision.value, subject_type=case.subject_type.value, effect=effect)
        if case.contract_id:
            self._refresh_contract(case.contract_id)
        return updated

    def dismiss(self, case_id: UUID | str, reason: str) -> ReviewCase:
        self._p.require(Permission.REVIEW_DECIDE)
        if not reason.strip():
            raise ValidationFailure("reason required", user_message="Please say why this case is being dismissed.")
        c = self._r.reviews.update(case_id, status=ReviewCaseStatus.DISMISSED, decision_notes=reason.strip(), decided_by=str(self._p.user_id), decided_at=datetime.now(timezone.utc))
        self._audit.record("review.dismiss", "review_case", c.id, reason=reason)
        return c

    # ------------------------------------------------------------------
    def _apply(self, case: ReviewCase, decision: ReviewDecision, correction: dict[str, Any]) -> str:
        st = case.subject_type
        if decision is ReviewDecision.ESCALATED:
            return "escalated"
        if st is SubjectType.OBLIGATION and case.subject_id:
            o = self._r.obligations.get(case.subject_id)
            if o is None:
                return "subject missing"
            if decision is ReviewDecision.APPROVED:
                self._r.obligations.update(o.id, review_status=ReviewStatus.APPROVED, human_modified=True)
                return "obligation approved"
            if decision is ReviewDecision.REJECTED:
                self._r.obligations.update(o.id, review_status=ReviewStatus.REJECTED, status=ObligationStatus.WAIVED, human_modified=True)
                return "obligation rejected (removed from active lists)"
            fields = {k: v for k, v in correction.items() if k in OBLIGATION_EDITABLE}
            self._r.obligations.update(o.id, review_status=ReviewStatus.CORRECTED, human_modified=True, **fields)
            return f"obligation corrected: {', '.join(sorted(fields))}"
        if st is SubjectType.FINDING and case.subject_id:
            f = self._r.findings.get(case.subject_id)
            if f is None:
                return "subject missing"
            if decision is ReviewDecision.APPROVED:
                self._r.findings.update(f.id, status=FindingStatus.ACKNOWLEDGED, review_status=ReviewStatus.APPROVED)
                return "finding confirmed as a valid review signal"
            self._r.findings.update(f.id, status=FindingStatus.DISMISSED, review_status=ReviewStatus.REJECTED)
            return "finding dismissed as not applicable"
        if st is SubjectType.AMENDMENT and case.proposed_change.get("kind") == "contract_field":
            if decision is ReviewDecision.APPROVED and case.contract_id:
                return self._apply_field_change(case.contract_id, case.proposed_change)
            return "amendment change not applied"
        if st is SubjectType.DEADLINE and case.subject_id:
            if decision is ReviewDecision.APPROVED:
                self._deadlines.confirm(case.subject_id)
                return "deadline confirmed"
            if decision is ReviewDecision.REJECTED:
                self._deadlines.reject(case.subject_id, "Rejected in review")
                return "deadline rejected"
        if st is SubjectType.CONTRACT and case.contract_id and decision is ReviewDecision.APPROVED:
            self._r.contracts.update(case.contract_id, review_status=ReviewStatus.APPROVED)
            return "contract review approved"
        return "no automatic effect"

    def _apply_field_change(self, contract_id: UUID, change: dict[str, Any]) -> str:
        fld, value = change.get("field"), change.get("value")
        if fld in CONTRACT_FIELD_MAP:
            v = date.fromisoformat(value) if fld in ("effective_date", "expiration_date") else value
            self._r.contracts.update(contract_id, **{CONTRACT_FIELD_MAP[fld]: v})
            patched = self._deadlines.apply_term_override(contract_id, fld, value) if isinstance(value, int) else 0
            return f"contract field {fld} updated; {patched} deadline(s) recalculated"
        if fld == "payment_days":
            self._r.contracts.update(contract_id, payment_terms=f"Undisputed invoices payable within {value} days after receipt (per approved amendment)")
            patched = self._deadlines.apply_term_override(contract_id, fld, int(value))
            return f"payment terms updated; {patched} deadline(s) recalculated"
        return f"approved; '{fld}' is tracked in the amendment record only"

    def _refresh_contract(self, contract_id: UUID) -> None:
        self._risk.score_contract(contract_id)
        remaining = self._r.reviews.count([F.eq("contract_id", str(contract_id)), F.in_("status", [ReviewCaseStatus.OPEN, ReviewCaseStatus.IN_REVIEW])])
        if remaining == 0:
            c = self._r.contracts.get(contract_id)
            if c and c.review_status is ReviewStatus.NEEDS_REVIEW:
                self._r.contracts.update(contract_id, review_status=ReviewStatus.APPROVED)
