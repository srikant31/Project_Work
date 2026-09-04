# Building ERD 2 (Evaluation & Governance) in Postgres — full step-by-step

This covers the diagram you uploaded: Documents → EvaluationQuestions → EvaluationResults ← EvaluationTestRuns, plus Users → AuditLogs.
`Documents` and `Users` already exist from Schema 1 — this guide assumes that schema is already applied (from the earlier session). If it isn't yet, run that DDL first; every FK below depends on it.

---

## Step 1 — Confirm Postgres is running and you're connected

```bash
psql -U helpdesk_app -d helpdesk_db -h localhost
```
If this fails, Postgres isn't running or the user/db from the earlier setup doesn't exist yet — go back and run that setup first. Don't proceed until this connects cleanly.

## Step 2 — Confirm the tables this diagram depends on already exist

```sql
\dt
```
You must see `Users` and `Documents` in the list. If either is missing, stop here and create Schema 1 first — every table below has a hard FK dependency on these two.

## Step 3 — Create EvaluationQuestions

```sql
CREATE TABLE EvaluationQuestions (
    eval_question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text TEXT NOT NULL,
    expected_answer_text TEXT,
    expected_doc_id UUID REFERENCES Documents(doc_id),
    expected_page_number INT,
    category VARCHAR(100),
    difficulty VARCHAR(10) CHECK (difficulty IN ('easy','medium','hard')),
    created_at TIMESTAMP DEFAULT now()
);
```
Run it, then verify:
```sql
\d EvaluationQuestions
```
Confirm `expected_doc_id` shows as a foreign key referencing `documents(doc_id)` in the output — if it doesn't, the constraint silently failed and you need to re-run it.

## Step 4 — Create EvaluationTestRuns

This table has no FK dependencies — it's independent, safe to create any time:
```sql
CREATE TABLE EvaluationTestRuns (
    test_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_name VARCHAR(255),
    chunking_config JSONB,
    top_k_config INT,
    embedding_model_config VARCHAR(100),
    llm_config JSONB,
    run_at TIMESTAMP DEFAULT now()
);
```
```sql
\d EvaluationTestRuns
```

## Step 5 — Create EvaluationResults (depends on both tables above)

Must be created after Steps 3 and 4 — it has FKs into both:
```sql
CREATE TABLE EvaluationResults (
    eval_result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_question_id UUID NOT NULL REFERENCES EvaluationQuestions(eval_question_id),
    test_run_id UUID NOT NULL REFERENCES EvaluationTestRuns(test_run_id),
    retrieved_chunks JSONB,
    generated_answer TEXT,
    retrieval_hit BOOLEAN,
    grounded BOOLEAN,
    hallucination_detected BOOLEAN,
    answer_correct BOOLEAN,
    retrieval_precision FLOAT,
    retrieval_recall FLOAT,
    response_time_ms INT,
    evaluated_at TIMESTAMP DEFAULT now()
);
```
```sql
\d EvaluationResults
```
Confirm both `eval_question_id` and `test_run_id` show as foreign keys.

## Step 6 — Create AuditLogs (depends only on Users)

```sql
CREATE TABLE AuditLogs (
    audit_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES Users(user_id),
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);
```
Note: `entity_id` has no FK constraint — this is intentional, not a mistake. It's a polymorphic reference (points at a different table depending on `entity_type`), which Postgres can't enforce with a single FK. This gets validated in application code instead.

```sql
\d AuditLogs
```

## Step 7 — Add indexes (do this now, not later)

Every FK column that you'll query/filter on regularly needs an index — Postgres does not create these automatically:
```sql
CREATE INDEX idx_evalquestions_expected_doc ON EvaluationQuestions(expected_doc_id);
CREATE INDEX idx_evalresults_question_id ON EvaluationResults(eval_question_id);
CREATE INDEX idx_evalresults_testrun_id ON EvaluationResults(test_run_id);
CREATE INDEX idx_auditlogs_user_id ON AuditLogs(user_id);
CREATE INDEX idx_auditlogs_entity ON AuditLogs(entity_type, entity_id);
```
The last one (`entity_type, entity_id` composite) matters specifically because your dashboard/admin screens will filter audit logs by "show me all actions on this document" — that's exactly this pair.

