-- Проверка загружаемых файлов и класс данных базы (docs/operations/02 §6, 04 §8, §10; этап 3б, D-045, D-049, D-050).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
-- Уже загруженные файлы получают scan_status = 'legacy_unscanned': доступны по прежним правам, скачивание журналируется (FILE-04).
BEGIN;
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS scan_status VARCHAR(20) NOT NULL DEFAULT 'legacy_unscanned';
ALTER TABLE attachments ADD COLUMN IF NOT EXISTS scanned_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS attachment_scans (
    id                VARCHAR(36)   PRIMARY KEY,
    attachment_id     VARCHAR(36),
    quarantine_key    VARCHAR(64)   NOT NULL,
    source            VARCHAR(16)   NOT NULL DEFAULT 'upload',
    route             VARCHAR(160)  NOT NULL DEFAULT '',
    storage_path      VARCHAR(1024) NOT NULL DEFAULT '',
    file_name         VARCHAR(255)  NOT NULL DEFAULT '',
    status            VARCHAR(20)   NOT NULL DEFAULT 'pending',
    engine            VARCHAR(16)   NOT NULL DEFAULT 'clamav',
    engine_version    VARCHAR(32)   NOT NULL DEFAULT '',
    signature_version VARCHAR(32)   NOT NULL DEFAULT '',
    sha256            VARCHAR(64)   NOT NULL DEFAULT '',
    size              BIGINT        NOT NULL DEFAULT 0,
    detected_name     VARCHAR(120)  NOT NULL DEFAULT '',
    attempts          INTEGER       NOT NULL DEFAULT 1,
    last_error_code   VARCHAR(32)   NOT NULL DEFAULT '',
    uploaded_by       VARCHAR(36),
    scanned_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT attachment_scans_quarantine_key_key UNIQUE (quarantine_key),
    CONSTRAINT ck_attachment_scan_status CHECK (status IN ('pending', 'clean', 'infected', 'error', 'legacy_unscanned'))
);
CREATE INDEX IF NOT EXISTS ix_attachment_scans_status_created ON attachment_scans (status, created_at);
CREATE INDEX IF NOT EXISTS ix_attachment_scans_attachment_id ON attachment_scans (attachment_id);
CREATE INDEX IF NOT EXISTS ix_attachment_scans_sha256 ON attachment_scans (sha256);

CREATE TABLE IF NOT EXISTS app_markers (
    key     VARCHAR(64) PRIMARY KEY,
    value   VARCHAR(64) NOT NULL,
    set_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    app_env VARCHAR(32) NOT NULL DEFAULT ''
);
COMMIT;
