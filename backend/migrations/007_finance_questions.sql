-- Раздел «Финансы», вариант Б — этап 6: уточнения (D-140). Аддитивно, повторный запуск безопасен.
-- НЕ запускается автоматически (D-100): применить вручную после 002–006.
BEGIN;
CREATE TABLE IF NOT EXISTS finance_questions (
	id VARCHAR(36) NOT NULL, 
	subject_type VARCHAR(24) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	deal_id VARCHAR(36), 
	question TEXT NOT NULL, 
	context_snapshot JSONB NOT NULL, 
	asked_by VARCHAR(36), 
	assignee_id VARCHAR(36) NOT NULL, 
	due_on DATE, 
	status VARCHAR(16) NOT NULL, 
	answer TEXT NOT NULL, 
	answered_by VARCHAR(36), 
	answered_at TIMESTAMP WITH TIME ZONE, 
	reviewed_by VARCHAR(36), 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	review_comment TEXT NOT NULL, 
	source_updated_by VARCHAR(36), 
	source_updated_at TIMESTAMP WITH TIME ZONE, 
	source_update_ref VARCHAR(255) NOT NULL, 
	files JSONB NOT NULL, 
	history JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_fin_question_status CHECK (status in ('open','answered','verified','rejected','closed'))
);
CREATE INDEX IF NOT EXISTS ix_finance_questions_deal_id ON finance_questions (deal_id);
CREATE INDEX IF NOT EXISTS ix_fin_question_assignee_status ON finance_questions (assignee_id, status);
CREATE INDEX IF NOT EXISTS ix_fin_question_subject ON finance_questions (subject_type, subject_id);
COMMIT;
