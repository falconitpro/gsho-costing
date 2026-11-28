-- 071 (D-208, L2 упрощённо): приказ по личному составу в Канцелярии, сформированный по кадровому событию «Персонала».
-- Подпись — на бумаге, скан подписанного приказа — в Канцелярии (как сейчас). Электронная подпись — после матрицы ЭДО от юриста.
BEGIN;
CREATE TABLE IF NOT EXISTS hr_event_orders (
    id VARCHAR(36) PRIMARY KEY,
    event_id VARCHAR(36) NOT NULL REFERENCES hr_employee_events(id),
    office_doc_id VARCHAR(36) NOT NULL REFERENCES office_docs(id),
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_event_orders_event_id ON hr_event_orders (event_id);
CREATE INDEX IF NOT EXISTS ix_hr_event_orders_office_doc_id ON hr_event_orders (office_doc_id);
COMMIT;
