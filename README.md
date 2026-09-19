# ContractLens Enterprise

AI contract intelligence and obligation operations for legal, procurement, finance and compliance teams.
Desktop app (Python 3.12, PyQt6) on Supabase, ChromaDB and OpenAI. **It assists professionals and does not give legal advice.**
Every AI-derived fact carries a verified source quote, and nothing consequential happens without a human decision.

## Quick start (local demo, no accounts needed)

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python main.py            # click "Enter demo workspace", then "Load demo data"
```
Demo mode is labelled in the UI. The five sample contracts (`data/samples`) run through the **real** parsing, indexing,
deadline, QA, risk and persistence code; only the LLM step is replaced by curated fixtures, and one fixture item is a
deliberately fabricated obligation so you can watch the QA agent catch it. Without `OPENAI_API_KEY` you cannot analyse *new*
uploads (they are indexed and searchable, and you get a clear message). Regenerate samples: `python scripts/generate_samples.py`.

Production setup: copy `.env.example` to `.env`, then follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## What it does

| Area | Highlights |
|---|---|
| **Command Center** | KPIs (active, renewals ≤ 90 d, pending, overdue, findings, review queue, processing), lifecycle/category/month/review charts, deadline calendar, alert centre, recent runs, insights |
| **Contract Intelligence** | PDF/DOCX upload (drag & drop), OCR fallback, five-pane workspace: metadata, clause explorer, document viewer with search and evidence highlighting, AI insights, evidence panel; versions, amendments, comparison |
| **Obligation Operations** | ledger with 9 views and grouping, owner assignment, dependencies (cycle-safe graph), notes, disputes, exceptions, **completion only with human evidence**, CSV export |
| **Renewal Radar** | horizon, notice deadlines, auto-renewal roll-forward with stated assumptions, calculation traces, confirm/override/handle, event recording, calendar, `.ics` export of *confirmed* deadlines |
| **Risk Observatory** | risk matrix, heatmap, trend, per-contract score explanation, **business risk kept separate from extraction uncertainty**, human review workbench, configurable weights, playbook deviations |
| **Evidence Explorer** | hybrid dense + BM25 retrieval with reranking, org-isolated; evidence ledger of verified quotes |
| **AI Copilot** | grounded answers with sources and uncertainty; deterministic tools for expiry/obligations/deadlines; **refuses when evidence is insufficient**; withholds answers whose citations fail verification |
| **Agent Runs** | live agent registry, run history, workflow plan, step timeline, warnings, token usage |
| **Integrations / Administration** | `.ics` and webhook (allowlisted, confirmed, audited), service health, members and roles, playbook, alert offsets, holidays, retention purge, audit log, index rebuild |

## Architecture in one page

```
PyQt6 UI ──▶ services ──▶ agents (Supervisor + 8) ──▶ tools / rag
   │            │
   │            └─▶ repositories ─▶ TableStore ─┬─ SupabaseTableStore (user JWT → RLS is the authority)
   └─ QThreadPool workers                        └─ SqliteTableStore  (local demo / tests)
```
- **Single source of truth for the schema**: Pydantic table models generate the Postgres migrations (with RLS and composite
  tenant foreign keys) *and* the local SQLite DDL; a test fails on drift.
- **Digital twin** (24 tables): contracts, versions, documents, chunks, parties, clauses, obligations, deadlines, events, evidence,
  analysis runs/findings, review cases, alerts, amendments, playbook rules, integrations, audit logs.
- **LLMs extract, Python computes**: deadlines come from a deterministic engine (`app/tools/temporal.py`) with traces, stated
  assumptions and *unresolved* status when an anchor date is missing. Ambiguous dates are never silently resolved.
- **Evidence integrity**: every model quote must appear verbatim in its source chunk; unverifiable quotes are never stored.
- **Human control**: computed deadlines are `pending_review` until a person confirms; amendments only change the contract
  after approval; AI cannot complete obligations; external actions need enabled integration + permission + confirmation + confirmed data.
- **Security**: Supabase Auth + RLS, no service-role key (refused at start-up), optional OpenAI Edge-Function proxy, upload
  validation and hashing, prompt-injection fencing/detection, tool capability policy, redacted logs, append-only audit, retention.

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Tests and limitations: [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Project layout

```
main.py  requirements*.txt  .env.example  pytest.ini  ContractLens.spec
migrations/          0001..0005 SQL (0002 and 0004 generated)
supabase/functions/  openai-proxy (Edge Function)
app/
  config/ core/ models/ schemas/ database/ repositories/ security/
  rag/               chunker, embeddings, vector stores (Chroma / SQLite / memory), hybrid retriever, indexer, evaluation
  tools/             parsers, OCR, normalisation, sections, temporal engine, diffing, ICS
  agents/            supervisor + document, extraction, obligation, temporal, amendment, risk, QA agents, grounded copilot
  services/          ingestion, analysis, persistence, obligations, deadlines, alerts, renewals, reviews, risk, copilot, admin, ...
  workers/           QThreadPool task runner
  ui/                theme (tokens, QSS, icons, motion), components, one package per screen
  demo/              sample texts, curated fixtures, seeder
scripts/  tests/  data/samples/  docs/
```

## Commands

```
python -m pytest                          # 164 tests, no network
python scripts/generate_migrations.py     # regenerate 0002/0004 after model changes (--check in CI)
python scripts/seed_demo.py [--remove]    # CLI demo data
python scripts/eval_retrieval.py          # retrieval quality on the samples
python scripts/generate_samples.py        # rebuild sample PDFs/DOCX (needs reportlab)
```

## Known limitations (details in the test report)

Live Supabase and live OpenAI paths are untested here; embedded ChromaDB crashed natively on the development machine, so the
app self-tests it and falls back to a SQLite vector index (use a Chroma server for teams); OCR needs a local Tesseract; DOCX
page numbers are logical; the UI polls instead of using Realtime.
