-- Зеркальные договоры компании группы с конечным поставщиком (docs/mirror-requests/README.md, D-160). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): на стенде с APP_ENV=dev колонку добавляет init_db().
BEGIN;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS mirror_kind VARCHAR(16) NOT NULL DEFAULT '';
COMMIT;
