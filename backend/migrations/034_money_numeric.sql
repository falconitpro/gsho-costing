-- D-113: денежные суммы и курсы ключевых таблиц — NUMERIC(20,6) вместо double precision (точное десятичное хранение).
-- Значения округляются до 6 знаков после запятой — копейки и курсы не меняются.
BEGIN;
ALTER TABLE factory_price_history ALTER COLUMN unit_price TYPE NUMERIC(20,6) USING round(unit_price::numeric, 6);
ALTER TABLE client_price_history ALTER COLUMN unit_price_rub TYPE NUMERIC(20,6) USING round(unit_price_rub::numeric, 6), ALTER COLUMN total_rub TYPE NUMERIC(20,6) USING round(total_rub::numeric, 6), ALTER COLUMN fx_rate TYPE NUMERIC(20,6) USING round(fx_rate::numeric, 6);
ALTER TABLE logistics_history ALTER COLUMN amount TYPE NUMERIC(20,6) USING round(amount::numeric, 6), ALTER COLUMN per_unit TYPE NUMERIC(20,6) USING round(per_unit::numeric, 6);
ALTER TABLE positions ALTER COLUMN unit_price TYPE NUMERIC(20,6) USING round(unit_price::numeric, 6), ALTER COLUMN options_price TYPE NUMERIC(20,6) USING round(options_price::numeric, 6), ALTER COLUMN zip_price TYPE NUMERIC(20,6) USING round(zip_price::numeric, 6);
ALTER TABLE local_options ALTER COLUMN cost TYPE NUMERIC(20,6) USING round(cost::numeric, 6), ALTER COLUMN install_cost_rub TYPE NUMERIC(20,6) USING round(install_cost_rub::numeric, 6);
ALTER TABLE factory_offers ALTER COLUMN unit_price TYPE NUMERIC(20,6) USING round(unit_price::numeric, 6), ALTER COLUMN transfer_price TYPE NUMERIC(20,6) USING round(transfer_price::numeric, 6);
ALTER TABLE variants ALTER COLUMN price_rub TYPE NUMERIC(20,6) USING round(price_rub::numeric, 6), ALTER COLUMN price_amount TYPE NUMERIC(20,6) USING round(price_amount::numeric, 6), ALTER COLUMN price_fx_rate TYPE NUMERIC(20,6) USING round(price_fx_rate::numeric, 6), ALTER COLUMN price_input_amount TYPE NUMERIC(20,6) USING round(price_input_amount::numeric, 6);
ALTER TABLE kp_revisions ALTER COLUMN price_before_rub TYPE NUMERIC(20,6) USING round(price_before_rub::numeric, 6), ALTER COLUMN price_rub TYPE NUMERIC(20,6) USING round(price_rub::numeric, 6);
ALTER TABLE offer_prices ALTER COLUMN total_rub TYPE NUMERIC(20,6) USING round(total_rub::numeric, 6), ALTER COLUMN extras_rub TYPE NUMERIC(20,6) USING round(extras_rub::numeric, 6), ALTER COLUMN fx_rate TYPE NUMERIC(20,6) USING round(fx_rate::numeric, 6), ALTER COLUMN unit_amount TYPE NUMERIC(20,6) USING round(unit_amount::numeric, 6);
ALTER TABLE negotiation_steps ALTER COLUMN price_before_rub TYPE NUMERIC(20,6) USING round(price_before_rub::numeric, 6), ALTER COLUMN price_rub TYPE NUMERIC(20,6) USING round(price_rub::numeric, 6);
ALTER TABLE plan_fact_entries ALTER COLUMN amount TYPE NUMERIC(20,6) USING round(amount::numeric, 6), ALTER COLUMN fx_rate TYPE NUMERIC(20,6) USING round(fx_rate::numeric, 6);
ALTER TABLE competitor_offers ALTER COLUMN amount TYPE NUMERIC(20,6) USING round(amount::numeric, 6), ALTER COLUMN fx_rate TYPE NUMERIC(20,6) USING round(fx_rate::numeric, 6);
COMMIT;
