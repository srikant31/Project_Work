# AI Helpdesk knowledge workspace

See the [internship requirements and progress comparison](docs/reports/README.md)
for a downloadable side-by-side review of all 49 specification sections, weekly
milestones, implemented features and remaining acceptance gates.

The implemented slice loads TXT, Markdown, text-layer PDF and DOCX documents,
preserves source evidence, chunks with the embedding model's tokenizer, and
publishes versioned evidence to PostgreSQL and Chroma. Search returns source
passages. A FastAPI API and React admin console now add login, role checks,
document upload/replacement/reindexing, online/offline availability and audit
history. The grounded-answer provider interface, context preview, thumbs
feedback, comparative chunking experiments, admin user/feedback review,
aggregate analytics, category maintenance and own-profile screens are
implemented. Admin document and review lists support UTC date/version filters;
feedback submissions and category changes are audited. Real LLM provider calls and the approved golden evaluation dataset
remain pending. See [ADMIN_CONSOLE_GUIDE.md](ADMIN_CONSOLE_GUIDE.md) for corpus
management, [ADMIN_REVIEW_GUIDE.md](ADMIN_REVIEW_GUIDE.md) for user reviews and
[ADMIN_ANALYTICS_PROFILE_GUIDE.md](ADMIN_ANALYTICS_PROFILE_GUIDE.md) for analytics,
profile, categories, filters and audit behavior.

## Local setup

