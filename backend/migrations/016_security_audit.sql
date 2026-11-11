-- Журнал скачиваний и выгрузок, предупреждения безопасности (docs/operations/02 «security_alerts», 04 §7; этап 3а).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
-- События скачиваний пишутся в существующую security_events (014): новых колонок там не нужно.
-- Антивирусная проверка файлов (attachment_scans) — этап 3б, отдельный файл 016b (D-045).
BEGIN;
CREATE TABLE IF NOT EXISTS security_alerts (
    id                 VARCHAR(36)  PRIMARY KEY,
    rule_key           VARCHAR(48)  NOT NULL,
    rule_version       VARCHAR(16)  NOT NULL DEFAULT '',
    severity           VARCHAR(16)  NOT NULL,
    status             VARCHAR(16)  NOT NULL DEFAULT 'new',
    subject_user_id    VARCHAR(36),
    first_seen_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    occurrence_count   INTEGER      NOT NULL DEFAULT 1,
    summary            VARCHAR(300) NOT NULL DEFAULT '',
    details_sanitized  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    last_event_id      VARCHAR(36),
    acknowledged_by    VARCHAR(36),
    acknowledged_at    TIMESTAMPTZ,
    ack_comment        TEXT         NOT NULL DEFAULT '',
    resolved_by        VARCHAR(36),
    resolved_at        TIMESTAMPTZ,
    resolution_comment TEXT         NOT NULL DEFAULT '',
    CONSTRAINT ck_security_alert_status CHECK (status IN ('new', 'acknowledged', 'resolved')),
    CONSTRAINT ck_security_alert_severity CHECK (severity IN ('low', 'medium', 'high', 'critical'))
);
CREATE INDEX IF NOT EXISTS ix_security_alerts_status_seen ON security_alerts (status, last_seen_at);
CREATE INDEX IF NOT EXISTS ix_security_alerts_subject_user_id ON security_alerts (subject_user_id);
-- Одна открытая запись на правило и сотрудника (02: dedup по rule_key + subject + open status); после resolved — новая
CREATE UNIQUE INDEX IF NOT EXISTS uq_security_alert_open ON security_alerts (rule_key, COALESCE(subject_user_id, ''))
    WHERE status <> 'resolved';
COMMIT;
