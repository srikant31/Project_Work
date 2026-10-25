# Analytics, profile, categories, filters and feedback audit

Open http://127.0.0.1:8000/ and sign in. Admins have new **Analytics** and
**Categories** destinations. Both admins and employees have **Your profile**.
Existing login uses Argon2id and revocable eight-hour opaque cookie sessions.

## Analytics

The admin-only screen provides total saved questions, questions today/this week,
saved responses, current thumbs up/down/unrated counts, average recorded response
time, insufficient-evidence responses, explicit no-useful-retrieval outcomes,
unknown historical outcomes, daily activity, frequent questions, document usage
and examples of saved refusals. Empty data yields zero counts and unknown
latency, rather than a fabricated average. Definitions are expandable in the UI.

Choose **From (UTC)** and **Through (UTC)** and apply the reporting dates, or
choose **All dates**. All boundaries are inclusive UTC calendar days.

- Question metrics use the user message timestamp. Response/feedback/outcome/
  latency metrics use the assistant response timestamp.
- Feedback metrics are the current conversation owner's vote on responses in
  the selected cohort. They are not counts of historical vote submissions.
- Today is UTC today; the week starts Monday UTC. Both intersect the range.
- Average response time includes known nonnegative timings only. The report
  includes its denominator; missing timing is not treated as zero.
- Insufficient evidence is an explicit refusal flag, not a calibrated confidence
  score. Missing outcome metadata stays unknown.
- No useful retrieval requires the explicit reason `no_eligible_sources`.
  Missing legacy context or metadata is not automatically a retrieval failure.
- Document usage counts distinct retained response/document-version pairs.
  Multiple chunks from one document do not multiply its response usage count.
  This is not source-file download analytics.
- Frequent questions normalize case and whitespace. They do not cluster topics.
- Daily activity covers at most the latest 31 days within the selected range;
  summary totals still cover the entire selected period. Top lists have at
  most ten entries each.
- Only saved messages count. Context previews, Retrieval Lab searches and
  generation failures without a saved response are not invented question logs.

## Your profile

This dedicated read-only screen shows the signed-in user's email, role,
department, user ID, current session expiry in UTC and own lifetime conversation,
question, response and current-vote counts. It is available to both roles.
It does not expose password hashes or session tokens/digests, and cannot be
used to select another person's profile. Identity, role, department and password
editing are outside this basic profile screen.

## Category management

Admins can create categories, update their names/descriptions, and confirm
removal of categories with zero document-version references. Names are validated
and unique regardless of case. Descriptions can be cleared. Rename preserves
the category UUID and linked source identities; vectors are not rewritten.

Counts include all historical/failed/staging document versions, not just current
online sources. The UI disables deletion for in-use categories and the server
checks again. A concurrent foreign-key conflict returns a sanitized 409.
Concurrent category writes serialize, so case-variant duplicates cannot race
through application checks. Mutations and their audit events commit together.

No document category reassignment or forced deletion of referenced categories
was added. Existing upload choices fetch the maintained category list.

Migration002 now distinguishes fresh taxonomy initialization from a maintained
taxonomy, using existing category audit evidence when the list was intentionally
emptied. Replaying setup does not restore deleted/renamed starter labels. Legacy
NULL-category backfill still works and reuses an existing case-insensitive
`Uncategorized` category without inventing a duplicate.

## Date and version filters

**Knowledge base:** use Uploaded from/through (UTC) and Document version, then
**Apply dates/version**. Without these filters, the prior latest-source listing
is preserved. With any of them, the query searches stored versions, so historical
versions can be found. Upload date is each version's creation timestamp; summary
counts refer to returned versions. The existing name/category/type/status
filters combine with these controls. Historical deactivated/staging versions
are inspectable but cannot be replaced or reindexed in the inspector.

**User feedback:** use Question from/through (UTC) and **Apply question dates**.
Filters use the exact linked question's timestamp. An unmatched user question
uses its own timestamp; a legacy answer with no question link is excluded by
date filtering. No question time or question/answer relationship is guessed.

Invalid/reversed ranges and non-positive/non-integer versions are rejected.
APIs require `YYYY-MM-DD` calendar dates rather than Unix seconds or compact
date strings. Ranges include midnight at the start through the entire end day.

## Feedback audit

Every accepted vote submission records `feedback.submitted` with actor,
response ID, rating, conversation ID and a validated question ID if available.
Audit time is the submission time. Invalid historical question links stay NULL.
No question or answer text is copied into the audit details. Vote and event
commit atomically; audit failure rolls the vote back. Rejected/unauthorized
submissions create no vote or event. Changing a vote creates another submission
event while the feedback table retains one current rating.

Activity also shows category.created/updated/deleted events. Events preceding
these features are not backfilled or reconstructed.

## APIs and files

| API | Access and behavior |
| --- | --- |
| `GET /api/analytics?date_from=&date_to=` | Admin-only aggregate reporting |
| `GET /api/profile` | Current authenticated user and current owned session |
| `GET/POST /api/categories` | Admin category listing/creation |
| `PATCH/DELETE /api/categories/{id}` | Admin edit or unused-only deletion |
| `GET /api/documents?uploaded_from=&uploaded_to=&version=` | Admin document/version filtering |
| `GET /api/admin/reviews?question_from=&question_to=` | Admin question date filtering |

New analytics/profile/category APIs, services and repositories follow the
existing separation. `backend/utils/date_ranges.py` supplies strict UTC calendar
validation. `backend/models/category_models.py` validates category inputs.
Document/admin-review queries gained filters. FeedbackService and its repository
write safe audit links. `backend/main.py` registers the routers and permits
DELETE in the existing trusted-origin CORS policy.

Frontend components are Analytics.tsx, Categories.tsx and Profile.tsx, supported
by typed API contracts, adminData.ts helpers and adminCompletion.css. The existing
KnowledgeBase, UserFeedback and DocumentDrawer were extended. Metadata browsing,
analytics and profile/category reads do not initialize models or vector stores.

## Verification and current data

New real-PostgreSQL tests cover analytics denominators/cohorts, strict UTC date
boundaries, exact question dates, category concurrency/reference conflicts and
audit rollback, safe current-user profile/session handling, feedback audit and
taxonomy replay preservation. Existing software regressions are also run.

Browser validation uses a disposable schema with explicitly fictional history
and document versions. It covers the new screens, CRUD, filters, role isolation,
feedback audit visibility, current-vote analytics and mobile widths without LLM
calls. Test history is removed afterward; it is never placed in public tables.
Evidence is under `output/admin_completion_validation/`.

Final checks on 23 October 2026: **339 backend tests passed**, **34 frontend
tests passed**, and the TypeScript/Vite production build passed. The isolated
browser run passed 14 checks; a further six checks passed against the restarted
main app. Both runs reported zero page errors and zero chat/provider requests.
The disposable schema and temporary fixture files were removed.

The live backup is
`output/backups/before_helpdesk_migrations_20261023_184958.dump`. Setup replay
preserved all public table row counts, including two users, ten document
versions, seventeen chunks and six existing audit events. The live app was
restarted and checked at http://127.0.0.1:8000/. There were no saved conversations
or feedback records at verification time; empty charts are therefore expected.

No schema extension is required; the current database remains 14 tables and
114 columns. Real LLM provider calls and the formal approved golden evaluation
dataset remain deferred work. A live application with no saved conversations
correctly displays empty analytics.
