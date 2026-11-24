-- D-157: закрепление сотрудника за направлением (если роль не совпадает с подразделением)
CREATE TABLE IF NOT EXISTS org_members (
    user_id VARCHAR(36) PRIMARY KEY REFERENCES users(id),
    direction_key VARCHAR(32) NOT NULL,
    updated_by VARCHAR(36),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
