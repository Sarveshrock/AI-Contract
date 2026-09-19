# ContractLens Enterprise — Architecture

ContractLens turns contracts into a searchable, monitored, evidence-grounded "digital twin".
It assists legal, procurement and finance professionals. **It does not provide legal advice and
never replaces legal review.** Every AI-derived item carries a source reference and a review state.

## 1. System architecture

```
┌──────────────────────────────── PyQt6 desktop client ─────────────────────────────────┐
│  UI (screens, components, theme)  ──signals──▶  Workers (QThreadPool)                  │
│            │                                          │                                │
│            ▼                                          ▼                                │
│  WorkspaceContext (per signed-in Principal)  ── services ── agents ── tools ── rag     │
└───────┬───────────────────────┬──────────────────────┬───────────────────┬────────────┘
        │ user JWT (anon key)   │ user JWT             │ vectors           │ LLM / embeddings
        ▼                       ▼                      ▼                   ▼
  Supabase Auth          Supabase Postgres       ChromaDB             OpenAI API
  Supabase Storage       (RLS = authority)       (persistent | http)  (direct, or via the
                                                                       `openai-proxy` Edge Function
                                                                       so no OpenAI key is on desktops)
```

Layering rules (enforced by package boundaries):

| Layer | Package | May depend on |
|---|---|---|
| UI | `app.ui` | services, models, workers |
| Services (business logic) | `app.services` | repositories, agents, rag, tools, security |
| Agents (LLM orchestration) | `app.agents` | tools, rag, schemas, security |
| Retrieval | `app.rag` | vector store + embedder interfaces |
| Persistence | `app.repositories`, `app.database` | models |
| Security | `app.security` | models |

No UI code touches the database, and no agent writes to the database directly. Agents return
structured results; a narrow `AnalysisPersistence` service writes them.

### Two runtime modes

* **Supabase mode** (production): `SUPABASE_URL` + `SUPABASE_ANON_KEY` are set. Users sign in with
  Supabase Auth. All table access uses the *user's* JWT, so Postgres Row Level Security is the
  authority for tenant isolation.
* **Local demo mode** (no Supabase configured): SQLite + local file storage + a single local demo
  principal. The UI shows a permanent **DEMO / LOCAL MODE** badge. Same repositories, same
  services, same schema (generated from the same models). There is no service-role key anywhere;
  the app refuses to start if the configured key is a `service_role` JWT.

## 2. Technology decisions

| Concern | Decision | Rationale / trade-off |
|---|---|---|
| UI | PyQt6, Model/View, QSS design tokens, QSvg icons | Native performance; one token module drives QSS, charts and icons. |
| Charts | PyQtGraph for line/bar; custom QPainter widgets for donut, heatmap, risk matrix | Small, fast, no web engine. |
| Concurrency | `QThreadPool` + `QRunnable` (`app.workers`) | Results marshalled to the UI thread through signals. |
| Backend | Supabase (Postgres, Auth, Storage, RLS) | Server-side authorisation; client only holds the anon key + user JWT. |
| Data access | `TableStore` protocol with `SupabaseTableStore` and `SqliteTableStore` | Repositories are written once. Tests and demo mode run without a server. |
| Schema | Pydantic table models are the single source of truth; SQL migrations and SQLite DDL are generated from them (`scripts/generate_migrations.py`) and a test fails on drift | No hand-maintained duplicate schemas. |
| Vector DB | ChromaDB behind `VectorStore` | See below. |
| LLM | OpenAI structured outputs (`chat.completions.parse`) with Pydantic schemas | Schema-validated; refusals and parse failures are explicit errors. |
| Embeddings | OpenAI `text-embedding-3-small`; offline `HashingEmbedder` fallback | The fallback is lexical-quality only and is labelled in the UI. |
| Dates | Deterministic Python engine (`app.tools.temporal`) | **LLMs extract terms, never compute dates.** |
| OCR | PyMuPDF page rendering + Tesseract (`pytesseract`) | Requires the Tesseract binary; absence is a clear, non-silent error. |

### ChromaDB deployment model

Chroma is *not* a managed distributed database. Two supported modes:

1. **Persistent (embedded)** — default. One index per machine under `data/chroma`. Right for a single
   user or a demo. Not shared between users; concurrent writers from several machines are not safe.
2. **HTTP client** — a Chroma server behind TLS with token auth (`CHROMA_MODE=http`). Right for
   teams that need a shared index. The operator owns backup, upgrades and capacity.

Isolation is layered: **one collection per organisation** (`cl_<org_uuid>`), *and* every query
carries an `organization_id` metadata filter, *and* the retriever verifies the organisation on every
returned hit. Chroma is a derived index: Postgres (`document_chunks`) is authoritative, and the
index can be rebuilt from it (`IndexingRecovery`). Embedder name and dimension are recorded in the
collection metadata; a mismatch raises an error asking for re-indexing rather than mixing vector
spaces.

### Supabase ↔ ChromaDB relationship

