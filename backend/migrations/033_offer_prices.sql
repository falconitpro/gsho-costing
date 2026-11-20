-- D-109: цены предложения в одном месте. Таблица записей цены и перенос существующих цен:
-- цена КД каждого предложения и итог каждой редакции КП. Повторный запуск ничего не добавляет.
BEGIN;

CREATE TABLE IF NOT EXISTS offer_prices (
  id VARCHAR(36) PRIMARY KEY,
  deal_id VARCHAR(36) NOT NULL REFERENCES deals(id),
  variant_id VARCHAR(36) NOT NULL,
  version_id VARCHAR(36),
  kind VARCHAR(16) NOT NULL,
  source_id VARCHAR(36) NOT NULL DEFAULT '',
  revision_no INTEGER,
  total_rub DOUBLE PRECISION NOT NULL,
  extras_rub DOUBLE PRECISION NOT NULL DEFAULT 0,
  currency VARCHAR(3) NOT NULL DEFAULT 'RUB',
  fx_rate DOUBLE PRECISION,
  units INTEGER NOT NULL DEFAULT 1,
  unit_amount DOUBLE PRECISION,
  created_by VARCHAR(36),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  seq BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_offer_prices_deal_id ON offer_prices (deal_id);
CREATE INDEX IF NOT EXISTS ix_offer_prices_variant_id ON offer_prices (variant_id);
CREATE INDEX IF NOT EXISTS ix_offer_prices_created_at ON offer_prices (created_at);

-- машин в предложении (как pricing.variant_units): одна позиция — её количество, несколько — 1 (комплект)
CREATE TEMP TABLE _vu ON COMMIT DROP AS
  SELECT v.id, v.deal_id, upper(coalesce(nullif(v.price_currency, ''), 'RUB')) AS cur,
         CASE WHEN upper(coalesce(nullif(v.price_currency, ''), 'RUB')) = 'RUB' THEN 1.0 ELSE v.price_fx_rate END AS rate,
         CASE WHEN count(p.id) = 1 THEN greatest(max(p.qty), 1) ELSE 1 END AS units
  FROM variants v
  LEFT JOIN positions p ON p.deal_id = v.deal_id
       AND (v.position_ids IS NULL OR jsonb_array_length(v.position_ids) = 0 OR v.position_ids ? p.id)
  GROUP BY v.id;

-- 1. цена КД
INSERT INTO offer_prices (id, deal_id, variant_id, kind, source_id, extras_rub, total_rub, currency, fx_rate, units, unit_amount, created_at, seq)
SELECT gen_random_uuid()::text, v.deal_id, v.id, 'kd', '', 0, round(v.price_rub::numeric, 2)::float8, u.cur, u.rate, u.units,
       CASE WHEN u.rate > 0 THEN round((v.price_rub / u.rate / u.units)::numeric, 2)::float8 END, coalesce(v.updated_at, now()), 0
FROM variants v JOIN _vu u ON u.id = v.id
WHERE v.price_rub IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM offer_prices o WHERE o.variant_id = v.id AND o.kind = 'kd');

-- 2. итоги редакций КП (с услугами, добавленными в КП; их сумма — отдельно)
INSERT INTO offer_prices (id, deal_id, variant_id, version_id, kind, source_id, revision_no, total_rub, extras_rub, currency, fx_rate,
                          units, unit_amount, created_by, created_at, seq)
SELECT gen_random_uuid()::text, r.deal_id, e.key, r.version_id, 'revision', r.id, r.no, round((e.value #>> '{}')::numeric, 2)::float8,
       coalesce((SELECT sum(CASE WHEN coalesce(x->>'name', '') <> '' AND coalesce(x->>'price_rub', '') ~ '^-?[0-9]+(\.[0-9]+)?$'
                                 THEN (x->>'price_rub')::float8 ELSE 0 END)
                 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(r.changes->e.key->'extras') = 'array' THEN r.changes->e.key->'extras' ELSE '[]'::jsonb END) x), 0),
       u.cur, u.rate, u.units,
       CASE WHEN u.rate > 0 THEN round(((e.value #>> '{}')::float8 / u.rate / u.units)::numeric, 2)::float8 END,
       r.created_by, r.created_at, r.no
FROM kp_revisions r
CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(r.prices) = 'object' THEN r.prices ELSE '{}'::jsonb END) e
JOIN _vu u ON u.id = e.key
WHERE jsonb_typeof(e.value) = 'number'
  AND NOT EXISTS (SELECT 1 FROM offer_prices o WHERE o.source_id = r.id AND o.variant_id = e.key AND o.kind = 'revision');

COMMIT;
