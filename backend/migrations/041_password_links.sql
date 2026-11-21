-- D-132: одноразовые ссылки «Задать пароль» — приглашение нового сотрудника, сброс администратором, «Забыли пароль?»
CREATE TABLE IF NOT EXISTS password_links (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    purpose VARCHAR(16) NOT NULL,
    sent_to VARCHAR(255) NOT NULL DEFAULT '',
    created_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_password_links_user_id ON password_links (user_id);
