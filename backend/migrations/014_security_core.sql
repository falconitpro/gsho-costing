-- Безопасность: сессии на сервере, TOTP, история паролей, события безопасности, отключение сотрудника
-- (docs/operations/02 §5, 04; D-021: в пакете — 011; D-032: security_events перенесены сюда из 012).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_must_change BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS offboarded_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS offboarded_by VARCHAR(36);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS needs_reassignment BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_tasks_assignee_reassign ON tasks (assignee_id, needs_reassignment);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id                  VARCHAR(36)  PRIMARY KEY,
    sid                 VARCHAR(36)  NOT NULL UNIQUE,
    user_id             VARCHAR(36)  NOT NULL REFERENCES users (id),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ,
    idle_expires_at     TIMESTAMPTZ  NOT NULL,
    absolute_expires_at TIMESTAMPTZ  NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revoked_by          VARCHAR(36),
    revoke_reason       VARCHAR(16)  NOT NULL DEFAULT '',
    remember            BOOLEAN      NOT NULL DEFAULT FALSE,
    auth_method         VARCHAR(16)  NOT NULL DEFAULT 'password',
    mfa_level           VARCHAR(16)  NOT NULL DEFAULT 'none',
    last_mfa_at         TIMESTAMPTZ,
    last_step_up_at     TIMESTAMPTZ,
    ip_prefix           VARCHAR(64)  NOT NULL DEFAULT '',
    user_agent_short    VARCHAR(120) NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_last_seen_at ON auth_sessions (last_seen_at);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_active ON auth_sessions (user_id, revoked_at, absolute_expires_at);

CREATE TABLE IF NOT EXISTS mfa_credentials (
    id                        VARCHAR(36) PRIMARY KEY,
    user_id                   VARCHAR(36) NOT NULL UNIQUE REFERENCES users (id),
    secret_ciphertext         TEXT        NOT NULL DEFAULT '',
    key_id                    VARCHAR(16) NOT NULL DEFAULT '',
    enabled_at                TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at              TIMESTAMPTZ,
    last_used_step            BIGINT      NOT NULL DEFAULT 0,
    failed_attempts           INTEGER     NOT NULL DEFAULT 0,
    locked_until              TIMESTAMPTZ,
    pending_secret_ciphertext TEXT        NOT NULL DEFAULT '',
    pending_key_id            VARCHAR(16) NOT NULL DEFAULT '',
    pending_expires_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    id              VARCHAR(36)  PRIMARY KEY,
    user_id         VARCHAR(36)  NOT NULL REFERENCES users (id),
    code_hash       VARCHAR(255) NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    used_at         TIMESTAMPTZ,
    used_session_id VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_mfa_recovery_codes_user_id ON mfa_recovery_codes (user_id);

CREATE TABLE IF NOT EXISTS mfa_challenges (
    id            VARCHAR(36) PRIMARY KEY,
    user_id       VARCHAR(36) NOT NULL REFERENCES users (id),
    verifier_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at    TIMESTAMPTZ NOT NULL,
    attempts      INTEGER     NOT NULL DEFAULT 0,
    consumed_at   TIMESTAMPTZ,
    remember      BOOLEAN     NOT NULL DEFAULT FALSE,
    auth_method   VARCHAR(16) NOT NULL DEFAULT 'password',
    ip_prefix     VARCHAR(64) NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mfa_challenges_user_id ON mfa_challenges (user_id);

CREATE TABLE IF NOT EXISTS password_history (
    id            VARCHAR(36)  PRIMARY KEY,
    user_id       VARCHAR(36)  NOT NULL REFERENCES users (id),
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_password_history UNIQUE (user_id, password_hash)
);
CREATE INDEX IF NOT EXISTS ix_password_history_user_id ON password_history (user_id);

CREATE TABLE IF NOT EXISTS security_events (
    id              VARCHAR(36)  PRIMARY KEY,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actor_user_id   VARCHAR(36),
    subject_user_id VARCHAR(36),
    event_type      VARCHAR(32)  NOT NULL,
    action          VARCHAR(48)  NOT NULL,
    result          VARCHAR(16)  NOT NULL DEFAULT 'success',
    request_id      VARCHAR(36)  NOT NULL DEFAULT '',
    session_id      VARCHAR(36),
    ip_prefix       VARCHAR(64)  NOT NULL DEFAULT '',
    entity_type     VARCHAR(32)  NOT NULL DEFAULT '',
    entity_id       VARCHAR(128) NOT NULL DEFAULT '',
    deal_id         VARCHAR(36),
    format          VARCHAR(16)  NOT NULL DEFAULT '',
    row_count       INTEGER,
    byte_size       BIGINT,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    risk_score      INTEGER,
    alert_id        VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_security_events_occurred_at ON security_events (occurred_at);
CREATE INDEX IF NOT EXISTS ix_security_events_deal_id ON security_events (deal_id);
CREATE INDEX IF NOT EXISTS ix_security_events_actor_type_at ON security_events (actor_user_id, event_type, occurred_at);
CREATE INDEX IF NOT EXISTS ix_security_events_subject_at ON security_events (subject_user_id, occurred_at);

-- события безопасности только добавляются (как audit_log в 001)
CREATE OR REPLACE FUNCTION security_events_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'security_events is append-only';
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_security_events_immutable ON security_events;
CREATE TRIGGER trg_security_events_immutable BEFORE UPDATE OR DELETE ON security_events FOR EACH ROW EXECUTE FUNCTION security_events_immutable();

COMMIT;
