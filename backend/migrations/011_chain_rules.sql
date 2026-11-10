-- Правила внутригрупповых цепочек и виза ФД/ЗГД до КП (docs/mirror-requests/README.md, D-164…D-167). Аддитивно, повторный запуск безопасен.
-- Правила хранятся в настройках (rate_settings, ключ intragroup_rules) — таблиц не добавляется.
-- НЕ запускается автоматически (D-100): на стенде с APP_ENV=dev колонки добавляет init_db().
BEGIN;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS chain_approval JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE part_requests ADD COLUMN IF NOT EXISTS chain_approval JSONB NOT NULL DEFAULT '{}'::jsonb;
COMMIT;
