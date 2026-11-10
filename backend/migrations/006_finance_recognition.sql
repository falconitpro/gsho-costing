-- Раздел «Финансы», вариант Б — этап 5: события признания выручки/расхода (D-133). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): применить вручную после 002–005. Перенос старых фактов по статьям — POST /api/finance/sync?dry_run=false.
BEGIN;
CREATE TABLE IF NOT EXISTS finance_recognition_events (
	id VARCHAR(36) NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	deal_id VARCHAR(36), 
	obligation_id VARCHAR(36), 
	cost_item_code VARCHAR(32), 
	position_id VARCHAR(36), 
	amount NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	fx_rate NUMERIC(18, 8), 
	fx_source VARCHAR(64) NOT NULL, 
	fx_on DATE, 
	amount_rub NUMERIC(18, 2) NOT NULL, 
	recognized_on DATE NOT NULL, 
	basis_type VARCHAR(32) NOT NULL, 
	document_type VARCHAR(64) NOT NULL, 
	document_number VARCHAR(64) NOT NULL, 
	document_date DATE, 
	attachment_id VARCHAR(36), 
	source_type VARCHAR(32) NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	reversal_reason TEXT NOT NULL, 
	idempotency_key VARCHAR(128), 
	comment TEXT NOT NULL, 
	created_by VARCHAR(36), 
	reversed_by VARCHAR(36), 
	reversed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_fin_recognition_source UNIQUE (source_type, source_id), 
	CONSTRAINT ck_fin_recognition_amount CHECK (amount > 0), 
	CONSTRAINT ck_fin_recognition_kind CHECK (kind in ('revenue','expense')), 
	FOREIGN KEY(obligation_id) REFERENCES finance_obligations (id), 
	UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_fin_recognition_deal ON finance_recognition_events (deal_id, kind, recognized_on);
CREATE INDEX IF NOT EXISTS ix_finance_recognition_events_deal_id ON finance_recognition_events (deal_id);
CREATE INDEX IF NOT EXISTS ix_finance_recognition_events_cost_item_code ON finance_recognition_events (cost_item_code);
COMMIT;
