-- Preserve existing conversation/message/feedback data. No evaluation labels
-- or scores are derived from user feedback. Safe to apply more than once.
BEGIN;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS generation_metadata JSONB;
COMMIT;
