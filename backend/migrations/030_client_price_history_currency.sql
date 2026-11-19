-- D-095: история КП клиенту — в валюте сделки (валюта КП и курс на момент назначения цены); рубли справочно
ALTER TABLE client_price_history ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'RUB';
ALTER TABLE client_price_history ADD COLUMN IF NOT EXISTS fx_rate DOUBLE PRECISION;
UPDATE client_price_history h SET currency = UPPER(v.price_currency), fx_rate = v.price_fx_rate
  FROM variants v
 WHERE h.variant_id = v.id AND h.fx_rate IS NULL AND v.price_currency IS NOT NULL AND UPPER(v.price_currency) <> 'RUB' AND v.price_fx_rate > 0;
