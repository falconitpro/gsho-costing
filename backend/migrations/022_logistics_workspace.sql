-- Рабочее место логиста (D-073): адреса отгрузки и доставки по сделке, запросы и ответы логистических компаний;
-- адреса доставки в карточке клиента (новый — после утверждения ЗГД).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на рабочем сервере — update.sh.
BEGIN;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS logistics_info JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS delivery_addresses JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMIT;
