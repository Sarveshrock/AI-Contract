# Test report

Run: `python -m pytest` on Windows 10, Python 3.12.1, PyQt6 6.11 (offscreen platform for UI tests).
Result at the time of writing: **164 passed, 0 failed** (about 2.5 minutes). All tests run without network access.

| File | Tests | Covers |
|---|---|---|
| `test_database.py` | 14 | type round-trips, filters, upsert idempotency, transactions, cascade, **organisation isolation (read/write)**, audit-trigger emulation, permissions, Supabase filter translation, **migration drift check**, RLS present on every table |
| `test_documents.py` | 19 | **PDF and DOCX extraction, page preservation, OCR fallback**, scanned-without-OCR error, malformed/encrypted files, upload validation (magic bytes, macros, launch actions), hashing, **duplicate detection**, sections, exact-slice chunks, end-to-end ingestion, failed-ingestion clean-up, versions, **interrupted-job recovery** |
| `test_rag.py` | 14 | **retrieval quality baseline (hit@5 ≥ 0.9, MRR ≥ 0.6)**, contract/version/clause filters, **cross-organisation retrieval blocked even on a shared vector store**, embedder-mismatch guard, SQLite vector store, BM25, context fencing |
| `test_temporal.py` | 21 | explicit/ambiguous dates, month-end clamping, business days and holidays, **notice windows**, **missing anchors stay unresolved**, event-triggered, **recurring** (no drift), timezone-aware due times, **amendment-driven date shifts** |
| `test_pipeline.py` | 17 | full agent pipeline on the sample contracts: dates, parties, obligations, **evidence verified against source text**, **fabricated quote caught by QA and routed to review**, auto-renewal roll-forward, risk scores split into business vs extraction, idempotent re-analysis that **preserves human edits**, run logging, **AI-unavailable fails cleanly**, supervisor retries, **prompt injection flagged and ineffective**, version comparison, **amendments never applied without human approval**, tool-policy and workflow allowlists |
| `test_services.py` | 31 | demo seeding, obligation ledger (**completion needs human evidence**), dependencies (cycle rejection), deadline confirm/override, alert idempotency/escalation, Renewal Radar, **Copilot** (routing, structured answers, extractive mode, **refusal without evidence**, **fabricated citations withheld**, injection in the question), **cross-organisation Copilot isolation**, admin role rules, playbook deviations, **external actions need enabled integration + confirmation + verified data + audit**, retention purge, risk-config re-scoring |
| `test_security.py` | 28 | **service-role key refused**, secrets not in repr/logs, redaction, prompt-injection patterns (positive and benign), hidden characters, Supabase store/auth adapters with fakes, retry on transient errors, OpenAI adapter failure modes (refusal, truncation, auth, rate limit), proxy client uses the user JWT, embedder batching, **all LLM schemas satisfy OpenAI strict mode**, Chroma self-test caching and fallback, embedder-switch → re-index flow, SQL hardening and storage-policy checks, Edge Function key handling |
| `test_ui_smoke.py` | 8 | every screen loads against demo data with **no exception in any slot or paint event**, navigation parameters, workspace opens with viewer/clauses/evidence, Copilot screen answers, empty-workspace state, high-contrast + reduced-motion toggles, read-only role, login dialog |
| `test_ui_bootstrap.py` | 1 | every navigation entry maps to a real screen (no placeholders) |

Retrieval evaluation (`python scripts/eval_retrieval.py`, offline hashing embedder, 12 golden questions over 3 contracts):
hit@5 = 1.00, MRR = 0.88. This is the *floor*; OpenAI embeddings should do better and the same script measures them.

## What was verified visually
Screens were rendered with the native Windows Qt platform at 1400×860 (including a 1366×768-class display) and inspected:
Command Center, Contract Intelligence (list and five-pane workspace), Obligation Operations, Renewal Radar, Risk Observatory,
Evidence Explorer. Layout was tuned for small laptop screens (scrollable regions, resizable splitters, no forced horizontal scroll).

## Limitations — what is NOT verified
These need your infrastructure and were **not** exercised against live services:

1. **Live Supabase.** Auth, PostgREST calls, Storage and RLS were tested with fakes and by static SQL checks
   (all five migrations parse with `pglast`; RLS coverage and composite tenant foreign keys are asserted).
   Apply the migrations to a real project and run the checklist in `docs/DEPLOYMENT.md` before trusting isolation.
2. **Live OpenAI.** The adapters, schemas (strict-mode compatible), retry and error mapping are tested; real model
   quality is not. Extraction/QA quality on your contracts must be evaluated with your own documents.
   The demo pipeline uses hand-authored fixtures in place of the model (clearly labelled in the UI).
3. **ChromaDB engine on this machine.** Chroma 1.5.9 and 0.6.3 both crashed the process with a native access
   violation on write here (cause not diagnosed). The app therefore runs a start-up self-test in a child process
   and falls back to a local SQLite vector index behind the same `VectorStore` interface. The Chroma code path
   (`ChromaVectorStore`) is covered only by filter-translation tests; validate it on your target machine
   (`python -c "from app.rag.factory import probe_embedded_chroma"`) or use `CHROMA_MODE=http`.
4. **OCR.** Tesseract is not installed on the test machine; OCR is tested with a deterministic fake engine and the
   "unavailable" error path. Real OCR accuracy is unmeasured.
5. **Realtime subscriptions** are not used (polling every 60 s); the migration adds the tables to the publication for a future switch.
6. **DOCX pages** are logical (about 3,000 characters) unless the file carries page-break markers.
7. **Concurrency**: Supabase writes from the desktop are not multi-statement transactions; multi-step analysis
   writes are compensated on failure (rows created by the run are removed). SQLite mode is fully transactional.
8. **Accessibility**: keyboard navigation, focus rings, accessible names, reduced motion and a high-contrast
   theme are implemented; no screen-reader audit was performed.
9. **Packaging** (`ContractLens.spec`) was written but not built here.
