-- D-127: обсуждения сделки — сообщения, вопросы и ответы, закреплённое, разделы — в существующей ленте сделки (deal_feed).
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS section VARCHAR(16) NOT NULL DEFAULT '';
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS parent_id VARCHAR(36);
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS mentions JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS awaiting BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS due_on DATE;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(36);
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS pinned_by VARCHAR(36);
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS edit_history JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE deal_feed ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_deal_feed_parent_id ON deal_feed (parent_id);
CREATE INDEX IF NOT EXISTS ix_deal_feed_awaiting ON deal_feed (deal_id) WHERE awaiting AND resolved_at IS NULL AND deleted_at IS NULL;
-- поиск по тексту ленты (по-русски)
CREATE INDEX IF NOT EXISTS ix_deal_feed_fts ON deal_feed USING gin (to_tsvector('russian', coalesce(subject, '') || ' ' || coalesce(body, '')));
