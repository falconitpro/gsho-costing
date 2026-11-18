-- D-079: торги — внутренние курсы валют заказчика (только для торгов)
ALTER TABLE deals ADD COLUMN IF NOT EXISTS negotiation_info JSONB NOT NULL DEFAULT '{}'::jsonb;
