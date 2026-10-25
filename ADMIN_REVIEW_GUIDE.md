# Admin user and feedback review

The **User feedback** screen lets an administrator inspect who asked a saved
question, the exact answer they received, its citations and retained context,
and the owner's current thumbs rating. No live LLM call or vector search is
needed to review history. Employee conversation access remains owner-only.

## Open and use

Start the API using the root README instructions and sign in with an admin
account at http://127.0.0.1:8000/. Open **User feedback** in the sidebar.

1. Browse all saved turns, or search the user directory by email/department and
   select a user. The directory includes conversation counts, thumbs-down
   counts and last saved activity. It includes users with no conversations.
2. Select a conversation, or keep all conversations for that user. User,
   conversation and review lists have independent pagination.
3. Filter by thumbs up/down/not rated, response outcome, and saved question or
   answer text. Combined filters apply to the same turn.
4. Choose **Inspect response** to see full question/answer text, rating and
   feedback dates, citation quotes and offsets, and expandable context chunks.
   Chunks show rank within retained context, similarity, source category,
   document version, location and current online/publication state.
5. Inspect saved generation diagnostics when investigating a poor answer:
   model/provider, Top K, timing, prompt version/hash and grounding metadata
   are shown when recorded. Source-file links use existing authenticated file
   permissions. Admin inspection does not republish an offline source.

There is no user creation/editing/deletion or rating override on this screen.
The existing local account CLI remains separate from web administration.

## Exact turns and historical honesty

New assistant messages store `reply_to_message_id`, referencing the exact user
question in the same conversation. The repository checks message roles and
conversation identity. Dashboard reads validate the link again.

Older answers without an explicit valid link remain visible, with **Exact
question not recorded**. The application does not guess using timestamps or
message order. An unlinked question in a conversation containing historical
unlinked answers has an **unknown** outcome, because absence of a stored link
does not prove that the user received no answer.

**Unanswered / insufficient evidence** includes saved refusals and questions
with no response where historical ambiguity does not prevent that conclusion.
Disabled-provider requests, failed generation calls and context previews are
not invented conversation records and do not appear as unanswered questions.

New votes store UTC `created_at` and `updated_at`. Changing a vote preserves
creation time and updates the modification time. Historical missing dates stay
NULL and display **Not recorded**. This is the current rating plus timestamps;
written reasons are not collected. New submissions also create an atomic
`feedback.submitted` audit event with the current vote and valid record links,
so Activity provides a submission trail going forward. This review screen still
shows the current vote; historical changes predating the audit feature are not
reconstructed. Feedback remains
separate from reviewed golden evaluation labels.

## Evidence boundaries

- Context is the retained, bounded evidence recorded for the answer, rather
  than every raw retrieval candidate or original retrieval rank.
- Historical offline/deactivated documents remain inspectable by admins;
  availability labels describe their current state.
- Source categories describe documents, not automatically classified discussion
  topics. No discussion-topic classifier was added.
- Stored citation quotes are snapshots; chunk text is loaded from the referenced
  versioned chunk records. A source-change refusal can have no retained context.
- Full rendered prompts and raw provider payloads were not added to storage.
- Password hashes, sessions, tokens and arbitrary legacy configuration fields
  are excluded from the review responses. Transcript text remains the actual
  saved user/assistant content. React renders it as text, not raw HTML.

## API and project structure

All endpoints require the server-side `require_admin` dependency. Missing or
expired authentication returns 401; an employee receives 403. Reads use only
PostgreSQL, with stable pagination, UUID validation and bounded search/page sizes.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/admin/users` | Search/paginate users and activity counts |
| `GET /api/admin/conversations?user_id=...` | Paginate the selected user's conversations |
| `GET /api/admin/reviews` | Paginate/filter turns across users or within a selected conversation |
| `GET /api/admin/reviews/{message_id}` | Open a response or unmatched question with retained context |

Review filters are `user_id`, `conversation_id`, `feedback` (`all`, `up`, `down`,
`unrated`), `status` (`all`, `answered`, `unanswered`, `unknown`), `search`,
`question_from`, `question_to`, `limit` and `offset`. Question dates use inclusive
UTC calendar days and the actual question timestamp. Unlinked historical
answers with no question timestamp are excluded by an active question-date
filter. Page limits are 1–100; search is limited to 200 characters.

Aggregate analytics, category maintenance and the profile screen are documented
in [ADMIN_ANALYTICS_PROFILE_GUIDE.md](ADMIN_ANALYTICS_PROFILE_GUIDE.md).

| File | Responsibility |
| --- | --- |
| `backend/api/admin_reviews.py` | Authentication/role enforcement and HTTP query validation |
| `backend/services/admin_review_service.py` | Safe API serialization and UTC timestamps |
| `backend/repositories/admin_review_repository.py` | SQL review index, exact linkage, filters, counts and evidence joins |
| `backend/repositories/conversation_repository.py` | Validated turn-link persistence |
| `backend/repositories/feedback_repository.py` | Atomic vote upsert with creation/update times |
| `backend/services/rag_service.py` | Records each new answer's exact question reference |
| `backend/database/models.py` | Nullable turn-reference and feedback timestamp mappings |
| `backend/database/migrations/006_admin_review_dashboard.sql` | Additive repeatable schema upgrade |
| `frontend/src/components/UserFeedback.tsx` | Directory, filters, saved turns and evidence inspector |
| `frontend/src/reviewData.ts` | Truthful historical outcome/linkage labels |
| `frontend/src/api.ts`, `types.ts`, `App.tsx`, `styles.css` | Typed integration, navigation and responsive presentation |

## Database upgrade and verification

Use `python -m scripts.apply_ingestion_migrations` for existing installs. It
backs up the configured database before discovering/applying numbered SQL.
Migration006 adds three nullable columns without inventing historical values.
The live database was backed up to
`output/backups/before_helpdesk_migrations_20261023_165449.dump`; all existing
table row counts were preserved. The reflected schema is now 14 tables and
114 columns. Fresh installs include the fields in `main_schema.sql`.

Tests cover admin/employee boundaries, exact multi-turn links, combined filters,
legacy ambiguity, vote times, source evidence, metadata allowlists, pagination
and no model/vector initialization. Browser verification uses an isolated
PostgreSQL schema with clearly fictional seeded history; it does not create
production conversations or make LLM calls. Evidence is retained under
`output/admin_dashboard_validation/`.

Final verification: **273 backend tests passed**, **23 frontend tests passed**,
the TypeScript/Vite production build passed, and **12 isolated browser checks
passed**. The backend suite retains one existing Starlette/httpx deprecation
warning. Temporary browser history and files were removed after verification.
