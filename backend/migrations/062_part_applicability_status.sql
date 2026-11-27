-- 062 (D-196, J-b): применимость запчастей — статус проверки, исполнение, диапазон серийных номеров, основание,
-- кто и когда проверил. Только новые столбцы. Все существующие связи получают статус «не подтверждено»
-- (решение владельца 7 от 26.11.2026, D-180): DEFAULT 'unverified' заполняет их при добавлении столбца.
BEGIN;

ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS execution_id VARCHAR(36) REFERENCES equipment_executions(id) ON DELETE SET NULL;
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'unverified';
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS serial_from VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS serial_to VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS basis_kind VARCHAR(16) NOT NULL DEFAULT '';
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS basis TEXT NOT NULL DEFAULT '';
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS verified_by VARCHAR(36) REFERENCES users(id);
ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

COMMIT;
