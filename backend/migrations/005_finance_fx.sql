-- Раздел «Финансы», вариант Б — этап 4: двухногие конвертации валют (D-131). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): применить вручную после 002–004.
BEGIN;
CREATE TABLE IF NOT EXISTS finance_fx_operations (
	id VARCHAR(36) NOT NULL, 
	sell_transaction_id VARCHAR(36) NOT NULL, 
	buy_transaction_id VARCHAR(36) NOT NULL, 
	fee_transaction_id VARCHAR(36), 
	sell_account_id VARCHAR(36) NOT NULL, 
	buy_account_id VARCHAR(36) NOT NULL, 
	sell_amount NUMERIC(18, 2) NOT NULL, 
	sell_currency VARCHAR(3) NOT NULL, 
	buy_amount NUMERIC(18, 2) NOT NULL, 
	buy_currency VARCHAR(3) NOT NULL, 
	rate NUMERIC(18, 8) NOT NULL, 
	rate_source VARCHAR(64) NOT NULL, 
	executed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	external_ref VARCHAR(128) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	reverse_reason TEXT NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	comment TEXT NOT NULL, 
	created_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_fin_fx_amounts CHECK (sell_amount > 0 AND buy_amount > 0 AND rate > 0), 
	CONSTRAINT ck_fin_fx_currencies CHECK (sell_currency <> buy_currency), 
	UNIQUE (sell_transaction_id), 
	FOREIGN KEY(sell_transaction_id) REFERENCES finance_transactions (id), 
	UNIQUE (buy_transaction_id), 
	FOREIGN KEY(buy_transaction_id) REFERENCES finance_transactions (id), 
	UNIQUE (fee_transaction_id), 
	FOREIGN KEY(fee_transaction_id) REFERENCES finance_transactions (id), 
	FOREIGN KEY(sell_account_id) REFERENCES finance_accounts (id), 
	FOREIGN KEY(buy_account_id) REFERENCES finance_accounts (id), 
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_finance_fx_operations_buy_account_id ON finance_fx_operations (buy_account_id);
CREATE INDEX IF NOT EXISTS ix_finance_fx_operations_sell_account_id ON finance_fx_operations (sell_account_id);
COMMIT;
