-- D-155: подтверждения к поручениям, напоминания о сроках, история сигналов «Требует вмешательства» (только добавление)
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS alerts JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS assignment_files (
    id VARCHAR(36) PRIMARY KEY,
    assignment_id VARCHAR(36) NOT NULL REFERENCES assignments(id),
    file_name VARCHAR(255) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    sha256 VARCHAR(64) NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    scan_status VARCHAR(24) NOT NULL DEFAULT 'clean',
    scan_id VARCHAR(36),
    uploaded_by VARCHAR(36),
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at TIMESTAMPTZ,
    removed_by VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_assignment_files_assignment_id ON assignment_files (assignment_id);

CREATE TABLE IF NOT EXISTS signal_log (
    key VARCHAR(160) PRIMARY KEY,
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    title VARCHAR(512) NOT NULL DEFAULT '',
    object_type VARCHAR(64) NOT NULL DEFAULT '',
    object_number VARCHAR(64) NOT NULL DEFAULT '',
    direction VARCHAR(64) NOT NULL DEFAULT '',
    url VARCHAR(300) NOT NULL DEFAULT '',
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    times INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_signal_log_resolved_at ON signal_log (resolved_at);
