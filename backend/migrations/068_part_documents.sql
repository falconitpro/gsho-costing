-- 068 (D-203, J-f): документы детали — вид, язык, ревизия, срок действия, предыдущая ревизия. Только новая таблица.
BEGIN;
CREATE TABLE IF NOT EXISTS part_documents (
    id VARCHAR(36) PRIMARY KEY,
    part_id VARCHAR(36) NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    kind VARCHAR(24) NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    language VARCHAR(8) NOT NULL DEFAULT 'ru',
    revision VARCHAR(64) NOT NULL DEFAULT '',
    file_name VARCHAR(255) NOT NULL,
    storage_path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    sha256 VARCHAR(64) NOT NULL DEFAULT '',
    supersedes_id VARCHAR(36) REFERENCES part_documents(id) ON DELETE SET NULL,
    valid_until DATE,
    comment TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    uploaded_by VARCHAR(36) REFERENCES users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_part_documents_part_id ON part_documents (part_id);
COMMIT;
