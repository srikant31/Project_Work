# Helpdesk knowledge workspace

Local React + TypeScript interface for the existing ingestion and retrieval backend.
It uses real API data and cookie sessions. No simulated answers or progress are displayed.
The Assistant shows provider availability honestly; generation stays disabled
until the backend reports a configured provider. Context preview works without
calling a provider.

## Run locally

Use Node.js 24 and npm. From this directory:

```powershell
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Start the backend separately on
`http://127.0.0.1:8000` using the repository's backend instructions. Vite proxies
`/api` to that loopback address. Both services must be running. Sign in with an
account created by the backend administrator; this frontend does not create or
embed default credentials.

Use `127.0.0.1` consistently for local browser access. Requests carry the
`X-Requested-With: helpdesk-ui` header and same-origin cookies. Session tokens
are managed by HttpOnly server cookies, never localStorage.

## Checks

```powershell
npm run build
npm test
```

The build checks TypeScript and creates `dist/`. Dependencies are pinned with a
included `package-lock.json`; `node_modules/` and `dist/` are ignored.

`npm run preview` serves the compiled frontend locally. It does not proxy the
API; use the normal Vite development server for the two-process local workflow,
or serve the built files with the backend's configured static-file setup.

## Screens

- **Knowledge base (admin):** actual document totals, search/category filtering,
  name/category/type/status filtering, upload, availability, source-file download, replacement, reindexing, extracted
  chunks, source offsets, warnings, version history and recorded configuration.
- **Retrieval lab (all signed-in roles):** semantic evidence search across
  eligible online documents. Scores are labeled similarity. Results include
  source version, physical PDF page or section, and stored character offsets.
- **Activity (admin):** recorded server audit events.
- **Assistant (all signed-in roles):** inspect retrieved context, exact prompt
  messages and source references before generation is configured. Configured
  responses show validated source quotations/citations or an evidence refusal.
  Context budgets/counts are shown as UTF-8 bytes when that is the server's
  accounting method. Feedback is thumbs up/down only.
- **Experiments (admin):** choose online managed sources, import/edit reviewed
  question JSON, compare chunk/overlap/top-K settings using temporary indexes,
  inspect actual retrieval metrics and download the report. Applying chunk
  settings is explicit, sequential and reports each document's resulting
  version. Top K remains a query setting. Feedback ratings do not become gold
  relevance labels or formal evaluation scores. Successful application updates
  matching document IDs in the editable question draft; the completed report
  retains its original corpus and labels. Invalid drafts are preserved with an
  actionable warning. Refreshing sources clears unavailable selections and
  asks for label review rather than remapping externally changed content.

Administrative navigation is hidden for employees; the backend remains the
authority for role and document-access checks. Errors from the API are presented
in the relevant screen. Expired sessions return to sign-in.

## Source map

| File | Responsibility |
| --- | --- |
| `src/api.ts` | Typed fetch client, cookie/CSRF request settings and API errors |
| `src/types.ts` | API response contracts |
| `src/validation.ts` | Empty-file and 20 MiB checks before upload/replacement |
| `src/App.tsx` | Session initialization, role-aware navigation and logout |
| `src/components/Login.tsx` | Account sign-in |
| `src/components/KnowledgeBase.tsx` | Document table, filters, metrics and upload |
| `src/components/DocumentDrawer.tsx` | Evidence inspection and source maintenance |
| `src/components/RetrievalLab.tsx` | Question form and evidence results |
| `src/components/Activity.tsx` | Audit-event listing |
| `src/components/Assistant.tsx` | Provider availability, context preview, grounded responses and thumbs feedback |
| `src/components/Experiments.tsx` | Gold JSON editor, corpus/configuration comparison and explicit per-document application |
| `src/components/UserFeedback.tsx` | Admin user/conversation selection, turn filters and evidence/feedback inspection |
| `src/components/Analytics.tsx` | UTC usage/current-feedback aggregates, daily counts, FAQs and source usage |
| `src/components/Categories.tsx` | Category creation, rename/description updates and unused-only removal |
| `src/components/Profile.tsx` | Own account, owned activity and current session expiry for both roles |
| `src/adminData.ts` | Strict calendar/version validation, query construction and UTC display |
| `src/adminCompletion.css` | Responsive layouts and scrollable navigation for the added screens |
| `src/reviewData.ts` | Review outcome/linkage labels and pagination summaries |
| `src/experimentData.ts` | Gold-question parsing and immutable remapping of editable labels after explicit application |
| `src/components/Shared.tsx` | Reusable notices, statuses and loading states |
| `src/styles.css` | Responsive layout, visual styles and reduced-motion support |
| `vite.config.ts` | Loopback server, fixed development port and `/api` proxy |

`npm test` uses Node's built-in test runner against the API client with mocked
transport and the upload, experiment and review helpers. Tests never call a
model provider or mutate application stores. `tests/api.test.mjs` covers cookie,
header, error/session behavior and admin URL construction; `reviewData.test.mjs`
covers truthful historical linkage/outcome labels and pagination summaries.
`adminData.test.mjs` and the added client tests cover calendar boundaries,
date/version query construction and category/profile request behavior.

Admins can open **User feedback** to select a user and conversation, filter
saved turns by rating/outcome/text, and inspect exact answers, citations,
retained chunks and feedback timestamps. Source categories are document
metadata; no discussion-topic classification is implied. See the root
[ADMIN_REVIEW_GUIDE.md](../ADMIN_REVIEW_GUIDE.md) for behavior and limitations.
Admins also have **Analytics** and **Categories**, while both roles have
**Your profile**. Knowledge base adds inclusive UTC upload dates and version
filters; User feedback adds question dates. Matching historical versions are
inspectable with mutation controls disabled. See
[ADMIN_ANALYTICS_PROFILE_GUIDE.md](../ADMIN_ANALYTICS_PROFILE_GUIDE.md).
