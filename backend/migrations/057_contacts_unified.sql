-- 057 (D-188): единый справочник контактов, этап 2 — связи «контакт ↔ клиент», доп. адреса, обезличивание,
-- история изменений контакта, снимок отправки КП. Расширение + заполнение связей из client_contacts; прежние поля не меняются.
BEGIN;

ALTER TABLE client_contacts ADD COLUMN IF NOT EXISTS extra_emails JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE client_contacts ADD COLUMN IF NOT EXISTS anonymized_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS client_contact_links (
    id VARCHAR(36) PRIMARY KEY,
    contact_id VARCHAR(36) NOT NULL REFERENCES client_contacts(id) ON DELETE CASCADE,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id VARCHAR(36) REFERENCES client_sites(id) ON DELETE SET NULL,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from DATE,
    valid_to DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_client_contact_links_client_active ON client_contact_links (client_id, is_active);
CREATE INDEX IF NOT EXISTS ix_client_contact_links_contact_id ON client_contact_links (contact_id);
CREATE INDEX IF NOT EXISTS ix_client_contact_links_client_id ON client_contact_links (client_id);

INSERT INTO client_contact_links (id, contact_id, client_id, site_id, roles, is_primary, is_active, comment, created_by, created_at, updated_at)
SELECT gen_random_uuid()::text, c.id, c.client_id, c.site_id, c.roles, c.is_primary, c.is_active, '', c.created_by, c.created_at, c.updated_at
FROM client_contacts c
WHERE NOT EXISTS (SELECT 1 FROM client_contact_links l WHERE l.contact_id = c.id AND l.client_id = c.client_id);

CREATE TABLE IF NOT EXISTS client_contact_history (
    id BIGSERIAL PRIMARY KEY,
    contact_id VARCHAR(36) NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id VARCHAR(36),
    field VARCHAR(64) NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_client_contact_history_contact_id ON client_contact_history (contact_id);

CREATE TABLE IF NOT EXISTS kp_dispatches (
    id VARCHAR(36) PRIMARY KEY,
    deal_id VARCHAR(36) NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    version_id VARCHAR(36),
    revision_no INTEGER,
    method VARCHAR(16) NOT NULL,
    sent_by VARCHAR(36),
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject TEXT NOT NULL DEFAULT '',
    message_id VARCHAR(255) NOT NULL DEFAULT '',
    dispatch_key VARCHAR(64) NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_kp_dispatches_deal_id ON kp_dispatches (deal_id);

CREATE TABLE IF NOT EXISTS kp_dispatch_recipients (
    id BIGSERIAL PRIMARY KEY,
    dispatch_id VARCHAR(36) NOT NULL REFERENCES kp_dispatches(id) ON DELETE CASCADE,
    kind VARCHAR(4) NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    contact_id VARCHAR(36),
    link_id VARCHAR(36),
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    job_title VARCHAR(255) NOT NULL DEFAULT '',
    email VARCHAR(255) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_kp_dispatch_recipients_dispatch_id ON kp_dispatch_recipients (dispatch_id);

COMMIT;
