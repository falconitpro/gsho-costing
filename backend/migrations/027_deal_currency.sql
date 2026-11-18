-- D-080: валюта сделки — по умолчанию валюта предложения поставщика; цена, введённая в другой валюте, пересчитывается в неё
ALTER TABLE deals ADD COLUMN IF NOT EXISTS deal_currency VARCHAR(3);
ALTER TABLE variants ADD COLUMN IF NOT EXISTS price_input_currency VARCHAR(3);
