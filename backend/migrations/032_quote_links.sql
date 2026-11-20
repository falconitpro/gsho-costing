-- D-107: ссылки для ответа логистических компаний на запрос без входа в CRM (в базе — только хэш ссылки)
CREATE TABLE IF NOT EXISTS quote_links (
  id VARCHAR(36) PRIMARY KEY,
  token_hash VARCHAR(64) NOT NULL UNIQUE,
  deal_id VARCHAR(36) NOT NULL REFERENCES deals(id),
  request_id VARCHAR(36) NOT NULL DEFAULT '',
  request_no VARCHAR(64) NOT NULL DEFAULT '',
  email VARCHAR(255) NOT NULL DEFAULT '',
  created_by VARCHAR(36),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  answered_at TIMESTAMPTZ,
  quote_ids TEXT NOT NULL DEFAULT '',
  answers INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_quote_links_deal_id ON quote_links (deal_id);
