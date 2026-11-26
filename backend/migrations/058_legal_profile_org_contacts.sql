-- 058 (D-189): единый справочник контактов, этап 3 — телефон и почта организации в юр. профиле (для договоров вместо
-- «первого контактного лица»), карта разового переноса контактов. Расширение + заполнение: org_phone / org_email —
-- из первого контакта профиля, чтобы договоры не изменились (юрист проверяет).
BEGIN;

ALTER TABLE legal_profiles ADD COLUMN IF NOT EXISTS org_phone VARCHAR(64) NOT NULL DEFAULT '';
ALTER TABLE legal_profiles ADD COLUMN IF NOT EXISTS org_email VARCHAR(255) NOT NULL DEFAULT '';

UPDATE legal_profiles
SET org_phone = left(coalesce(contacts -> 0 ->> 'phone', ''), 64),
    org_email = left(coalesce(contacts -> 0 ->> 'email', ''), 255)
WHERE org_phone = '' AND org_email = '' AND jsonb_typeof(contacts) = 'array' AND jsonb_array_length(contacts) > 0;

CREATE TABLE IF NOT EXISTS contact_migration_map (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(24) NOT NULL,
    source_key VARCHAR(160) NOT NULL UNIQUE,
    contact_id VARCHAR(36),
    link_id VARCHAR(36),
    outcome VARCHAR(16) NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
