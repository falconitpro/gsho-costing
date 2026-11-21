-- D-143: фоновые задания в отдельном процессе — отметки запусков, успеха и ошибок по каждому заданию
CREATE TABLE IF NOT EXISTS job_runs (
    job VARCHAR(64) PRIMARY KEY,
    last_start TIMESTAMPTZ,
    last_ok TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    error_at TIMESTAMPTZ,
    runs INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    host VARCHAR(64) NOT NULL DEFAULT ''
);
