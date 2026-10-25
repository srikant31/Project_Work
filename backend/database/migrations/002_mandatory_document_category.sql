-- Migration 002: make Documents.category_id mandatory
--
-- Run with:  psql -U postgres -d rag_db -f backend/database/migrations/002_mandatory_document_category.sql
--
-- Safe to re-run: initialize an empty taxonomy, preserve admin-managed labels,
-- and create the backfill target only when uncategorized legacy rows need it.
-- Category inserts are idempotent (ON CONFLICT), the backfill
-- only touches rows that are still NULL, and the ALTER is a no-op if the
-- column is already NOT NULL.

BEGIN;

-- Starter taxonomy — EDIT THIS LIST before running if it doesn't match your
-- real document set. This is a guess based on "DHTI IT helpdesk," not
-- something derived from your actual corpus, since nothing has been
-- categorized yet. 'Uncategorized' should stay regardless of what else
-- you change — it's the backfill target below when NULL legacy rows exist.
-- Do not resurrect starter categories after an administrator renames/removes
-- them. Later runs retain the maintained non-empty taxonomy.
DO $seed_taxonomy$
DECLARE
    was_maintained BOOLEAN := false;
BEGIN
    -- An intentionally emptied taxonomy is not a fresh install. Existing audit
    -- evidence avoids introducing a separate schema or initialization table.
    -- auditlogs need not exist yet when upgrading a pre-console installation.
    IF to_regclass('auditlogs') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM auditlogs WHERE entity_type = ''category''
                 AND action IN (''category.created'', ''category.updated'', ''category.deleted''))'
        INTO was_maintained;
    END IF;
    IF NOT was_maintained THEN
INSERT INTO documentcategories (category_name, description)
SELECT starter.category_name, starter.description
FROM (VALUES
    ('IT Support', 'Hardware, software, network, and account issues'),
    ('HR / Leave', 'Leave applications, attendance, personnel policy'),
    ('Asset Management', 'Issued equipment, asset tags, warranty and repair policy'),
    ('Faculty Administration', 'Course scheduling, faculty records, academic administration'),
    ('Uncategorized', 'Fallback for documents not yet assigned a specific category')
) AS starter(category_name, description)
WHERE NOT EXISTS (SELECT 1 FROM documentcategories)
ON CONFLICT (category_name) DO NOTHING;
    END IF;
END $seed_taxonomy$;

INSERT INTO documentcategories (category_name, description)
SELECT 'Uncategorized', 'Fallback for documents not yet assigned a specific category'
WHERE EXISTS (SELECT 1 FROM documents WHERE category_id IS NULL)
  AND NOT EXISTS (SELECT 1 FROM documentcategories WHERE lower(category_name) = lower('Uncategorized'))
ON CONFLICT (category_name) DO NOTHING;

-- Backfill: any existing document with no category gets 'Uncategorized'
-- rather than being left to fail the NOT NULL constraint below.
UPDATE documents
SET category_id = (
    SELECT category_id FROM documentcategories WHERE lower(category_name) = lower('Uncategorized')
    ORDER BY category_id LIMIT 1
)
WHERE category_id IS NULL;

-- Enforce going forward.
ALTER TABLE documents
    ALTER COLUMN category_id SET NOT NULL;

COMMIT;

-- Verify after running:
--   SELECT doc_name, category_id FROM documents WHERE category_id IS NULL;
--   -- should return 0 rows
