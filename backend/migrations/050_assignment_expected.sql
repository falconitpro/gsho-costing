-- D-156: поручение — ожидаемый результат (критерий приёмки), отдельно от подробностей
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS expected_result TEXT NOT NULL DEFAULT '';
