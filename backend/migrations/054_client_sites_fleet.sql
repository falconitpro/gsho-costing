-- 054 (D-182, K-2; основа — пакет Sprint 01 K Sites v1): нормализация площадок клиентов и привязка парка машин к площадкам.
-- Только добавление: прежние clients.sites (JSON) и client_machines.site (текст) не меняются; перенос — scripts/sites_import.py (пробный прогон по умолчанию).
-- Контактов в площадке нет: персональные данные — отдельной сущностью в K-1 (решение 1, D-180).
BEGIN;

CREATE TABLE IF NOT EXISTS client_sites (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    site_type VARCHAR(32) NOT NULL DEFAULT 'other',
    code VARCHAR(64) NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    country VARCHAR(2) NOT NULL DEFAULT '',
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    timezone VARCHAR(64) NOT NULL DEFAULT '',
    access_mode VARCHAR(32) NOT NULL DEFAULT '',
    hse_notes TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE client_machines ADD COLUMN IF NOT EXISTS site_id VARCHAR(36);
ALTER TABLE client_machines ADD COLUMN IF NOT EXISTS origin VARCHAR(24) NOT NULL DEFAULT 'unknown';
ALTER TABLE client_machines ADD COLUMN IF NOT EXISTS warranty_until DATE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'client_machines_site_id_fkey') THEN
        ALTER TABLE client_machines ADD CONSTRAINT client_machines_site_id_fkey
            FOREIGN KEY (site_id) REFERENCES client_sites(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_client_sites_client_active ON client_sites(client_id, is_active);
CREATE INDEX IF NOT EXISTS ix_client_sites_name ON client_sites(name);
CREATE INDEX IF NOT EXISTS ix_client_sites_client_id ON client_sites(client_id);
CREATE INDEX IF NOT EXISTS ix_client_machines_site_id ON client_machines(site_id);

COMMIT;
