-- D-145: десятичные суммы, этап 2 — дробные числа в JSON-полях с суммами округляются до 6 знаков («хвосты» двоичной дроби вида
-- 1234.5600000000002 → 1234.56). Целые, строки и даты не меняются; копейки и курсы не меняются. Меняются только строки с «хвостами».
CREATE OR REPLACE FUNCTION pg_temp.gsho_jround(j jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE jsonb_typeof(j)
    WHEN 'object' THEN (SELECT coalesce(jsonb_object_agg(k, pg_temp.gsho_jround(v)), '{}'::jsonb) FROM jsonb_each(j) AS t(k, v))
    WHEN 'array'  THEN (SELECT coalesce(jsonb_agg(pg_temp.gsho_jround(v) ORDER BY o), '[]'::jsonb) FROM jsonb_array_elements(j) WITH ORDINALITY AS t(v, o))
    WHEN 'number' THEN CASE WHEN scale((j #>> '{}')::numeric) > 6 THEN to_jsonb(trim_scale(round((j #>> '{}')::numeric, 6))) ELSE j END
    ELSE j END
$$;
BEGIN;
UPDATE payment_schemes SET lines = pg_temp.gsho_jround(lines::jsonb) WHERE lines IS NOT NULL AND lines::jsonb IS DISTINCT FROM pg_temp.gsho_jround(lines::jsonb);
UPDATE positions SET zip_items = pg_temp.gsho_jround(zip_items::jsonb) WHERE zip_items IS NOT NULL AND zip_items::jsonb IS DISTINCT FROM pg_temp.gsho_jround(zip_items::jsonb);
UPDATE batches SET items = pg_temp.gsho_jround(items::jsonb) WHERE items IS NOT NULL AND items::jsonb IS DISTINCT FROM pg_temp.gsho_jround(items::jsonb);
UPDATE legal_zip_sets SET lines = pg_temp.gsho_jround(lines::jsonb) WHERE lines IS NOT NULL AND lines::jsonb IS DISTINCT FROM pg_temp.gsho_jround(lines::jsonb);
UPDATE contracts SET payment_terms = pg_temp.gsho_jround(payment_terms::jsonb) WHERE payment_terms IS NOT NULL AND payment_terms::jsonb IS DISTINCT FROM pg_temp.gsho_jround(payment_terms::jsonb);
UPDATE contracts SET penalties = pg_temp.gsho_jround(penalties::jsonb) WHERE penalties IS NOT NULL AND penalties::jsonb IS DISTINCT FROM pg_temp.gsho_jround(penalties::jsonb);
UPDATE contracts SET security = pg_temp.gsho_jround(security::jsonb) WHERE security IS NOT NULL AND security::jsonb IS DISTINCT FROM pg_temp.gsho_jround(security::jsonb);
UPDATE contracts SET facts = pg_temp.gsho_jround(facts::jsonb) WHERE facts IS NOT NULL AND facts::jsonb IS DISTINCT FROM pg_temp.gsho_jround(facts::jsonb);
UPDATE contract_claims SET lines = pg_temp.gsho_jround(lines::jsonb) WHERE lines IS NOT NULL AND lines::jsonb IS DISTINCT FROM pg_temp.gsho_jround(lines::jsonb);
UPDATE part_kps SET lines = pg_temp.gsho_jround(lines::jsonb) WHERE lines IS NOT NULL AND lines::jsonb IS DISTINCT FROM pg_temp.gsho_jround(lines::jsonb);
UPDATE purchase_orders SET payments = pg_temp.gsho_jround(payments::jsonb) WHERE payments IS NOT NULL AND payments::jsonb IS DISTINCT FROM pg_temp.gsho_jround(payments::jsonb);
UPDATE warranty_claims SET lines = pg_temp.gsho_jround(lines::jsonb) WHERE lines IS NOT NULL AND lines::jsonb IS DISTINCT FROM pg_temp.gsho_jround(lines::jsonb);
UPDATE warranty_claims SET compensations = pg_temp.gsho_jround(compensations::jsonb) WHERE compensations IS NOT NULL AND compensations::jsonb IS DISTINCT FROM pg_temp.gsho_jround(compensations::jsonb);
UPDATE part_batches SET costs = pg_temp.gsho_jround(costs::jsonb) WHERE costs IS NOT NULL AND costs::jsonb IS DISTINCT FROM pg_temp.gsho_jround(costs::jsonb);
COMMIT;
