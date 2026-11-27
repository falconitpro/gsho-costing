-- 064 (D-198, I-3): смета наряда с версиями и согласованием, документы-снимки (дефектная ведомость, смета, акт),
-- вид оплаты наряда (клиент, гарантия, за счёт компании) и связь с гарантийной претензией. Только расширение.
BEGIN;

ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS billing VARCHAR(16) NOT NULL DEFAULT 'client';
ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS warranty_claim_id VARCHAR(36) REFERENCES warranty_claims(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS service_estimates (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id),
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    currency VARCHAR(3) NOT NULL DEFAULT 'RUB',
    vat_pct DOUBLE PRECISION NOT NULL DEFAULT 22.0,
    hour_rate DOUBLE PRECISION,
    note TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    total_net DOUBLE PRECISION,
    total_vat DOUBLE PRECISION,
    total DOUBLE PRECISION,
    sent_at TIMESTAMPTZ,
    sent_channel VARCHAR(16) NOT NULL DEFAULT '',
    decided_at TIMESTAMPTZ,
    client_name VARCHAR(255) NOT NULL DEFAULT '',
    decision_basis TEXT NOT NULL DEFAULT '',
    decided_by VARCHAR(36) REFERENCES users(id),
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_service_estimate_version UNIQUE (work_order_id, version)
);
CREATE INDEX IF NOT EXISTS ix_service_estimates_work_order_id ON service_estimates (work_order_id);

CREATE TABLE IF NOT EXISTS service_estimate_lines (
    id VARCHAR(36) PRIMARY KEY,
    estimate_id VARCHAR(36) NOT NULL REFERENCES service_estimates(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 1,
    kind VARCHAR(8) NOT NULL DEFAULT 'other',
    part_id VARCHAR(36) REFERENCES parts(id) ON DELETE SET NULL,
    work_id VARCHAR(36) REFERENCES work_order_works(id) ON DELETE SET NULL,
    title VARCHAR(500) NOT NULL,
    article VARCHAR(128) NOT NULL DEFAULT '',
    qty DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    unit VARCHAR(16) NOT NULL DEFAULT 'шт',
    price DOUBLE PRECISION,
    discount_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_service_estimate_lines_estimate_id ON service_estimate_lines (estimate_id);

CREATE TABLE IF NOT EXISTS service_documents (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id),
    estimate_id VARCHAR(36) REFERENCES service_estimates(id) ON DELETE SET NULL,
    kind VARCHAR(16) NOT NULL,
    number VARCHAR(40) NOT NULL UNIQUE,
    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    storage_path TEXT NOT NULL DEFAULT '',
    filename VARCHAR(255) NOT NULL DEFAULT '',
    legal_checked BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_service_documents_work_order_id ON service_documents (work_order_id);

COMMIT;
