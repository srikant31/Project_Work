-- Migration 004: revocable admin-console sessions, visibility and audit details.
-- Apply after 003 with the migration/owner role. Safe to run more than once.
-- Existing passwords, roles, documents, chunks and vector references are retained.
-- Application role names remain the existing lowercase admin/employee values.
BEGIN;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_online BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE auditlogs ADD COLUMN IF NOT EXISTS details JSONB;

CREATE TABLE IF NOT EXISTS authsessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE NOT NULL
        CONSTRAINT authsessions_token_hash_length CHECK (length(token_hash) = 64),
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_authsessions_user_id ON authsessions(user_id);
CREATE INDEX IF NOT EXISTS idx_authsessions_expires_at ON authsessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_auditlogs_created_at ON auditlogs(created_at);

-- main_schema.sql grants these privileges for new installs. Explicitly grant
-- the new table on upgrades when that standard application role exists.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='helpdesk_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON authsessions TO helpdesk_app;
    END IF;
END $$;

COMMIT;
