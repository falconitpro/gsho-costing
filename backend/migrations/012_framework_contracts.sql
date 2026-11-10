-- Рамочные договоры и спецификации к ним (docs/legal/framework-contracts.md, D-168…D-170). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): на стенде с APP_ENV=dev колонку добавляет init_db().
BEGIN;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS is_framework BOOLEAN NOT NULL DEFAULT FALSE;
COMMIT;
