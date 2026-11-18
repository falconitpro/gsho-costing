-- D-076: торги — статус «На рассмотрении клиента» с напоминанием
ALTER TABLE negotiation_steps ADD COLUMN IF NOT EXISTS review JSONB NOT NULL DEFAULT '{}'::jsonb;
