-- Поддержка внутри CRM (docs/operations/02 §7, 05; этап 4). Только «в системе», без почты.
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
-- Вложения обращений — своя таблица (D-051), а не attachments: там deal_id обязателен и доступ идёт через сделку.
BEGIN;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS link VARCHAR(300);

CREATE TABLE IF NOT EXISTS support_number_sequences (
    year       INTEGER PRIMARY KEY,
    next_value INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id                VARCHAR(36)  PRIMARY KEY,
    number            VARCHAR(16)  NOT NULL,
    reporter_id       VARCHAR(36)  NOT NULL REFERENCES users (id),
    assignee_id       VARCHAR(36)  REFERENCES users (id),
    category          VARCHAR(16)  NOT NULL,
    requested_urgency VARCHAR(16)  NOT NULL DEFAULT 'normal',
    priority          VARCHAR(16)  NOT NULL DEFAULT 'normal',
    subject           VARCHAR(200) NOT NULL,
    description       TEXT         NOT NULL,
    current_route     VARCHAR(300) NOT NULL DEFAULT '',
    app_commit        VARCHAR(40)  NOT NULL DEFAULT '',
    correlation_id    VARCHAR(40)  NOT NULL DEFAULT '',
    client_metadata   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status            VARCHAR(20)  NOT NULL DEFAULT 'new',
    idempotency_key   VARCHAR(64),
    first_response_at TIMESTAMPTZ,
    resolved_at       TIMESTAMPTZ,
    closed_at         TIMESTAMPTZ,
    last_activity_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    reopened_from_id  VARCHAR(36),
    CONSTRAINT support_tickets_number_key UNIQUE (number),
    CONSTRAINT uq_support_idempotency UNIQUE (reporter_id, idempotency_key),
    CONSTRAINT ck_support_category CHECK (category IN ('error', 'question', 'access', 'improvement')),
    CONSTRAINT ck_support_urgency CHECK (requested_urgency IN ('normal', 'high', 'critical')),
    CONSTRAINT ck_support_priority CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    CONSTRAINT ck_support_status CHECK (status IN ('new', 'in_progress', 'waiting_reporter', 'resolved', 'closed'))
);
CREATE INDEX IF NOT EXISTS ix_support_reporter_activity ON support_tickets (reporter_id, last_activity_at);
CREATE INDEX IF NOT EXISTS ix_support_status_priority_activity ON support_tickets (status, priority, last_activity_at);
CREATE INDEX IF NOT EXISTS ix_support_assignee_status ON support_tickets (assignee_id, status);

CREATE TABLE IF NOT EXISTS support_messages (
    id         VARCHAR(36) PRIMARY KEY,
    ticket_id  VARCHAR(36) NOT NULL REFERENCES support_tickets (id),
    author_id  VARCHAR(36) REFERENCES users (id),
    kind       VARCHAR(16) NOT NULL,
    body       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_support_message_kind CHECK (kind IN ('user', 'specialist', 'system'))
);
CREATE INDEX IF NOT EXISTS ix_support_messages_ticket_id ON support_messages (ticket_id);
-- Сообщения неизменяемы (05 §5): правка и удаление запрещены на уровне БД
CREATE OR REPLACE FUNCTION support_messages_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'support_messages: сообщения обращений не изменяются и не удаляются';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_support_messages_immutable ON support_messages;
CREATE TRIGGER trg_support_messages_immutable BEFORE UPDATE OR DELETE ON support_messages FOR EACH ROW EXECUTE FUNCTION support_messages_immutable();

CREATE TABLE IF NOT EXISTS support_attachments (
    id           VARCHAR(36)   PRIMARY KEY,
    ticket_id    VARCHAR(36)   NOT NULL REFERENCES support_tickets (id),
    message_id   VARCHAR(36)   REFERENCES support_messages (id),
    file_name    VARCHAR(255)  NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    sha256       VARCHAR(64)   NOT NULL,
    size         BIGINT        NOT NULL DEFAULT 0,
    scan_status  VARCHAR(20)   NOT NULL DEFAULT 'legacy_unscanned',
    scan_id      VARCHAR(36),
    uploaded_by  VARCHAR(36)   REFERENCES users (id),
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_support_attachments_ticket_id ON support_attachments (ticket_id);
COMMIT;
