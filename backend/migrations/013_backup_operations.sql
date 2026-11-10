-- История резервного копирования в Yandex Object Storage (docs/operations/02 §4, 03; D-021: в пакете — 010). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-019, D-100): на стенде с APP_ENV=dev таблицу создаёт init_db(). Секретов и вывода команд таблица не хранит.
BEGIN;
CREATE TABLE IF NOT EXISTS backup_runs (
    id               VARCHAR(36)  PRIMARY KEY,
    run_key          VARCHAR(36)  NOT NULL UNIQUE,
    reason           VARCHAR(16)  NOT NULL,
    status           VARCHAR(16)  NOT NULL,
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    snapshot_id      VARCHAR(16),
    git_commit       VARCHAR(40),
    schema_revision  VARCHAR(64),
    storage_provider VARCHAR(16)  NOT NULL DEFAULT 'unknown',
    database_bytes   BIGINT,
    files_bytes      BIGINT,
    files_count      INTEGER,
    manifest_sha256  VARCHAR(64),
    check_status     VARCHAR(16)  NOT NULL DEFAULT '',
    checked_at       TIMESTAMPTZ,
    retention_status VARCHAR(16)  NOT NULL DEFAULT '',
    error_code       VARCHAR(32)  NOT NULL DEFAULT '',
    error_summary    VARCHAR(300) NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_backup_runs_reason CHECK (reason IN ('daily', 'pre_deploy', 'manual', 'restore_check')),
    CONSTRAINT ck_backup_runs_status CHECK (status IN ('running', 'success', 'failed'))
);
CREATE INDEX IF NOT EXISTS ix_backup_runs_status_started ON backup_runs (status, started_at);
CREATE INDEX IF NOT EXISTS ix_backup_runs_finished_at ON backup_runs (finished_at);
COMMIT;
