-- D-085: цена фиксируется за 1 шт., итог — справочно на количество
ALTER TABLE variants ADD COLUMN IF NOT EXISTS price_input_units INTEGER;
