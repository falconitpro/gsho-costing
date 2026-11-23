-- D-154: «Пульт» ЗГД v2 — руководители направлений и управленческие поручения с полным циклом (только новые таблицы)
CREATE TABLE IF NOT EXISTS org_directions (
    key VARCHAR(32) PRIMARY KEY,
    head_id VARCHAR(36) REFERENCES users(id),
    updated_by VARCHAR(36),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assignments (
    id VARCHAR(36) PRIMARY KEY,
    number VARCHAR(24) NOT NULL,
    seq INTEGER NOT NULL,
    year INTEGER NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'task',
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    mode VARCHAR(8) NOT NULL DEFAULT 'direct',
    direction_key VARCHAR(32) NOT NULL DEFAULT '',
    author_id VARCHAR(36) NOT NULL REFERENCES users(id),
    assignee_id VARCHAR(36) NOT NULL REFERENCES users(id),
    executor_id VARCHAR(36) REFERENCES users(id),
    head_id VARCHAR(36) REFERENCES users(id),
    status VARCHAR(16) NOT NULL DEFAULT 'new',
    due_on DATE NOT NULL,
    first_due_on DATE NOT NULL,
    source_url VARCHAR(300) NOT NULL DEFAULT '',
    source_key VARCHAR(160) NOT NULL DEFAULT '',
    source_title VARCHAR(255) NOT NULL DEFAULT '',
    report TEXT NOT NULL DEFAULT '',
    block_reason TEXT NOT NULL DEFAULT '',
    accepted_at TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ,
    done_at TIMESTAMPTZ,
    done_by VARCHAR(36),
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_assignments_number ON assignments (number);
CREATE INDEX IF NOT EXISTS ix_assignments_author_id ON assignments (author_id);
CREATE INDEX IF NOT EXISTS ix_assignments_assignee_id ON assignments (assignee_id);
CREATE INDEX IF NOT EXISTS ix_assignments_head_id ON assignments (head_id);
CREATE INDEX IF NOT EXISTS ix_assignments_status_due ON assignments (status, due_on);

CREATE TABLE IF NOT EXISTS assignment_events (
    id VARCHAR(36) PRIMARY KEY,
    assignment_id VARCHAR(36) NOT NULL REFERENCES assignments(id),
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    by_id VARCHAR(36),
    action VARCHAR(24) NOT NULL,
    from_status VARCHAR(16) NOT NULL DEFAULT '',
    to_status VARCHAR(16) NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_assignment_events_assignment_id ON assignment_events (assignment_id);
