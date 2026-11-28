-- 072 (D-209): приказ по личному составу заполняется полностью (решение владельца 28.11.2026: от руки — только подпись).
-- Реквизиты приказа, которых нет в кадровом событии (договор, испытание, основание увольнения, подписант), — в связи событие ↔ приказ.
BEGIN;
ALTER TABLE hr_event_orders ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;
-- кто ещё видит документ Канцелярии; приказ с оплатой труда — только ЗГД, ФД, бухгалтер по зарплате (доступ к оплате труда «Персонала»);
-- кого ознакомить после подписания
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS viewer_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS pay_restricted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS planned_acks JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMIT;
