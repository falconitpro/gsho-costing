-- D-077: просмотр файлов сделок в системе; скачивание — по праву или по одобренному запросу
CREATE TABLE IF NOT EXISTS file_download_requests (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    deal_id VARCHAR(36),
    storage_path VARCHAR(1024) NOT NULL,
    file_name VARCHAR(512) NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    comment TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_file_download_requests_user_id ON file_download_requests (user_id);
CREATE INDEX IF NOT EXISTS ix_file_download_requests_deal_id ON file_download_requests (deal_id);