## Step 8 — Grant privileges to the app user (if not already covered by earlier `ALTER DEFAULT PRIVILEGES`)

If you already ran `ALTER DEFAULT PRIVILEGES` from the earlier setup, new tables should already be covered — verify:
```sql
\dp EvaluationQuestions
```
You should see `helpdesk_app` with `arwd` (all CRUD) privileges. If not, grant manually:
```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON EvaluationQuestions, EvaluationTestRuns, EvaluationResults, AuditLogs TO helpdesk_app;
```

## Step 9 — Verify structure end-to-end

```sql
\dt
```
Confirm all four new tables appear alongside the Schema 1 tables.

```sql
\d EvaluationResults
```
Manually check every column name and type against the diagram field-by-field — this is the point where a typo (wrong type, wrong nullable) is cheapest to catch.

## Step 10 — Prove the constraints actually work (don't skip this)

Insert real test data and confirm both the happy path and the failure path:

```sql
-- Happy path: seed one row through the whole chain
INSERT INTO Users (email, role) VALUES ('mentor@dhti.test', 'admin');

INSERT INTO Documents (doc_name, doc_type, doc_path)
VALUES ('Leave_Policy.pdf', 'pdf', '/docs/leave_policy.pdf');

INSERT INTO EvaluationQuestions (question_text, expected_doc_id, difficulty)
VALUES ('How many days of casual leave are permitted?',
        (SELECT doc_id FROM Documents WHERE doc_name = 'Leave_Policy.pdf'),
        'easy');

INSERT INTO EvaluationTestRuns (run_name, top_k_config)
VALUES ('baseline_run_1', 5);

INSERT INTO EvaluationResults (eval_question_id, test_run_id, retrieval_hit, grounded, answer_correct)
VALUES (
    (SELECT eval_question_id FROM EvaluationQuestions LIMIT 1),
    (SELECT test_run_id FROM EvaluationTestRuns LIMIT 1),
    true, true, true
);

INSERT INTO AuditLogs (user_id, action, entity_type, entity_id)
VALUES (
    (SELECT user_id FROM Users LIMIT 1),
    'upload', 'Document',
    (SELECT doc_id FROM Documents LIMIT 1)
);
```
All five inserts should succeed. Confirm with:
```sql
SELECT eq.question_text, er.retrieval_hit, er.answer_correct
FROM EvaluationResults er
JOIN EvaluationQuestions eq ON er.eval_question_id = eq.eval_question_id;
```

```sql
-- Failure path: this MUST fail — proves the FK is real, not decorative
INSERT INTO EvaluationResults (eval_question_id, test_run_id)
VALUES (gen_random_uuid(), (SELECT test_run_id FROM EvaluationTestRuns LIMIT 1));
```
Expect: `ERROR: insert or update on table "evaluationresults" violates foreign key constraint`. If this succeeds instead of failing, something is wrong with the schema — go back and re-check Step 5.

## Step 11 — Generate a live diagram and compare against your design

```bash
schemaspy -t pgsql -db helpdesk_db -u helpdesk_app -p yourpassword -host localhost -o schemaspy_output/
```
Open the generated diagram, compare table-by-table against the image you uploaded. Any mismatch here is cheap to fix now, expensive to fix after application code depends on it.

---

## Exit checklist for this ERD before moving to application code
- [ ] All 4 tables created, visible in `\dt`
- [ ] Every FK confirmed via `\d <table>` output
- [ ] All 5 indexes created
- [ ] `helpdesk_app` has correct privileges
- [ ] Happy-path insert chain succeeds end-to-end
- [ ] Failure-path insert correctly rejected
- [ ] SchemaSpy diagram matches the uploaded design

Once every box above is checked, this ERD is genuinely done — not just "tables exist," but proven to enforce the rules it was designed for.
