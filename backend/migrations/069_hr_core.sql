-- 069 (D-205, L1): блок «Персонал» — ядро. Работодатель = компания группы (+3 поля в legal_entities), остальное — новые таблицы.
-- Личные и зарплатные данные — только шифротекст (ключ в .env). Кадровые события и оплата труда — только добавление (триггеры ставит init_db).
BEGIN;
ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS is_employer BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS hr_timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow';
ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS hr_external_id VARCHAR(64) NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS hr_departments (
    id VARCHAR(36) PRIMARY KEY,
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    parent_id VARCHAR(36) REFERENCES hr_departments(id),
    code VARCHAR(32) NOT NULL DEFAULT '',
    name VARCHAR(255) NOT NULL,
    head_employee_id VARCHAR(36),
    valid_from DATE NOT NULL,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_departments_employer_id ON hr_departments (employer_id);

CREATE TABLE IF NOT EXISTS hr_positions (
    id VARCHAR(36) PRIMARY KEY,
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    code VARCHAR(32) NOT NULL DEFAULT '',
    name VARCHAR(255) NOT NULL,
    category VARCHAR(24) NOT NULL DEFAULT 'worker',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_hr_position_name UNIQUE (employer_id, name)
);
CREATE INDEX IF NOT EXISTS ix_hr_positions_employer_id ON hr_positions (employer_id);

CREATE TABLE IF NOT EXISTS hr_staff_slots (
    id VARCHAR(36) PRIMARY KEY,
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    department_id VARCHAR(36) NOT NULL REFERENCES hr_departments(id),
    position_id VARCHAR(36) NOT NULL REFERENCES hr_positions(id),
    fte NUMERIC(6, 2) NOT NULL DEFAULT 1,
    work_location VARCHAR(255) NOT NULL DEFAULT '',
    valid_from DATE NOT NULL,
    valid_to DATE,
    supersedes_id VARCHAR(36) REFERENCES hr_staff_slots(id),
    comment TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_staff_slots_employer_id ON hr_staff_slots (employer_id);
CREATE INDEX IF NOT EXISTS ix_hr_staff_slots_department_id ON hr_staff_slots (department_id);

CREATE TABLE IF NOT EXISTS hr_employees (
    id VARCHAR(36) PRIMARY KEY,
    personnel_no VARCHAR(32) NOT NULL UNIQUE,
    last_name VARCHAR(128) NOT NULL,
    first_name VARCHAR(128) NOT NULL,
    middle_name VARCHAR(128) NOT NULL DEFAULT '',
    user_id VARCHAR(36) UNIQUE REFERENCES users(id),
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_employments (
    id VARCHAR(36) PRIMARY KEY,
    employee_id VARCHAR(36) NOT NULL REFERENCES hr_employees(id),
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    kind VARCHAR(16) NOT NULL DEFAULT 'main',
    start_date DATE NOT NULL,
    end_date DATE,
    external_id VARCHAR(64) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_employments_employee_id ON hr_employments (employee_id);
CREATE INDEX IF NOT EXISTS ix_hr_employments_employer_id ON hr_employments (employer_id);

CREATE TABLE IF NOT EXISTS hr_employee_events (
    id VARCHAR(36) PRIMARY KEY,
    employment_id VARCHAR(36) NOT NULL REFERENCES hr_employments(id),
    employee_id VARCHAR(36) NOT NULL REFERENCES hr_employees(id),
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    kind VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    cancels_id VARCHAR(36) REFERENCES hr_employee_events(id),
    comment TEXT NOT NULL DEFAULT '',
    recorded_by VARCHAR(36) REFERENCES users(id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_employee_events_employment_id ON hr_employee_events (employment_id);
CREATE INDEX IF NOT EXISTS ix_hr_employee_events_employee_id ON hr_employee_events (employee_id);
CREATE INDEX IF NOT EXISTS ix_hr_employee_events_employer_id ON hr_employee_events (employer_id);

CREATE TABLE IF NOT EXISTS hr_access_scopes (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id) ON DELETE CASCADE,
    granted_by VARCHAR(36) REFERENCES users(id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_hr_access_scope UNIQUE (user_id, employer_id)
);
CREATE INDEX IF NOT EXISTS ix_hr_access_scopes_user_id ON hr_access_scopes (user_id);

-- личные данные (зашифрованный JSON), оплата труда (только добавление, зашифровано), сканы документов (файл зашифрован)
CREATE TABLE IF NOT EXISTS hr_employee_personal (
    employee_id VARCHAR(36) PRIMARY KEY REFERENCES hr_employees(id),
    data_enc TEXT NOT NULL DEFAULT '',
    kid VARCHAR(16) NOT NULL DEFAULT '',
    fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_by VARCHAR(36) REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_pay_records (
    id VARCHAR(36) PRIMARY KEY,
    employment_id VARCHAR(36) NOT NULL REFERENCES hr_employments(id),
    employee_id VARCHAR(36) NOT NULL REFERENCES hr_employees(id),
    employer_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    kind VARCHAR(16) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    data_enc TEXT NOT NULL DEFAULT '',
    kid VARCHAR(16) NOT NULL DEFAULT '',
    cancels_id VARCHAR(36) REFERENCES hr_pay_records(id),
    comment TEXT NOT NULL DEFAULT '',
    recorded_by VARCHAR(36) REFERENCES users(id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_pay_records_employment_id ON hr_pay_records (employment_id);
CREATE INDEX IF NOT EXISTS ix_hr_pay_records_employee_id ON hr_pay_records (employee_id);
CREATE INDEX IF NOT EXISTS ix_hr_pay_records_employer_id ON hr_pay_records (employer_id);

CREATE TABLE IF NOT EXISTS hr_employee_documents (
    id VARCHAR(36) PRIMARY KEY,
    employee_id VARCHAR(36) NOT NULL REFERENCES hr_employees(id),
    employment_id VARCHAR(36) REFERENCES hr_employments(id),
    kind VARCHAR(24) NOT NULL,
    data_class VARCHAR(12) NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    number VARCHAR(64) NOT NULL DEFAULT '',
    issued_on DATE,
    valid_until DATE,
    file_name VARCHAR(255) NOT NULL,
    mime VARCHAR(128) NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    sha256 VARCHAR(64) NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL,
    kid VARCHAR(16) NOT NULL DEFAULT '',
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    comment TEXT NOT NULL DEFAULT '',
    uploaded_by VARCHAR(36) REFERENCES users(id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_hr_employee_documents_employee_id ON hr_employee_documents (employee_id);
COMMIT;
