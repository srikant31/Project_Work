-- Migration 003: staged publication and immutable ingestion provenance.
-- Apply after migration 002, using the database owner/migration role.
-- This migration does not contact Chroma and does not repair legacy content.
-- Legacy rows keep NULL identity, hashes, offsets and extraction snapshots:
-- their old files may have changed, so guessing these values would be unsafe.
-- A duplicate chunk-index preflight aborts the entire transaction for review.
-- Re-running this migration is supported.

BEGIN;

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_key TEXT,
    ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS text_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS pipeline_fingerprint VARCHAR(64),
    ADD COLUMN IF NOT EXISTS pipeline_config JSONB,
    ADD COLUMN IF NOT EXISTS extracted_text TEXT,
    ADD COLUMN IF NOT EXISTS extraction_warnings JSONB,
    ADD COLUMN IF NOT EXISTS cleanup_pending BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE documentchunks
    ADD COLUMN IF NOT EXISTS start_char INT,
    ADD COLUMN IF NOT EXISTS end_char INT;

-- The initial schema used this PostgreSQL-generated constraint name.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check
    CHECK (status IN ('staging','active','deactivated','failed'));
ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'staging';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM documentchunks
        GROUP BY document_id, chunk_index HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration 003 stopped: duplicate document/chunk_index rows require review; no data was deleted.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM documents
        WHERE source_key IS NOT NULL AND status = 'active'
        GROUP BY category_id, source_key HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration 003 stopped: multiple active versions for one source require review; no version was deactivated.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM documents
        WHERE source_key IS NOT NULL
        GROUP BY category_id, source_key, version HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration 003 stopped: duplicate source/version rows require review; no provenance was changed.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_source
    ON documents(category_id, source_key)
    WHERE status = 'active' AND source_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_source_version
    ON documents(category_id, source_key, version)
    WHERE source_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_documentchunks_document_index
    ON documentchunks(document_id, chunk_index);

COMMIT;
