-- D-128: Канцелярия — журналы писем, приказов, распоряжений и внутренних документов; подписанты; коды юр. лиц в номерах.
ALTER TABLE legal_entities ADD COLUMN IF NOT EXISTS doc_prefix VARCHAR(8) NOT NULL DEFAULT '';
UPDATE legal_entities SET doc_prefix = CASE code WHEN 'ru' THEN 'РФ' WHEN 'kg' THEN 'КГ' WHEN 'cn' THEN 'КН' ELSE upper(code) END WHERE doc_prefix = '';

CREATE TABLE IF NOT EXISTS office_signers (
    id VARCHAR(36) PRIMARY KEY,
    entity_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    user_id VARCHAR(36) REFERENCES users(id),
    full_name VARCHAR(255) NOT NULL,
    position VARCHAR(255) NOT NULL DEFAULT '',
    basis VARCHAR(255) NOT NULL DEFAULT '',
    poa_id VARCHAR(36),
    valid_from DATE,
    valid_to DATE,
    methods JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_office_signers_entity_id ON office_signers (entity_id);

CREATE TABLE IF NOT EXISTS office_counters (
    id VARCHAR(36) PRIMARY KEY,
    entity_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    journal VARCHAR(16) NOT NULL,
    year INTEGER NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_office_counter UNIQUE (entity_id, journal, year)
);

CREATE TABLE IF NOT EXISTS office_docs (
    id VARCHAR(36) PRIMARY KEY,
    entity_id VARCHAR(36) NOT NULL REFERENCES legal_entities(id),
    kind VARCHAR(16) NOT NULL,
    category VARCHAR(16) NOT NULL DEFAULT '',
    journal VARCHAR(16) NOT NULL DEFAULT '',
    number VARCHAR(48) NOT NULL DEFAULT '',
    seq INTEGER,
    year INTEGER,
    reg_date DATE,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    subject VARCHAR(512) NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    letter_type VARCHAR(16) NOT NULL DEFAULT '',
    channel VARCHAR(16) NOT NULL DEFAULT '',
    counterparty VARCHAR(255) NOT NULL DEFAULT '',
    contact VARCHAR(255) NOT NULL DEFAULT '',
    sender_number VARCHAR(64) NOT NULL DEFAULT '',
    sender_date DATE,
    due_on DATE,
    executor_id VARCHAR(36) REFERENCES users(id),
    co_executor_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    deal_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    deal_section VARCHAR(16) NOT NULL DEFAULT '',
    contract_id VARCHAR(36),
    claim_id VARCHAR(36),
    reply_to_id VARCHAR(36),
    signer_id VARCHAR(36) REFERENCES office_signers(id),
    signed_on DATE,
    sign_method VARCHAR(16) NOT NULL DEFAULT '',
    sent_on DATE,
    effective_on DATE,
    closed_on DATE,
    close_note TEXT NOT NULL DEFAULT '',
    files JSONB NOT NULL DEFAULT '[]'::jsonb,
    history JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_office_docs_entity_id ON office_docs (entity_id);
CREATE INDEX IF NOT EXISTS ix_office_docs_number ON office_docs (number);
CREATE INDEX IF NOT EXISTS ix_office_docs_reply_to_id ON office_docs (reply_to_id);
CREATE INDEX IF NOT EXISTS ix_office_docs_kind_status ON office_docs (kind, status);
-- номер не повторяется внутри журнала юр. лица за год
CREATE UNIQUE INDEX IF NOT EXISTS uq_office_docs_number ON office_docs (entity_id, journal, year, seq) WHERE seq IS NOT NULL;
