-- Журнал действий недоступен для редактирования и удаления (раздел 2 ТЗ).
-- Применяется после создания схемы (alembic upgrade head / init_db).
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
REVOKE UPDATE, DELETE ON audit_log FROM gsho;          -- прикладная роль БД
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- Версии расчёта неизменяемы
CREATE OR REPLACE FUNCTION deal_versions_immutable() RETURNS trigger AS $$
BEGIN
  IF (OLD.calc_input IS DISTINCT FROM NEW.calc_input) OR (OLD.calc_result IS DISTINCT FROM NEW.calc_result) THEN
    RAISE EXCEPTION 'deal_versions: calc_input/calc_result are immutable';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_versions_immutable ON deal_versions;
CREATE TRIGGER trg_versions_immutable BEFORE UPDATE ON deal_versions FOR EACH ROW EXECUTE FUNCTION deal_versions_immutable();

-- Индексы под отчёты
CREATE INDEX IF NOT EXISTS ix_fx_rates_cur_on ON fx_rates (currency, "on" DESC);
CREATE INDEX IF NOT EXISTS ix_logistics_history_item_route ON logistics_history (item_code, route, "on" DESC);
CREATE INDEX IF NOT EXISTS ix_price_history_factory_model ON factory_price_history (factory_id, model, "on" DESC);
