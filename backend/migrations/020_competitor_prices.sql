-- Цены конкурентов при утверждении сделки и цена предложения с НДС / без НДС (D-066).
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на рабочем сервере — update.sh, на стенде с APP_ENV=dev схему создаёт init_db().
BEGIN;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS price_vat_included BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS price_vat_pct DOUBLE PRECISION;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS price_input_amount DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS competitors (
    id          VARCHAR(36)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    name_key    VARCHAR(255) NOT NULL UNIQUE,
    comment     TEXT         NOT NULL DEFAULT '',
    created_by  VARCHAR(36),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS competitor_offers (
    id             VARCHAR(36)      PRIMARY KEY,
    deal_id        VARCHAR(36)      NOT NULL REFERENCES deals (id),
    variant_id     VARCHAR(36),
    competitor_id  VARCHAR(36)      REFERENCES competitors (id),
    "on"           DATE             NOT NULL,
    equipment      VARCHAR(500)     NOT NULL DEFAULT '',
    qty            DOUBLE PRECISION NOT NULL DEFAULT 1,
    amount         DOUBLE PRECISION NOT NULL,
    currency       VARCHAR(3)       NOT NULL DEFAULT 'RUB',
    vat_included   BOOLEAN          NOT NULL DEFAULT FALSE,
    vat_pct        DOUBLE PRECISION NOT NULL DEFAULT 22,
    fx_rate        DOUBLE PRECISION NOT NULL DEFAULT 1,
    fx_on          DATE,
    source         VARCHAR(255)     NOT NULL DEFAULT '',
    comment        TEXT             NOT NULL DEFAULT '',
    created_by     VARCHAR(36),
    deleted_at     TIMESTAMPTZ,
    deleted_by     VARCHAR(36),
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_competitor_offers_deal_id ON competitor_offers (deal_id);
CREATE INDEX IF NOT EXISTS ix_competitor_offers_competitor_id ON competitor_offers (competitor_id);
COMMIT;
