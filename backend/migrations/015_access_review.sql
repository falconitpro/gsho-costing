-- Пересмотр прав (docs/operations/02 «Access review», 04 §6; D-042: в пакете входил в 011, отдельным файлом — этап 2б).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
BEGIN;
CREATE TABLE IF NOT EXISTS access_review_campaigns (
    id              VARCHAR(36)  PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    period_start    DATE         NOT NULL,
    period_end      DATE         NOT NULL,
    due_date        DATE         NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'open',
    scope           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by      VARCHAR(36),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    completed_by    VARCHAR(36),
    completion_note TEXT         NOT NULL DEFAULT '',
    CONSTRAINT ck_access_review_campaign_status CHECK (status IN ('draft', 'open', 'completed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_access_review_campaigns_due_date ON access_review_campaigns (due_date);

CREATE TABLE IF NOT EXISTS access_review_items (
    id           VARCHAR(36) PRIMARY KEY,
    campaign_id  VARCHAR(36) NOT NULL REFERENCES access_review_campaigns (id),
    user_id      VARCHAR(36) NOT NULL REFERENCES users (id),
    snapshot     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    decision     VARCHAR(16) NOT NULL DEFAULT 'pending',
    reviewer_id  VARCHAR(36),
    reviewed_at  TIMESTAMPTZ,
    comment      TEXT        NOT NULL DEFAULT '',
    changes      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_access_review_item UNIQUE (campaign_id, user_id),
    CONSTRAINT ck_access_review_item_decision CHECK (decision IN ('pending', 'keep', 'change', 'disable', 'skipped'))
);
CREATE INDEX IF NOT EXISTS ix_access_review_items_campaign_id ON access_review_items (campaign_id);
CREATE INDEX IF NOT EXISTS ix_access_review_items_user_id ON access_review_items (user_id);
COMMIT;
