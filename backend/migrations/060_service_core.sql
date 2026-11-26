-- 060 (D-191, I-0): основа сервисной службы — справочники (виды работ с нормативами, допуски, сроки реакции),
-- обращения, заказ-наряды, события нарядов (только добавление) и чек-листы. Только новые таблицы: прежние не меняются.
-- Машина — существующая client_machines (без второй таблицы машин), площадка — client_sites, контакт — client_contacts.
-- Черновик справочников загружается отдельно: python -m scripts.service_refs_import (пробный прогон по умолчанию).
BEGIN;

CREATE TABLE IF NOT EXISTS service_work_types (
    id VARCHAR(36) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(24) NOT NULL DEFAULT 'repair',
    machine_kind VARCHAR(64) NOT NULL DEFAULT '',
    norm_hours DOUBLE PRECISION,
    crew_size INTEGER NOT NULL DEFAULT 1,
    permit_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    checklist JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_draft BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_permits (
    id VARCHAR(36) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    valid_months INTEGER,
    is_draft BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_sla_levels (
    id VARCHAR(36) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    reaction_hours DOUBLE PRECISION,
    arrival_hours DOUBLE PRECISION,
    restore_hours DOUBLE PRECISION,
    calendar VARCHAR(16) NOT NULL DEFAULT '24x7',
    is_draft BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_requests (
    id VARCHAR(36) PRIMARY KEY,
    number VARCHAR(32) NOT NULL UNIQUE,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id),
    site_id VARCHAR(36) REFERENCES client_sites(id) ON DELETE SET NULL,
    machine_id VARCHAR(36) REFERENCES client_machines(id) ON DELETE SET NULL,
    contact_id VARCHAR(36) REFERENCES client_contacts(id) ON DELETE SET NULL,
    channel VARCHAR(16) NOT NULL DEFAULT 'phone',
    kind VARCHAR(24) NOT NULL DEFAULT 'repair',
    sla_code VARCHAR(32) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'new',
    description TEXT NOT NULL DEFAULT '',
    machine_down BOOLEAN NOT NULL DEFAULT FALSE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reject_reason TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_service_requests_client_id ON service_requests (client_id);
CREATE INDEX IF NOT EXISTS ix_service_requests_site_id ON service_requests (site_id);
CREATE INDEX IF NOT EXISTS ix_service_requests_machine_id ON service_requests (machine_id);
CREATE INDEX IF NOT EXISTS ix_service_requests_status ON service_requests (status);

CREATE TABLE IF NOT EXISTS work_orders (
    id VARCHAR(36) PRIMARY KEY,
    number VARCHAR(32) NOT NULL UNIQUE,
    request_id VARCHAR(36) REFERENCES service_requests(id) ON DELETE SET NULL,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id),
    site_id VARCHAR(36) REFERENCES client_sites(id) ON DELETE SET NULL,
    machine_id VARCHAR(36) REFERENCES client_machines(id) ON DELETE SET NULL,
    work_type_id VARCHAR(36) REFERENCES service_work_types(id) ON DELETE SET NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    engineer_id VARCHAR(36) REFERENCES users(id),
    assigned_by VARCHAR(36) REFERENCES users(id),
    planned_start TIMESTAMPTZ,
    planned_end TIMESTAMPTZ,
    description TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    signed_by_name VARCHAR(255) NOT NULL DEFAULT '',
    signed_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_work_orders_request_id ON work_orders (request_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_client_id ON work_orders (client_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_site_id ON work_orders (site_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_machine_id ON work_orders (machine_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_status ON work_orders (status);
CREATE INDEX IF NOT EXISTS ix_work_orders_engineer_id ON work_orders (engineer_id);

CREATE TABLE IF NOT EXISTS work_order_events (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id),
    kind VARCHAR(24) NOT NULL,
    base_version INTEGER NOT NULL,
    new_version INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor_id VARCHAR(36) REFERENCES users(id),
    device_id VARCHAR(64) NOT NULL DEFAULT '',
    offline BOOLEAN NOT NULL DEFAULT FALSE,
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    result VARCHAR(16) NOT NULL DEFAULT 'applied',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_work_order_events_work_order_id ON work_order_events (work_order_id);
CREATE INDEX IF NOT EXISTS ix_work_order_events_order_received ON work_order_events (work_order_id, received_at);

CREATE OR REPLACE FUNCTION work_order_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'work_order_events: только добавление (изменение и удаление запрещены)';
END $$;
DROP TRIGGER IF EXISTS work_order_events_no_update ON work_order_events;
CREATE TRIGGER work_order_events_no_update BEFORE UPDATE OR DELETE ON work_order_events
    FOR EACH ROW EXECUTE FUNCTION work_order_events_append_only();
DROP TRIGGER IF EXISTS work_order_events_no_truncate ON work_order_events;
CREATE TRIGGER work_order_events_no_truncate BEFORE TRUNCATE ON work_order_events
    FOR EACH STATEMENT EXECUTE FUNCTION work_order_events_append_only();

CREATE TABLE IF NOT EXISTS work_order_checklist_items (
    id VARCHAR(36) PRIMARY KEY,
    work_order_id VARCHAR(36) NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    done BOOLEAN NOT NULL DEFAULT FALSE,
    done_by VARCHAR(36) REFERENCES users(id),
    done_at TIMESTAMPTZ,
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_work_order_checklist_position UNIQUE (work_order_id, position)
);
CREATE INDEX IF NOT EXISTS ix_work_order_checklist_items_work_order_id ON work_order_checklist_items (work_order_id);

COMMIT;
