-- Зеркальные запросы цен на запчасти через компанию группы (docs/mirror-requests/README.md, D-156…D-159). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): на стенде с APP_ENV=dev колонки добавляет init_db().
BEGIN;
ALTER TABLE supplier_rfqs ADD COLUMN IF NOT EXISTS mirror_of_id VARCHAR(36);
ALTER TABLE supplier_rfqs ADD COLUMN IF NOT EXISTS end_supplier_id VARCHAR(36);
ALTER TABLE supplier_rfqs ADD COLUMN IF NOT EXISTS route_id VARCHAR(36);
ALTER TABLE supplier_rfqs ADD COLUMN IF NOT EXISTS mirror_state VARCHAR(24) NOT NULL DEFAULT '';
ALTER TABLE supplier_rfqs ADD COLUMN IF NOT EXISTS chain JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ix_supplier_rfqs_mirror_of_id ON supplier_rfqs (mirror_of_id);
ALTER TABLE supplier_rfq_lines ADD COLUMN IF NOT EXISTS chain JSONB NOT NULL DEFAULT '{}'::jsonb;
COMMIT;
