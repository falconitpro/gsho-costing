-- D-129: Канцелярия — согласование перед подписанием: типовые схемы и маршрут документа.
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS approval JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS office_routes (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    kinds JSONB NOT NULL DEFAULT '[]'::jsonb,
    categories JSONB NOT NULL DEFAULT '[]'::jsonb,
    entity_id VARCHAR(36) REFERENCES legal_entities(id),
    mode VARCHAR(8) NOT NULL DEFAULT 'seq',
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    required BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- D-129: реквизиты, комментарии, обязательный скан
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS comments JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS urgent BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS confidential BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS needs_reply BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS pages INTEGER;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS attachments_count INTEGER;
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS address VARCHAR(512) NOT NULL DEFAULT '';
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS tracking VARCHAR(128) NOT NULL DEFAULT '';   -- D-131: реквизит источника поступления / отправки
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS delivered_on DATE;
-- D-130: рассмотрение и резолюции
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS reviewer_id VARCHAR(36) REFERENCES users(id);
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS resolutions JSONB NOT NULL DEFAULT '[]'::jsonb;
