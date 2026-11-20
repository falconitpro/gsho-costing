-- D-117: сбор ошибок системы (сервер и страницы в браузере). Одна запись на отпечаток, повторы — счётчик.
CREATE TABLE IF NOT EXISTS app_errors (
  id VARCHAR(36) PRIMARY KEY,
  fingerprint VARCHAR(64) NOT NULL UNIQUE,
  source VARCHAR(16) NOT NULL,
  kind VARCHAR(120) NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  "where" VARCHAR(300) NOT NULL DEFAULT '',
  trace TEXT NOT NULL DEFAULT '',
  count INTEGER NOT NULL DEFAULT 1,
  users JSONB NOT NULL DEFAULT '[]'::jsonb,
  last_request_id VARCHAR(64) NOT NULL DEFAULT '',
  release VARCHAR(40) NOT NULL DEFAULT '',
  first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,
  resolved_by VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_app_errors_fingerprint ON app_errors (fingerprint);
CREATE INDEX IF NOT EXISTS ix_app_errors_last_seen ON app_errors (last_seen);
