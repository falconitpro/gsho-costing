-- Зеркальные запросы ГШО через компанию группы (docs/mirror-requests.md, D-150…D-155). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): на стенде с APP_ENV=dev колонки добавляет init_db().
BEGIN;
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS mirror_of_id VARCHAR(36);
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS end_supplier_id VARCHAR(36);
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS route_id VARCHAR(36);
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS chain JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS mirror_state VARCHAR(24) NOT NULL DEFAULT '';
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS transfer_price DOUBLE PRECISION;
ALTER TABLE factory_offers ADD COLUMN IF NOT EXISTS transfer_note TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_factory_offers_mirror_of_id ON factory_offers (mirror_of_id);
ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS channels JSONB NOT NULL DEFAULT '{}'::jsonb;
COMMIT;
