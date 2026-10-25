-- Preserve every existing message and vote. Historical answer/question links
-- and feedback timestamps cannot be reconstructed honestly, so remain NULL.
BEGIN;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_message_id UUID;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'messages'::regclass
          AND conname = 'messages_reply_to_message_id_fkey'
    ) THEN
        ALTER TABLE messages
            ADD CONSTRAINT messages_reply_to_message_id_fkey
            FOREIGN KEY (reply_to_message_id) REFERENCES messages(message_id);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_messages_reply_to_message_id
    ON messages(reply_to_message_id);

-- Do not add a now() default: that would invent dates for pre-existing votes.
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

COMMIT;
