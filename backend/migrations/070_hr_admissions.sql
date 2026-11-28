-- 070 (D-206, L4): допуски, обучение, инструктажи и медосмотры — общий справочник service_permits и общая таблица engineer_permits.
-- Допуск принадлежит сотруднику «Персонала» (employee_id) и/или учётной записи CRM (user_id): рабочим без входа в CRM — только employee_id.
-- Проверка допусков при назначении на наряд не меняется, только дополнительно учитывает допуски сотрудника, связанного с учётной записью.
BEGIN;
ALTER TABLE service_permits ADD COLUMN IF NOT EXISTS category VARCHAR(16) NOT NULL DEFAULT 'permit';
ALTER TABLE engineer_permits ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE engineer_permits ADD COLUMN IF NOT EXISTS employee_id VARCHAR(36) REFERENCES hr_employees(id);
ALTER TABLE engineer_permits ADD COLUMN IF NOT EXISTS result VARCHAR(16) NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_engineer_permits_employee_id ON engineer_permits (employee_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_engineer_permit_employee ON engineer_permits (employee_id, permit_code) WHERE employee_id IS NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_engineer_permit_owner') THEN
        ALTER TABLE engineer_permits ADD CONSTRAINT ck_engineer_permit_owner CHECK (user_id IS NOT NULL OR employee_id IS NOT NULL);
    END IF;
END $$;
-- уже заведённые допуски инженеров — к их карточкам в «Персонале» (если карточка связана с учётной записью)
UPDATE engineer_permits ep SET employee_id = e.id
  FROM hr_employees e
 WHERE e.user_id = ep.user_id AND ep.employee_id IS NULL
   AND NOT EXISTS (SELECT 1 FROM engineer_permits x WHERE x.employee_id = e.id AND x.permit_code = ep.permit_code);
COMMIT;
