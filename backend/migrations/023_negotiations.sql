-- D-074: торги по сделке — шаги снижения цены и изменения условий; сроки и условия оплаты у цен конкурентов
CREATE TABLE IF NOT EXISTS negotiation_steps (
    id VARCHAR(36) PRIMARY KEY,
    deal_id VARCHAR(36) NOT NULL REFERENCES deals(id),
    version_id VARCHAR(36),
    no INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by VARCHAR(36) REFERENCES users(id),
    kind VARCHAR(20) NOT NULL DEFAULT 'price',
    reason VARCHAR(30) NOT NULL DEFAULT 'client',
    comment TEXT NOT NULL DEFAULT '',
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    terms JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    price_before_rub DOUBLE PRECISION,
    price_rub DOUBLE PRECISION,
    margin_pct DOUBLE PRECISION,
    show_discount BOOLEAN NOT NULL DEFAULT false,
    kp_revision_id VARCHAR(36),
    sent_at TIMESTAMPTZ,
    sent_to JSONB NOT NULL DEFAULT '[]'::jsonb,
    client_answer TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_negotiation_steps_deal_id ON negotiation_steps (deal_id);
ALTER TABLE competitor_offers ADD COLUMN IF NOT EXISTS delivery_term VARCHAR(255) NOT NULL DEFAULT '';
ALTER TABLE competitor_offers ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(500) NOT NULL DEFAULT '';
