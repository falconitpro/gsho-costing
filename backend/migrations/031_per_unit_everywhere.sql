-- D-105: все расходы — на 1 машину. Записи старого режима «на всю позицию / на всю партию» переводятся:
-- суммы делятся на количество машин, ставится признак per_unit. Итог расчёта не меняется: при расчёте суммы снова
-- умножаются на количество. Повторный запуск ничего не меняет (переводятся только записи без признака).
BEGIN;

-- число из JSON так же, как его читает расчёт (deal_calc._num): число, либо первое число из текста («12 500», «12,5»)
CREATE OR REPLACE FUNCTION pg_temp.num(v jsonb) RETURNS float8 LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN v IS NULL OR jsonb_typeof(v) = 'null' THEN NULL
    WHEN jsonb_typeof(v) = 'number' THEN (v #>> '{}')::float8
    WHEN jsonb_typeof(v) = 'string' THEN
      replace(substring(replace(replace(v #>> '{}', chr(160), ''), ' ', '') FROM '-?\d+(?:[.,]\d+)?'), ',', '.')::float8
    ELSE NULL END
$$;

-- делит число (или сумму {amount, currency}) на k; нечисловое значение остаётся как есть (расчёт считает его нулём)
CREATE OR REPLACE FUNCTION pg_temp.div(v jsonb, k float8) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN v IS NULL THEN NULL
    WHEN jsonb_typeof(v) = 'object' THEN
      CASE WHEN pg_temp.num(v->'amount') IS NULL THEN v ELSE v || jsonb_build_object('amount', pg_temp.num(v->'amount') / k) END
    WHEN pg_temp.num(v) IS NULL THEN v
    ELSE to_jsonb(pg_temp.num(v) / k) END
$$;

-- 1. Расходы позиции: логистика по Китаю, экспортные формальности, сертификация; утильсбор — если он не по коэффициенту
--    (по коэффициенту он и так за каждую машину)
CREATE TEMP TABLE _pos ON COMMIT DROP AS
  SELECT id, logistics AS lg, GREATEST(COALESCE(qty, 1), 1)::float8 AS k FROM positions
  WHERE logistics IS NOT NULL AND jsonb_typeof(logistics) = 'object' AND COALESCE(logistics->>'per_unit', 'false') <> 'true';

UPDATE positions p SET logistics = (
  SELECT jsonb_object_agg(e.key, CASE
           WHEN e.key IN ('domestic_logistics', 'export_formalities', 'certification_rub') THEN pg_temp.div(e.value, _pos.k)
           WHEN e.key = 'util_fee_rub' AND (_pos.lg->'util_coefficient' IS NULL OR jsonb_typeof(_pos.lg->'util_coefficient') = 'null')
             THEN pg_temp.div(e.value, _pos.k)
           ELSE e.value END)
  FROM jsonb_each(_pos.lg) e) || jsonb_build_object('per_unit', true)
FROM _pos WHERE _pos.id = p.id AND _pos.lg <> '{}'::jsonb;

UPDATE positions p SET logistics = jsonb_build_object('per_unit', true)
FROM _pos WHERE _pos.id = p.id AND _pos.lg = '{}'::jsonb;

-- 2. Суммы партии: фрахт, страховка, «от двери до двери», брокер, доставка по РФ — на 1 машину партии
CREATE TEMP TABLE _bat ON COMMIT DROP AS
  SELECT b.id, b.items AS it,
         GREATEST(COALESCE((SELECT SUM(GREATEST(COALESCE(p.qty, 1), 1)) FROM positions p WHERE p.batch_id = b.id), 1), 1)::float8 AS k
  FROM batches b
  WHERE b.items IS NOT NULL AND jsonb_typeof(b.items) = 'object' AND COALESCE(b.items->>'per_unit', 'false') <> 'true';

UPDATE batches b SET items = COALESCE((
  SELECT jsonb_object_agg(e.key, CASE
           WHEN e.key IN ('intl_freight', 'insurance_amount', 'door_to_door', 'broker_rub', 'rf_delivery_rub') THEN pg_temp.div(e.value, _bat.k)
           ELSE e.value END)
  FROM jsonb_each(_bat.it) e), '{}'::jsonb) || jsonb_build_object('per_unit', true)
FROM _bat WHERE _bat.id = b.id;

-- 3. Доукомплектование в РФ: количество и монтаж — на 1 машину. Переводятся опции, у которых количество делится
--    на число машин без остатка; остальные остаются как были (расчёт их по-прежнему понимает)
UPDATE local_options o SET per_unit = true,
  qty = COALESCE(o.qty, 1) / GREATEST(COALESCE(p.qty, 1), 1),
  install_cost_rub = o.install_cost_rub / GREATEST(COALESCE(p.qty, 1), 1)
FROM positions p
WHERE p.id = o.position_id AND NOT o.per_unit AND COALESCE(o.qty, 1) % GREATEST(COALESCE(p.qty, 1), 1) = 0;

COMMIT;
