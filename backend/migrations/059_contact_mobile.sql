-- 059 (D-190): мобильный (рабочий) телефон контакта клиента — по просьбе владельца 27.11. Только расширение.
BEGIN;
ALTER TABLE client_contacts ADD COLUMN IF NOT EXISTS mobile_phone VARCHAR(64) NOT NULL DEFAULT '';
COMMIT;
