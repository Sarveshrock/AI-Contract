"""Offline rule-based analysis: real sample contracts through the real pipeline, no AI and no network."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.agents.offline_llm import RuleBasedLLM
from app.core.errors import AIUnavailableError
from app.schemas.llm_outputs import AnswerOut
from tests.helpers import make_workspace

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture()
def offline_ws(store, principal, tmp_path):
    return make_workspace(store, principal, tmp_path, llm=RuleBasedLLM())


def _analyse(ws, name: str):
    out = ws.contracts.upload(SAMPLES / name)
    assert out.analysis is not None, out.analysis_skipped_reason
    return ws.contracts.twin(out.ingestion.contract_id), out


def test_msa_facts_are_read_with_verified_evidence(offline_ws):
    twin, out = _analyse(offline_ws, "msa_v1.pdf")
    c = twin.contract
    assert out.analysis.error is None
    assert c.effective_date == date(2025, 1, 15) and c.auto_renews is True and c.renewal_notice_days == 90
    assert "Delaware" in (c.governing_law or "")
    names = {p.name for p, _cp in twin.parties}
    assert any("Acme" in n for n in names) and any("Northwind" in n for n in names)
    assert len(twin.clauses) >= 8 and twin.obligations


def test_every_obligation_has_a_quote_and_says_it_came_from_rules(offline_ws):
    twin, _ = _analyse(offline_ws, "saas_subscription.pdf")
    assert twin.obligations
    for ob in twin.obligations:
        assert ob.uncertainty and "offline" in ob.uncertainty.lower()
        assert any(True for _ in twin.evidence(__import__("app.models.enums", fromlist=["SubjectType"]).SubjectType.OBLIGATION, ob.id)), ob.title


def test_deadlines_are_computed_by_the_engine_not_the_rules(offline_ws):
    twin, _ = _analyse(offline_ws, "mutual_nda.pdf")
    assert twin.contract.effective_date is not None
    assert all(d.validation_status.value in ("pending_review", "needs_review", "unresolved", "assumed", "confirmed") for d in twin.deadlines)


def test_offline_llm_cannot_write_free_form_answers():
    with pytest.raises(AIUnavailableError):
        RuleBasedLLM().structured(system="s", user="u", schema=AnswerOut, purpose="grounded_qa")


def test_copilot_uses_retrieval_only_with_offline_rules(offline_ws):
    _analyse(offline_ws, "msa_v1.pdf")
    ans = offline_ws.copilot.ask("Which clauses mention service credits?")
    assert ans.mode == "extractive" and ans.sources


def test_analyse_pending_processes_documents_uploaded_without_analysis(offline_ws):
    out = offline_ws.contracts.upload(SAMPLES / "msa_v1.pdf", analyze=False)
    assert out.analysis is None
    results = offline_ws.contracts.analyze_pending()
    assert len(results) == 1 and results[0].error is None
    twin = offline_ws.contracts.twin(out.ingestion.contract_id)
    assert twin.contract.analysis_status.value in ("completed", "needs_review") and twin.obligations
