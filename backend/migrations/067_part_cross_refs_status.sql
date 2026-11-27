-- 067 (D-202): кроссировки номеров — «номер завода-изготовителя машины ↔ OEM-номер производителя детали».
-- Новые виды номеров (machine_maker, oem) — значения прежнего столбца kind. Только новые столбцы: статус проверки,
-- основание, источник (ручной ввод, импорт, ИИ), кто и когда проверил. Существующие номера — «не подтверждено».
BEGIN;
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'unverified';
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS basis_kind VARCHAR(16) NOT NULL DEFAULT '';
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS basis TEXT NOT NULL DEFAULT '';
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'manual';
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS source_ref VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS verified_by VARCHAR(36) REFERENCES users(id);
ALTER TABLE part_cross_refs ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
COMMIT;
