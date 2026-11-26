-- 053 (D-181): реестр событий изменений — только добавление. Роль «Инженер» и группы доступа K/I хранятся в коде
-- (roles — JSON у пользователя, уровни — DEFAULT_MATRIX), таблиц для них не нужно.
BEGIN;

CREATE TABLE IF NOT EXISTS change_events (
    seq BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    xid BIGINT NOT NULL DEFAULT (pg_current_xact_id()::text)::bigint,
    actor_id VARCHAR(36),
    entity VARCHAR(64) NOT NULL,
    entity_id VARCHAR(128) NOT NULL,
    action VARCHAR(32) NOT NULL,
    deal_id VARCHAR(36)
);
CREATE INDEX IF NOT EXISTS ix_change_events_entity ON change_events (entity);
CREATE INDEX IF NOT EXISTS ix_change_events_deal_id ON change_events (deal_id);
CREATE OR REPLACE FUNCTION change_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'change_events: только добавление (изменение и удаление запрещены)';
END $$;
DROP TRIGGER IF EXISTS change_events_no_update ON change_events;
CREATE TRIGGER change_events_no_update BEFORE UPDATE OR DELETE ON change_events
    FOR EACH ROW EXECUTE FUNCTION change_events_append_only();
DROP TRIGGER IF EXISTS change_events_no_truncate ON change_events;
CREATE TRIGGER change_events_no_truncate BEFORE TRUNCATE ON change_events
    FOR EACH STATEMENT EXECUTE FUNCTION change_events_append_only();

COMMIT;
