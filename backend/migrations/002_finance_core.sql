-- Раздел «Финансы», вариант Б — этапы 1–3 (docs/finance/). Аддитивный change set: только новые таблицы и индексы.
-- НЕ запускается автоматически. В репозитории нет рабочего Alembic-контура (alembic.ini/env.py отсутствуют), см. D-100.
-- Dev/тестовый стенд: таблицы создаёт init_db() (create_all). Прод: применяет владелец rollout вручную после backup,
-- в одной транзакции; повторный запуск безопасен (IF NOT EXISTS). Откат — DROP пяти новых таблиц (финансовой истории до rollout нет).
BEGIN;
CREATE TABLE IF NOT EXISTS finance_obligations (
	id VARCHAR(36) NOT NULL, 
	source_type VARCHAR(32) NOT NULL, 
	source_id VARCHAR(36) NOT NULL, 
	source_line_key VARCHAR(128) NOT NULL, 
	source_revision VARCHAR(64) NOT NULL, 
	source_line_no INTEGER, 
	deal_id VARCHAR(36), 
	contract_id VARCHAR(36), 
	legal_entity_id VARCHAR(36), 
	counterparty_kind VARCHAR(16) NOT NULL, 
	counterparty_id VARCHAR(36), 
	counterparty_name_snapshot VARCHAR(255) NOT NULL, 
	superseded_by_id VARCHAR(36), 
	direction VARCHAR(3) NOT NULL, 
	nature VARCHAR(16) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	event_code VARCHAR(32) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	legal_due_on DATE, 
	legal_due_text TEXT NOT NULL, 
	event_on DATE, 
	event_is_fact BOOLEAN NOT NULL, 
	forecast_on DATE, 
	basis_snapshot JSONB NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	excluded_reason VARCHAR(255) NOT NULL, 
	sync_status VARCHAR(16) NOT NULL, 
	synced_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_fin_obligation_source UNIQUE (source_type, source_id, source_line_key), 
	CONSTRAINT ck_fin_obligation_amount CHECK (amount >= 0), 
	CONSTRAINT ck_fin_obligation_direction CHECK (direction in ('in','out')), 
	FOREIGN KEY(superseded_by_id) REFERENCES finance_obligations (id)
);

CREATE INDEX IF NOT EXISTS ix_fin_obl_deal_forecast ON finance_obligations (deal_id, forecast_on);
CREATE INDEX IF NOT EXISTS ix_fin_obl_entity_dir ON finance_obligations (legal_entity_id, direction);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_contract_id ON finance_obligations (contract_id);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_deal_id ON finance_obligations (deal_id);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_forecast_on ON finance_obligations (forecast_on);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_is_active ON finance_obligations (is_active);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_legal_due_on ON finance_obligations (legal_due_on);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_legal_entity_id ON finance_obligations (legal_entity_id);
CREATE INDEX IF NOT EXISTS ix_finance_obligations_source_id ON finance_obligations (source_id);

CREATE TABLE IF NOT EXISTS finance_accounts (
	id VARCHAR(36) NOT NULL, 
	legal_entity_id VARCHAR(36) NOT NULL, 
	bank_profile_id VARCHAR(36) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	opened_on DATE, 
	archived_on DATE, 
	note TEXT NOT NULL, 
	created_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(legal_entity_id) REFERENCES legal_entities (id), 
	FOREIGN KEY(bank_profile_id) REFERENCES bank_profiles (id)
);

CREATE INDEX IF NOT EXISTS ix_finance_accounts_bank_profile_id ON finance_accounts (bank_profile_id);
CREATE INDEX IF NOT EXISTS ix_finance_accounts_legal_entity_id ON finance_accounts (legal_entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_account_active_bank ON finance_accounts (bank_profile_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS finance_balance_snapshots (
	id VARCHAR(36) NOT NULL, 
	account_id VARCHAR(36) NOT NULL, 
	as_of TIMESTAMP WITH TIME ZONE NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	external_ref VARCHAR(255) NOT NULL, 
	attachment_id VARCHAR(36), 
	note TEXT NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	confirmed_by VARCHAR(36), 
	confirmed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES finance_accounts (id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_fin_snapshot_account_asof ON finance_balance_snapshots (account_id, as_of);

CREATE TABLE IF NOT EXISTS finance_transactions (
	id VARCHAR(36) NOT NULL, 
	account_id VARCHAR(36), 
	direction VARCHAR(3) NOT NULL, 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	booked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	value_date DATE, 
	counterparty_name VARCHAR(255) NOT NULL, 
	purpose TEXT NOT NULL, 
	external_ref VARCHAR(128) NOT NULL, 
	import_batch_id VARCHAR(36), 
	status VARCHAR(24) NOT NULL, 
	reversal_of_id VARCHAR(36), 
	reversed_by_id VARCHAR(36), 
	origin_type VARCHAR(24) NOT NULL, 
	origin_id VARCHAR(64), 
	idempotency_key VARCHAR(128) NOT NULL, 
	attachment_id VARCHAR(36), 
	note TEXT NOT NULL, 
	created_by VARCHAR(36), 
	posted_by VARCHAR(36), 
	posted_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_fin_tx_amount CHECK (amount > 0), 
	CONSTRAINT ck_fin_tx_direction CHECK (direction in ('in','out')), 
	CONSTRAINT ck_fin_tx_status CHECK (status in ('draft','needs_reconciliation','posted','reversed')), 
	FOREIGN KEY(account_id) REFERENCES finance_accounts (id), 
	FOREIGN KEY(reversal_of_id) REFERENCES finance_transactions (id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_fin_tx_account_booked ON finance_transactions (account_id, booked_at);
CREATE INDEX IF NOT EXISTS ix_finance_transactions_external_ref ON finance_transactions (external_ref);
CREATE INDEX IF NOT EXISTS ix_finance_transactions_status ON finance_transactions (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_tx_account_extref ON finance_transactions (account_id, external_ref) WHERE external_ref <> '' AND status IN ('posted','reversed') AND origin_type <> 'reversal';

CREATE TABLE IF NOT EXISTS finance_allocations (
	id VARCHAR(36) NOT NULL, 
	transaction_id VARCHAR(36) NOT NULL, 
	obligation_id VARCHAR(36) NOT NULL, 
	amount_tx NUMERIC(18, 2) NOT NULL, 
	amount_obligation NUMERIC(18, 2) NOT NULL, 
	fx_rate NUMERIC(18, 8), 
	fx_on DATE, 
	fx_source VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	allocated_on DATE NOT NULL, 
	idempotency_key VARCHAR(160) NOT NULL, 
	note TEXT NOT NULL, 
	created_by VARCHAR(36), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	confirmed_by VARCHAR(36), 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	reversed_by VARCHAR(36), 
	reversed_at TIMESTAMP WITH TIME ZONE, 
	reverse_reason TEXT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_fin_alloc_amount CHECK (amount_tx > 0 AND amount_obligation > 0), 
	CONSTRAINT ck_fin_alloc_status CHECK (status in ('provisional','confirmed','reversed')), 
	FOREIGN KEY(transaction_id) REFERENCES finance_transactions (id), 
	FOREIGN KEY(obligation_id) REFERENCES finance_obligations (id), 
	UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_fin_alloc_obligation_status ON finance_allocations (obligation_id, status);
CREATE INDEX IF NOT EXISTS ix_fin_alloc_tx_status ON finance_allocations (transaction_id, status);

COMMIT;
