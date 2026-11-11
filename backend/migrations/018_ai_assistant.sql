-- Помощник CRM (docs/operations/02 §8, 06; этап 6). Только чтение; по умолчанию без внешней модели (D-055).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
BEGIN;
CREATE TABLE IF NOT EXISTS ai_conversations (
    id              VARCHAR(36)  PRIMARY KEY,
    owner_id        VARCHAR(36)  NOT NULL REFERENCES users (id),
    title           VARCHAR(120) NOT NULL DEFAULT '',
    default_mode    VARCHAR(16)  NOT NULL DEFAULT 'crm',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_ai_conversations_owner_last ON ai_conversations (owner_id, last_message_at);

CREATE TABLE IF NOT EXISTS ai_messages (
    id                VARCHAR(36) PRIMARY KEY,
    conversation_id   VARCHAR(36) NOT NULL REFERENCES ai_conversations (id),
    role              VARCHAR(16) NOT NULL,
    mode              VARCHAR(16) NOT NULL DEFAULT 'crm',
    content           TEXT        NOT NULL DEFAULT '',
    status            VARCHAR(16) NOT NULL DEFAULT 'complete',
    provider          VARCHAR(32) NOT NULL DEFAULT 'none',
    model             VARCHAR(64) NOT NULL DEFAULT '',
    sources           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    warnings          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    facts             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    request_id        VARCHAR(40) NOT NULL DEFAULT '',
    client_request_id VARCHAR(64),
    reply_to_id       VARCHAR(36),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    purged_at         TIMESTAMPTZ,
    CONSTRAINT uq_ai_message_request UNIQUE (conversation_id, client_request_id),
    CONSTRAINT ck_ai_message_role CHECK (role IN ('user', 'assistant', 'system_notice')),
    CONSTRAINT ck_ai_message_status CHECK (status IN ('complete', 'fallback', 'error'))
);
CREATE INDEX IF NOT EXISTS ix_ai_messages_conversation_created ON ai_messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS ai_tool_audits (
    id                  VARCHAR(36) PRIMARY KEY,
    conversation_id     VARCHAR(36),
    message_id          VARCHAR(36),
    actor_user_id       VARCHAR(36) NOT NULL,
    tool_name           VARCHAR(48) NOT NULL,
    arguments_sanitized JSONB       NOT NULL DEFAULT '{}'::jsonb,
    result_status       VARCHAR(16) NOT NULL DEFAULT 'ok',
    result_count        INTEGER     NOT NULL DEFAULT 0,
    entity_ids          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    duration_ms         INTEGER     NOT NULL DEFAULT 0,
    error_code          VARCHAR(32) NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_tool_audits_conversation_id ON ai_tool_audits (conversation_id);
CREATE INDEX IF NOT EXISTS ix_ai_tool_audits_actor_user_id ON ai_tool_audits (actor_user_id);

CREATE TABLE IF NOT EXISTS ai_feedback (
    id         VARCHAR(36) PRIMARY KEY,
    message_id VARCHAR(36) NOT NULL REFERENCES ai_messages (id),
    user_id    VARCHAR(36) NOT NULL REFERENCES users (id),
    rating     VARCHAR(16) NOT NULL,
    comment    TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ai_feedback UNIQUE (message_id, user_id),
    CONSTRAINT ck_ai_feedback_rating CHECK (rating IN ('positive', 'negative'))
);

CREATE TABLE IF NOT EXISTS ai_usage_daily (
    user_id           VARCHAR(36) NOT NULL,
    usage_date        DATE        NOT NULL,
    request_count     INTEGER     NOT NULL DEFAULT 0,
    token_in          INTEGER     NOT NULL DEFAULT 0,
    token_out         INTEGER     NOT NULL DEFAULT 0,
    minute_started_at TIMESTAMPTZ,
    minute_count      INTEGER     NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, usage_date)
);
COMMIT;
