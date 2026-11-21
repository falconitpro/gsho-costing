-- D-140: лист ознакомления с приказами и распоряжениями; D-136: индексы связей писем с договорами и претензиями
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS acks JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS ix_office_docs_contract_id ON office_docs (contract_id);
CREATE INDEX IF NOT EXISTS ix_office_docs_claim_id ON office_docs (claim_id);
