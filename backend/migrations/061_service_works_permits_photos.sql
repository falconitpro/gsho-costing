-- 061 (D-195, I-2): работы наряда с нормативами и фактом, допуски сотрудников со сроками, фото наряда с геолокацией,
-- заключение диагностики. Только расширение: новые таблицы и один новый столбец work_orders.diagnosis.
-- Фото хранятся 10 лет (retain_until), до 10 МБ, с координатами — решение владельца 27.11.2026.
BEGIN;

ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS diagnosis TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS engineer_permits (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    permit_code VARCHAR(32) NOT NULL,
    number VARCHAR(64) NOT NULL DEFAULT '',
    issued_on DATE,
    valid_until DATE,
    comment TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_engineer_permit UNIQUE (user_id, permit_code)
);
CREATE INDEX IF NOT EXISTS ix_engineer_permits_user_id ON engineer_permits (user_id);

CREATE TABLE IF NOT EXISTS work_order_works (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 1,
    work_type_id VARCHAR(36) REFERENCES service_work_types(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    norm_hours DOUBLE PRECISION,
    actual_hours DOUBLE PRECISION,
    performer_id VARCHAR(36) REFERENCES users(id),
    done BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_work_order_works_work_order_id ON work_order_works (work_order_id);

CREATE TABLE IF NOT EXISTS work_order_photos (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id),
    event_id VARCHAR(36),
    storage_path TEXT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(64) NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 VARCHAR(64) NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    checklist_item_id VARCHAR(36) REFERENCES work_order_checklist_items(id) ON DELETE SET NULL,
    work_id VARCHAR(36) REFERENCES work_order_works(id) ON DELETE SET NULL,
    taken_at TIMESTAMPTZ,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy_m DOUBLE PRECISION,
    geo_source VARCHAR(8) NOT NULL DEFAULT '',
    uploaded_by VARCHAR(36) REFERENCES users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retain_until DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_work_order_photos_work_order_id ON work_order_photos (work_order_id);

COMMIT;
