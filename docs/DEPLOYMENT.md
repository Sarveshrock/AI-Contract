# Deployment guide

## 1. Supabase

1. Create a project. Note the **anon (publishable) key** — the desktop client never receives the service-role key.
2. Apply the migrations in order (`supabase db push`, or `psql -f` each file):
   `0001_extensions.sql`, `0002_tables.sql`, `0003_functions.sql`, `0004_rls.sql`, `0005_storage_realtime.sql`.
   `0002` and `0004` are **generated** from `app/models`; after changing a model run `python scripts/generate_migrations.py`
   (CI: `--check`).
3. Auth: enable e-mail/password (or your SSO provider). A `profiles` row is created automatically for every new user.
4. Create the first organisation while signed in as that user (SQL editor with their JWT, or any client):
   `select public.create_organization('Acme Legal', 'acme-legal');` — the caller becomes `owner`.
   Owners/admins then add existing users with `public.add_org_member_by_email` (Administration → Members).
5. Storage: the `contracts` bucket is created private by `0005`; object paths start with the organisation id and are protected by policies.
6. **Verify isolation before go-live** (two users in two organisations):
   - user A cannot `select` user B's `contracts`, `document_chunks`, `audit_logs`;
   - inserting a row with B's `org_id` fails; inserting a child row that references B's parent fails (composite FK);
   - a `viewer` cannot insert/update; a `member` cannot read `audit_logs` or `integrations`;
   - user A cannot download B's storage object by path.

## 2. OpenAI

Either put `OPENAI_API_KEY` in the desktop `.env` (development only), or deploy the proxy so no key exists on desktops:

```
supabase functions deploy openai-proxy
supabase secrets set OPENAI_API_KEY=sk-...
# desktop .env:
OPENAI_BASE_URL=https://<project-ref>.supabase.co/functions/v1/openai-proxy
```
The proxy authenticates the Supabase session, allowlists paths/models, caps output tokens and rate-limits per user. It logs metadata only.

## 3. ChromaDB

| Mode | Use when | Notes |
|---|---|---|
| `persistent` (default) | one user / demo | embedded index in `data/chroma`. A child-process self-test runs first; on failure the app uses the local SQLite index and says so. |
| `http` | teams | run a Chroma server behind TLS with token auth; set `CHROMA_HOST/PORT/SSL/TOKEN`. You own backup and capacity. |
| `memory` | tests | volatile |

One collection per organisation plus an `organization_id` filter on every query. The index is derived data: **Administration → System → Rebuild search index** recreates it from `document_chunks`. Changing `OPENAI_EMBEDDING_MODEL` requires a rebuild (the app detects the mismatch and refuses to mix vector spaces).

## 4. OCR

Install Tesseract (Windows: UB-Mannheim build) and set `TESSERACT_CMD` if it is not on `PATH`. Without it, scanned PDFs are rejected with an explanatory message.

## 5. Desktop app

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # fill in
python main.py
```
Windows package: `scripts\build_windows.ps1` (PyInstaller one-folder build). Ship `.env` separately; do not embed keys.

## 6. Operations

- Retention: Administration → Workspace settings (soft-deleted contracts are purged after N days; audit logs are never purged by clients).
- Backups: Supabase (database + storage) and, for `http` Chroma, the Chroma volume. The vector index can always be rebuilt.
- Logs: `data/logs/contractlens.log` (JSON, redacted; contract text is never logged).
- Upgrades: re-run `scripts/generate_migrations.py --check`, apply new migration files, restart clients.
