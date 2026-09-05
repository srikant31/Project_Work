-- ============================================================
-- MIGRATION: add password_hash, normalize doc_category
-- ============================================================

-- 1. Add password_hash to Users
-- Add nullable first since the table may already have rows without a hash
ALTER TABLE Users ADD COLUMN password_hash VARCHAR(255);

-- Backfill existing rows before enforcing NOT NULL — e.g. if you have
-- seed/test users, hash a temp password for them here, or update manually:
-- UPDATE Users SET password_hash = '<bcrypt/argon2 hash>' WHERE user_id = '...';

-- Once every row has a real hash, lock it down:
ALTER TABLE Users ALTER COLUMN password_hash SET NOT NULL;


-- 2. Create DocumentCategories
CREATE TABLE DocumentCategories (
    category_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT
);

-- 3. Backfill categories from whatever distinct strings already exist
INSERT INTO DocumentCategories (category_name)
SELECT DISTINCT doc_category FROM Documents
WHERE doc_category IS NOT NULL
ON CONFLICT (category_name) DO NOTHING;

-- 4. Add the FK column to Documents (nullable for now, to allow backfill)
ALTER TABLE Documents ADD COLUMN category_id UUID REFERENCES DocumentCategories(category_id);

-- 5. Populate category_id by matching the old string values
UPDATE Documents d
SET category_id = dc.category_id
FROM DocumentCategories dc
WHERE d.doc_category = dc.category_name;

-- 6. Drop the old string column now that category_id is populated
ALTER TABLE Documents DROP COLUMN doc_category;

-- 7. Index the new FK (matches your existing indexing pattern)
CREATE INDEX idx_documents_category_id ON Documents(category_id);