Run commands from the project root in PowerShell. The existing `venv` already
contains the pinned dependencies. For a fresh virtual environment, install
`requirements_utf8.txt`. Both requirements files now use UTF-8 and the same pins.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements_utf8.txt
```

Provide `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD` in the ignored
`.env` file. The configured database and ingestion tables must exist. Existing
installations should apply migrations; do not rerun the full schema over an
existing database. New installations use `backend/database/main_schema.sql`,
with the application role provisioned first, and migration 002 for categories.

```powershell
.\venv\Scripts\python.exe -m scripts.apply_ingestion_migrations
```

This backs up the configured database with `pg_dump` before applying additive
migrations, currently 002 through 006. `pg_dump` must be on PATH or beside `psql`. Backups,
preview artifacts and validation logs stay under ignored `output/`.
Connections use UTC; historical timestamps and unknown legacy source metadata
are preserved rather than reconstructed.

The default embedding model is `all-MiniLM-L6-v2`, loaded from the local cache.
The model must already be available, or its initial download must be explicitly
requested with the preview/ingestion CLI's `--allow-model-download` option.

## Start the admin console

The local admin account is `admin@helpdesk.local`. Its generated password is
stored privately in `output/local_admin_credentials.json` in this workspace;
it is not embedded in the frontend or committed to Git.

For a new installation, create an account after applying the migrations:

```powershell
.\venv\Scripts\python.exe -m scripts.create_user --email admin@helpdesk.local --role admin
```

The command prompts privately for a password. Add `--role employee` with a
different email to create an employee account. It refuses to overwrite existing
accounts. There is no public registration or browser role selector.

New accounts use Argon2id password hashes (64 MiB, 3 passes, 4 lanes).
Existing bcrypt accounts upgrade automatically after a successful login;
failed logins do not change hashes. New passwords support 10–256 characters.
Authentication uses eight-hour revocable HttpOnly cookie sessions backed by
PostgreSQL; it does not use JWT. No schema change or password reset is required
for the hash migration.

Build the React app once, then start the API from the project root:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open [the local workspace](http://127.0.0.1:8000). FastAPI serves the built React
files and `/api` together. During frontend development, run
`npm --prefix frontend run dev` in a second terminal and open
[the Vite development server](http://127.0.0.1:5173). Its `/api` proxy uses the
backend on port 8000. Use `127.0.0.1` consistently for cookies.

Admin: upload a source, inspect its chunks and pipeline, replace/reindex it,
toggle availability, compare chunking experiments, review activity, or open
**User feedback** to inspect users' saved conversations, exact question/answer
pairs, citations, retained chunks and ratings. Both roles can use Retrieval lab
and Assistant context preview; generation requires a configured provider.
Admins also have **Analytics** and **Categories**. Both roles have **Your profile**,
which displays their own account, current session expiry and saved activity.
**Offline** means excluded from retrieval while retained for administration;
**online** means eligible for retrieval after successful indexing. These flags
do not fetch website URLs. Replacements preserve the current availability.

Uploads accept PDF, DOCX, TXT and Markdown up to 20 MiB. Files are stored with
server-generated names under ignored `data/uploads/`. Availability, versions,
users, revocable sessions and audit events are stored in PostgreSQL; embeddings
remain in Chroma. For HTTPS deployments set `AUTH_COOKIE_SECURE=true` and
configure `FRONTEND_ORIGINS`; the supplied launch commands bind to loopback.

## Preview extraction and chunks without database writes

```powershell
.\venv\Scripts\python.exe -m scripts.preview_documents --folder data/documents --output output/preview.json
.\venv\Scripts\python.exe -m scripts.ingest_folder --folder data/documents --dry-run
```

The sample folder deliberately includes an empty PDF and an unsupported XYZ
file. Those two inputs produce expected failures/rejections and exit code 1;
the four supported fixtures preview successfully. `sample_scanned_like.pdf` is
actually blank; generated test fixtures separately cover real image-only and
mixed scanned/text PDFs. Use a folder of approved, valid documents for an
all-successful ingestion run. No document text is sent to an external LLM.

## Ingest and search

```powershell
.\venv\Scripts\python.exe -m scripts.ingest_folder --folder data/documents --category-name "IT Support" --output output/ingestion.json
.\venv\Scripts\python.exe -m scripts.search_demo "How do I connect to VPN?" --top-k 3
.\venv\Scripts\python.exe -m scripts.verify_stores
```

Folder sources use category plus relative file path, including extension, as
their identity. Moving a corpus root keeps relative identities stable. Direct
`process_file()` callers default to a normalized absolute path; supply an
explicit portable `source_key` for an evaluation corpus.

Unchanged source bytes, extracted text and pipeline fingerprint are skipped
only after the existing vectors are verified. Changed content, format or
configuration creates a new immutable version. Failed versions can consume
version numbers. Same-title documents with different source keys stay separate.

## Publication and recovery

1. Extract, chunk and validate the embedding output before creating a version.
2. Persist a staging document, inactive chunks and its processing job.
3. Write and verify all vectors while holding the source advisory lock.
4. In one PostgreSQL transaction, retire the predecessor, publish the new
   document/chunks and mark the job completed.
5. Clean predecessor vectors separately; keep history and extracted snapshots.

PostgreSQL document/chunk state controls retrieval eligibility. Staging/stale
vectors cannot become valid search results merely because they exist in Chroma.
The stores do not share a distributed transaction. An uncertain commit is
checked through a fresh connection; an unknown outcome is preserved for
reconciliation instead of triggering potentially destructive compensation.

```powershell
.\venv\Scripts\python.exe -m scripts.reconcile_stores
.\venv\Scripts\python.exe -m scripts.reconcile_stores --doc-id DOCUMENT_UUID --apply
```

Report mode changes no document/vector contents. Apply requires an explicit
document UUID. Recovery can retire stale stages, clean failed/obsolete vectors,
or restore missing active vectors from immutable stored chunks with the original
embedding configuration. Choose the original `--collection`/`--chroma-path`
when recovering a different index. An index with a changed model configuration
must be reindexed as a new document version. Recovery never resets a collection.

## Source and chunk contract

- SHA-256 of original file bytes and actual byte size are retained.
- Normalized extraction text is stored immutably with its own checksum.
- Chunk `[start_char:end_char]` is an exact substring of that normalized text.
- PDF page numbers are physical, 1-indexed and preserve blank-page gaps.
- DOCX/TXT/Markdown have no invented page number. Use headings/offsets instead.
- Default chunk size/overlap is 200/40 **model tokens**, not words.
- Stored `token_count` excludes special tokens. Every final model input,
  including special tokens, must fit the model's effective sequence limit.
- Paragraph/sentence boundaries are preferred; overlap stays within page/section.
- DOCX paragraphs and tables retain their original order.

Golden questions should anchor to source identity, version/checksum and evidence
spans. Chunk IDs are configuration-specific labels. Old rows with no captured
provenance remain legacy and require deliberate reingestion before gold grading.

## Tests

```powershell
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m pytest --run-integration -q
npm --prefix frontend test
npm --prefix frontend run build
```

Real-store tests are opt-in. They create unique PostgreSQL schemas and temporary
Chroma directories, then remove the schemas. They never ingest into public
tables or the normal `chroma_db`. The configured database role needs permission
to create test schemas. With integration requested, an unavailable database or
missing model fails rather than silently passing a mocked integration test.

## Current limits

OCR, complex PDF visual reading-order reconstruction and PDF image/table
recognition are outside this slice. Image-only PDF pages require OCR; mixed
text/image-only documents are rejected to avoid silently incomplete ingestion.
Images alongside extracted text can still contain information not extracted.
DOCX rendered pagination, headers, footers, text boxes and tracked revisions
are not extracted and generate an explicit limitation warning. Legacy encoding
fallback is reported. The console has backend-enforced roles, upload/request
size limits and browser origin/header checks. OCR, online URL crawling, account
management screens, answer generation and a formal evaluation dashboard are
not implemented. Indexing is synchronous for this small local console; a large
deployment would need a durable background worker and job progress endpoint.

See `LLM_HANDOFF.txt` for implementation decisions, validation results, live
database changes, import ownership and the next evaluation-dataset tasks.

## Realistic document validation

An additional validation run generated four fictional business policies: a
five-page PDF including a blank physical page, a three-page DOCX with an
interleaved SLA table, Markdown leave rules and a UTF-16 equipment handbook.
It authored 12 answerable questions and 3 unsupported questions before ranking.
Inputs, expected evidence, full results and visual previews are packaged in
`output/real_document_validation_bundle.zip` in this workspace.

```powershell
.\venv\Scripts\python.exe -m scripts.validate_real_documents --strict
```

The script accepts `--documents-dir`, `--manifest`, `--queries` and `--output`
for another corpus. It uses real isolated PostgreSQL/Chroma, checks authored
facts/source order/page numbers/whole-source coverage, rejects negative PDFs
without writes, verifies repeat-import skips and audits both stores.
For these four generated documents: 30 chunks, all checks passed, correct
document at rank 1 for 12/12 answerable questions and complete evidence within
the top 3 for 12/12. Complete evidence in the first passage was 9/12.
These are fixture-specific retrieval results, not an answer-generation or
production-corpus evaluation. The three unsupported questions are diagnostics
and are excluded from answerable retrieval hit rates.
