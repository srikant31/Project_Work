-- Migration 002: make Documents.category_id mandatory
--
-- Run with:  psql -U postgres -d rag_db -f backend/database/migrations/002_mandatory_document_category.sql
--
-- Safe to re-run: category inserts are idempotent (ON CONFLICT), the backfill
-- only touches rows that are still NULL, and the ALTER is a no-op if the
-- column is already NOT NULL.

BEGIN;

-- Starter taxonomy — EDIT THIS LIST before running if it doesn't match your
-- real document set. This is a guess based on "DHTI IT helpdesk," not
-- something derived from your actual corpus, since nothing has been
-- categorized yet. 'Uncategorized' should stay regardless of what else
-- you change — it's the backfill target below.
INSERT INTO documentcategories (category_name, description) VALUES
    ('IT Support', 'Hardware, software, network, and account issues'),
    ('HR / Leave', 'Leave applications, attendance, personnel policy'),
    ('Asset Management', 'Issued equipment, asset tags, warranty and repair policy'),
    ('Faculty Administration', 'Course scheduling, faculty records, academic administration'),
    ('Uncategorized', 'Fallback for documents not yet assigned a specific category')
ON CONFLICT (category_name) DO NOTHING;

-- Backfill: any existing document with no category gets 'Uncategorized'
-- rather than being left to fail the NOT NULL constraint below.
UPDATE documents
SET category_id = (
    SELECT category_id FROM documentcategories WHERE category_name = 'Uncategorized'
)
WHERE category_id IS NULL;

-- Enforce going forward.
ALTER TABLE documents
    ALTER COLUMN category_id SET NOT NULL;

COMMIT;

-- Verify after running:
--   SELECT doc_name, category_id FROM documents WHERE category_id IS NULL;
--   -- should return 0 rows
