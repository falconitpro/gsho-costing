-- Раздел «Финансы», вариант Б — этап 4: пакеты импорта банковской выписки. Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): применить вручную после 002 и 003.
BEGIN;
CREATE TABLE IF NOT EXISTS finance_import_batches (
	id VARCHAR(36) NOT NULL, 
	account_id VARCHAR(36) NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_sha256 VARCHAR(64) NOT NULL, 
	mapping JSONB NOT NULL, 
	rows JSONB NOT NULL, 
	rows_total INTEGER NOT NULL, 
	rows_valid INTEGER NOT NULL, 
	rows_duplicates INTEGER NOT NULL, 
	rows_errors INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	result JSONB NOT NULL, 
	idempotency_key VARCHAR(128), 
	created_by VARCHAR(36), 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES finance_accounts (id), 
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_finance_import_batches_account_id ON finance_import_batches (account_id);
CREATE INDEX IF NOT EXISTS ix_finance_import_batches_file_sha256 ON finance_import_batches (file_sha256);
COMMIT;
