"""Demo-mode seeding. Clearly identified: every seeded contract carries ``is_demo=True``.

The sample documents go through the *real* ingestion, retrieval, temporal, QA, risk and persistence
code. Only the LLM step is replaced by curated fixtures (``FixtureLLM``), and a few human actions
(owner assignment, completions, an invoice event, confirmations) are recorded so the screens have
realistic content. Nothing here runs unless the user explicitly loads demo data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from app.core.container import WorkspaceContext
from app.core.errors import ContractLensError, DuplicateDocumentError, ValidationFailure
from app.database.store import F
from app.demo.fixtures import FixtureLLM
from app.models.entities import PlaybookRule
from app.security.access import Permission
from app.models.enums import (
    ClauseType,
    ContractStatus,
    DeadlineCategory,
    DocumentRole,
    EventType,
    IntegrationProvider,
    NoteKind,
    ObligationStatus,
    PlaybookRuleType,
    Role,
    Severity,
)

SAMPLES = [
    ("msa_v1.pdf", None, DocumentRole.ORIGINAL, "Original"),
    ("msa_amendment_1.pdf", "msa", DocumentRole.AMENDMENT, "Amendment No. 1"),
    ("msa_v2_restated.docx", "msa", DocumentRole.REVISED, "Amended & Restated"),
    ("saas_subscription.pdf", None, DocumentRole.ORIGINAL, "Original"),
    ("mutual_nda.pdf", None, DocumentRole.ORIGINAL, "Original"),
]


@dataclass
class SeedReport:
    contracts: int = 0
    documents: int = 0
    obligations: int = 0
    deadlines: int = 0
    review_cases: int = 0
    alerts: int = 0
    notes: list[str] = field(default_factory=list)


def has_demo_data(ws: WorkspaceContext) -> bool:
    return ws.repos.contracts.count([F.eq("is_demo", True)]) > 0


def seed_demo(ws: WorkspaceContext, sample_dir: Path, *, today: date | None = None, on_progress: Callable[[str, float], None] | None = None) -> SeedReport:
    """Load the bundled sample contracts into an (ideally empty) workspace."""
    if not ws.settings.is_demo:
        raise ValidationFailure("demo data only in local mode", user_message="Demo data can only be loaded in local demo mode, never into a Supabase workspace.")
    if has_demo_data(ws):
        raise ValidationFailure("demo data exists", user_message="Demo data is already loaded.")
    today = today or date.today()
    notify = on_progress or (lambda msg, frac: None)
    report = SeedReport()
    # a workspace whose services use the curated fixtures instead of a live model
    demo = ws.container.open_workspace(ws.principal, llm=FixtureLLM(), embedder=ws.embedder)
    _playbook(demo)
    ids: dict[str, str] = {}
    for i, (filename, group, role, label) in enumerate(SAMPLES):
        notify(f"Processing {filename}", i / (len(SAMPLES) + 1))
        path = sample_dir / filename
        if not path.exists():
            report.notes.append(f"Missing sample file {filename}; run scripts/generate_samples.py")
            continue
        try:
            res = demo.ingestion.ingest_file(path, contract_id=ids.get(group) if group else None, role=role, label=label, is_demo=True)
        except DuplicateDocumentError:
            report.notes.append(f"{filename} was already uploaded.")
            continue
        if group is None and filename.startswith("msa"):
            ids["msa"] = str(res.contract_id)
        try:
            demo.analysis.analyze_version(res.contract_id, res.version_id, is_new_contract=group is None, today=today)
        except ContractLensError as exc:
            report.notes.append(f"Analysis of {filename} failed: {exc.user_message}")
        report.documents += 1
    notify("Recording demo activity", 0.9)
    _human_activity(demo, today, report)
    # mark everything seeded as demo (amendment/restated versions attach to the MSA contract)
    demo.repos.contracts.update_where([F.is_null("deleted_at")], is_demo=True)
    report.alerts = len(demo.alerts.scan(today))
    report.contracts = demo.repos.contracts.count([F.eq("is_demo", True)])
    report.obligations = demo.repos.obligations.count()
    report.deadlines = demo.repos.deadlines.count()
    report.review_cases = demo.repos.reviews.count()
    demo.audit.record("demo.seed", "organization", ws.principal.org_id, contracts=report.contracts)
    notify("Done", 1.0)
    return report


def remove_demo_data(ws: WorkspaceContext) -> int:
    """Delete demo contracts (cascades to versions, chunks, obligations, deadlines, evidence...)."""
    ws.principal.require(Permission.CONTRACTS_DELETE)
    n = 0
    for c in ws.repos.contracts.list([F.eq("is_demo", True)]):
        for d in ws.repos.documents.list([F.eq("contract_id", str(c.id))]):
            ws.indexer.remove_document(str(d.id))
        ws.repos.contracts.delete(c.id)
        n += 1
    ws.audit.record("demo.remove", "organization", ws.principal.org_id, contracts=n)
    return n


def _playbook(ws: WorkspaceContext) -> None:
    rules = [
        PlaybookRule(org_id=ws.principal.org_id, name="Renewal notice at most 60 days", rule_type=PlaybookRuleType.MAX_NOTICE_DAYS, params={"days": 60}, severity=Severity.MEDIUM,
                     description="Long notice periods make it easy to miss the non-renewal window."),
        PlaybookRule(org_id=ws.principal.org_id, name="Approved governing law", rule_type=PlaybookRuleType.REQUIRE_GOVERNING_LAW,
                     params={"allowed": ["Delaware", "New York", "England"]}, severity=Severity.MEDIUM),
        PlaybookRule(org_id=ws.principal.org_id, name="Confidentiality clause required", rule_type=PlaybookRuleType.REQUIRE_CLAUSE, clause_type=ClauseType.CONFIDENTIALITY,
                     severity=Severity.LOW),
        PlaybookRule(org_id=ws.principal.org_id, name="No unlimited liability wording", rule_type=PlaybookRuleType.FORBID_PHRASE, params={"phrases": ["without limit"]}, severity=Severity.HIGH),
    ]
    for r in rules:
        ws.admin.save_rule(r)


def _human_activity(ws: WorkspaceContext, today: date, report: SeedReport) -> None:
    """Simulate a few real human actions so the screens show owners, completions, confirmations and events."""
    try:
        priya = ws.admin.add_member("priya.nair@demo.contractlens.local", Role.CONTRACT_MANAGER)
        marcus = ws.admin.add_member("marcus.lee@demo.contractlens.local", Role.MEMBER)
        ws.admin.add_member("legal.reviewer@demo.contractlens.local", Role.LEGAL_REVIEWER)
        ws.integrations.save(IntegrationProvider.CALENDAR_ICS, "Calendar export (.ics)", enabled=True, requires_confirmation=True)
        ws.integrations.save(IntegrationProvider.WEBHOOK, "Ticketing webhook", config={"url": "https://hooks.example.invalid/contractlens", "allowed_hosts": ["hooks.example.invalid"]},
                             enabled=False, requires_confirmation=True)
    except ContractLensError as exc:
        report.notes.append(f"Team setup skipped: {exc.user_message}")
        return
    contracts = {c.title: c for c in ws.repos.contracts.list()}
    msa = next((c for t, c in contracts.items() if "Master Services" in t), None)
    saas = next((c for t, c in contracts.items() if "SaaS" in t or "Helios" in t), None)
    rows = ws.obligations.list(today=today)
    owners = [priya.user_id, marcus.user_id, ws.principal.user_id]
    for i, r in enumerate(rows):
        if i % 3 != 2:  # leave a third unassigned so the "unassigned" view is meaningful
            ws.obligations.assign_owner(r.obligation.id, owners[i % 3])
    if msa:
        by_title = {r.obligation.title: r for r in rows if r.obligation.contract_id == msa.id}
        done = by_title.get("Deliver certificate of insurance")
        if done:
            ws.obligations.complete(done.obligation.id, "Certificate of insurance received by e-mail 2025-01-22 (ref COI-2025-0117).")
        maint = by_title.get("Maintain liability insurance")
        if maint:
            ws.obligations.mark_reviewed(maint.obligation.id)
        try:
            ws.deadlines.record_event(msa.id, EventType.INVOICE_RECEIVED, "Invoice INV-2026-0912", today - timedelta(days=10), "September services invoice")
        except ContractLensError:
            pass
        notice = next((d for d in ws.repos.deadlines.list([F.eq("contract_id", str(msa.id)), F.eq("category", DeadlineCategory.RENEWAL_NOTICE)])), None)
        if notice and notice.due_date:
            ws.deadlines.confirm(notice.id, "Verified against clause 2.2 by Contracts team")
        ws.contracts.approve(msa.id)
    if saas:
        indem = next((r for r in rows if r.obligation.contract_id == saas.id and r.obligation.title == "Indemnify Provider"), None)
        if indem:
            ws.obligations.flag_dispute(indem.obligation.id, "Legal is negotiating a cap on this indemnity; do not treat as agreed.")
            ws.obligations.add_note(indem.obligation.id, "Raised with Helios on the last call; awaiting redline.", NoteKind.NOTE)
    nda = next((c for t, c in contracts.items() if "NDA" in t), None)
    if nda:
        ws.contracts.approve(nda.id)
    _ = ObligationStatus, ContractStatus
