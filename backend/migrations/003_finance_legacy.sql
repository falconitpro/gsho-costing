-- Раздел «Финансы», вариант Б — этап 3: отзыв непроведённых записей (старые отметки оплаты, D-117).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-100): применить вручную после 002_finance_core.sql.
-- Права «Раздел «Финансы»» (finance_section, D-116) — данные матрицы в коде, миграции БД не требуют.
BEGIN;
ALTER TABLE finance_transactions ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE finance_transactions ADD COLUMN IF NOT EXISTS withdrawn_by VARCHAR(36);
ALTER TABLE finance_transactions ADD COLUMN IF NOT EXISTS withdrawn_reason TEXT NOT NULL DEFAULT '';
COMMIT;
