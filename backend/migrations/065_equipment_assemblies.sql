-- 065 (D-200, J-c): справочник узлов и агрегатов (общий и по модели ГШО), ссылка из применимости запчастей.
-- Только расширение: новая таблица и столбец part_applicability.assembly_id. Текст «узел» в применимости остаётся
-- (для старых записей и моделей других производителей). Перенос текстов в справочник — scripts/assemblies_migrate.py
-- (пробный прогон по умолчанию, --yes — по согласованию); он же заводит общий справочник узлов.
BEGIN;

CREATE TABLE IF NOT EXISTS equipment_assemblies (
    id VARCHAR(36) PRIMARY KEY,
    model_id VARCHAR(36) REFERENCES equipment_models(id) ON DELETE CASCADE,
    parent_id VARCHAR(36) REFERENCES equipment_assemblies(id) ON DELETE SET NULL,
    code VARCHAR(32) NOT NULL DEFAULT '',
    name_ru VARCHAR(255) NOT NULL,
    name_en VARCHAR(255) NOT NULL DEFAULT '',
    name_cn VARCHAR(255) NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_equipment_assembly_name UNIQUE (model_id, name_ru)
);
CREATE INDEX IF NOT EXISTS ix_equipment_assemblies_model_id ON equipment_assemblies (model_id);

ALTER TABLE part_applicability ADD COLUMN IF NOT EXISTS assembly_id VARCHAR(36) REFERENCES equipment_assemblies(id) ON DELETE SET NULL;

COMMIT;
