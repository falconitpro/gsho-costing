-- 066 (D-201, J-e): комплекты ТО — работы из справочника сервиса и диапазон заводских номеров машин.
-- Только новые столбцы; существующие комплекты: без работ, подходят ко всем номерам (диапазон пуст).
BEGIN;
ALTER TABLE maintenance_kits ADD COLUMN IF NOT EXISTS works JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE maintenance_kits ADD COLUMN IF NOT EXISTS serial_from VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE maintenance_kits ADD COLUMN IF NOT EXISTS serial_to VARCHAR(64) NOT NULL DEFAULT '';
COMMIT;