| Supabase (authoritative) | Chroma metadata (derived) |
|---|---|
| `documents.id`, `documents.file_hash` | `document_id`, `document_hash` |
| `document_chunks.id` (deterministic UUIDv5 of hash+index) | vector id and `chunk_id` |
| `contracts.id`, `contract_versions.id`, `organizations.id` | `contract_id`, `contract_version_id`, `organization_id` |
| `document_chunks.section_reference`, `page_number`, `clause_type` | same names |
| `document_chunks.text_hash` | `source_text_reference` (`<document_id>#<char_start>-<char_end>`) |

`evidence.chunk_id` points at `document_chunks`, and each evidence row stores a verbatim quote with
its offsets and hash, so evidence survives loss of the vector index.

## 3. Database schema (summary)

24 tables, all UUID-keyed, with `created_at`/`updated_at` (trigger-maintained), enum `CHECK`
constraints, and an `org_id` on every tenant table. See `migrations/` (generated) for the full DDL.

```
organizations ─┬─ organization_members ── profiles (auth.users)
               ├─ contracts ─┬─ contract_versions ─┬─ documents ── document_chunks
               │             │                     ├─ clauses ── obligations ─┬─ deadlines
               │             │                     │                          ├─ obligation_dependencies
               │             │                     │                          └─ obligation_notes
               │             ├─ contract_parties ── parties
               │             ├─ events, amendments
               │             └─ alerts
               ├─ evidence (→ any subject), analysis_runs ── analysis_findings ── review_cases
               ├─ playbook_rules, integrations
               └─ audit_logs (append-only)
```

Tenant integrity is enforced twice: RLS policies (`has_org_role(org_id, roles)`) and *composite
foreign keys* `(parent_id, org_id)` so a row can never reference a parent from another
organisation. Row changes on sensitive tables are recorded by an audit trigger; semantic events
(login, AI queries, external actions) are recorded by `AuditService`.

## 4. Agent workflow

```
Supervisor ── classifies task ─▶ WorkflowPlan (steps, dependencies, required/optional)
   │
   ├─ INGEST_ANALYSIS: DocumentIntelligence → ContractExtraction → ObligationIntelligence
   │                   → TemporalReasoning → AmendmentIntelligence* → RiskTriage → EvidenceQA → persist
   ├─ QUESTION:        intent router → structured tool | hybrid retrieval → GroundedCopilot → EvidenceQA
   ├─ COMPARE_VERSIONS: AmendmentIntelligence → EvidenceQA
   └─ REFRESH_DEADLINES: TemporalReasoning → RiskTriage
```

* Agents declare capabilities; `ToolPolicy` authorises each capability per agent and per principal.
* Contract text is untrusted: it is delimited, scanned (`PromptGuard`), and never influences the plan
  or tool selection. Extraction calls have **no tools**; the plan comes from an enum-constrained task.
* Every quote returned by a model is verified against the source chunk. Failures become
  `unsupported_claim` findings and review cases; they are never stored as verified evidence.
* Quality below the configured threshold routes the run to `needs_review`.
* Runs, steps, token usage and warnings are logged in `analysis_runs` (visible in *Agent Runs*).

## 5. UI design system

Dark "command centre" theme, defined once in `app/ui/theme/tokens.py` and compiled to QSS
(`qss.py`): deep navy surfaces, electric-cyan primary, violet/indigo secondary, emerald/amber/rose
states, 1px hairline borders, minimal glow (focus and primary actions only). Motion (`motion.py`)
is limited to screen fades, panel slides, skeleton shimmer and progress; a **reduced-motion**
setting disables all of it. All interactive widgets are keyboard reachable with visible focus rings.

Reusable components: GlassPanel, MetricCard, NeonButton, StatusBadge, SearchBar, ContractCard,
ObligationCard, EvidencePanel, AgentStatusPanel, TimelineItem, DataTable, EmptyState, LoadingState,
ErrorState, ConfirmationDialog, ReviewPanel, plus charts (Donut, Bars, Heatmap, RiskMatrix, Trend).

## 6. Security model

* Supabase Auth + RLS; no privileged keys on the client; refuses `service_role` keys.
* Role → permission map (`app.security.access`) checked in services *and* enforced by RLS.
* Uploads: extension + magic-byte + size + page limits + hash; duplicate detection by SHA-256.
* Prompt-injection defence: delimiting, pattern detection surfaced as findings, no tools in extraction.
* External actions (`.ics` export, webhook) go through `ToolExecutor`: allowlisted tool, enabled
  integration, permission, explicit confirmation, *approved* source data only, audit entry.
* Logs are redacted (keys, tokens, emails); contract text is never logged.
* Configurable retention (`organizations.retention_days`) with a purge service.

## 7. Known limitations

See `docs/TEST_REPORT.md` for the verified/unverified matrix. Notable: live Supabase and live
OpenAI paths cannot be exercised in CI; Realtime subscriptions are not used (polling instead);
OCR needs a local Tesseract install; DOCX has no physical pages, so page numbers are logical.
