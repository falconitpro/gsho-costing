-- 055 (D-184, K-1): контактные лица клиентов и журнал просмотров (152-ФЗ). Только добавление.
-- Прежние clients.contacts (JSON) не меняются; перенос — scripts/contacts_import.py (пробный прогон по умолчанию).
BEGIN;

CREATE TABLE IF NOT EXISTS client_contacts (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id VARCHAR(36) REFERENCES client_sites(id) ON DELETE SET NULL,
    full_name VARCHAR(255) NOT NULL,
    position VARCHAR(255) NOT NULL DEFAULT '',
    department VARCHAR(255) NOT NULL DEFAULT '',
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    work_phone VARCHAR(64) NOT NULL DEFAULT '',
    work_email VARCHAR(255) NOT NULL DEFAULT '',
    messenger VARCHAR(255) NOT NULL DEFAULT '',
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    consent_on DATE,
    consent_source VARCHAR(255) NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_client_contacts_client_active ON client_contacts (client_id, is_active);
CREATE INDEX IF NOT EXISTS ix_client_contacts_client_id ON client_contacts (client_id);
CREATE INDEX IF NOT EXISTS ix_client_contacts_site_id ON client_contacts (site_id);

CREATE TABLE IF NOT EXISTS client_contact_views (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id VARCHAR(36),
    client_id VARCHAR(36) NOT NULL,
    contact_id VARCHAR(36),
    kind VARCHAR(16) NOT NULL DEFAULT 'list'
);
CREATE INDEX IF NOT EXISTS ix_client_contact_views_at ON client_contact_views (at);
CREATE INDEX IF NOT EXISTS ix_client_contact_views_user_id ON client_contact_views (user_id);
CREATE INDEX IF NOT EXISTS ix_client_contact_views_client_id ON client_contact_views (client_id);

COMMIT;
