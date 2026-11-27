-- 063 (D-197, J-a): ускорение единого поиска Каталога. Индексы pg_trgm по нормализованным номерам
-- (только буквы и цифры, верхний регистр — та же формула, что в app/services/catalog_search.py: norm_sql).
-- Только индексы: данные и столбцы не меняются. Если расширение pg_trgm недоступно (нет прав) — индексы
-- пропускаются с предупреждением, поиск работает и без них, только медленнее на больших объёмах.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
    EXCEPTION WHEN insufficient_privilege OR undefined_file THEN
        RAISE WARNING 'pg_trgm недоступно — индексы поиска не созданы (%)', SQLERRM;
        RETURN;
    END;
    CREATE INDEX IF NOT EXISTS ix_parts_article_norm_trgm ON parts
        USING gin (regexp_replace(upper(article), '[^0-9A-ZА-ЯЁ]', '', 'g') gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS ix_parts_oem_norm_trgm ON parts
        USING gin (regexp_replace(upper(oem_number), '[^0-9A-ZА-ЯЁ]', '', 'g') gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS ix_part_cross_refs_number_norm_trgm ON part_cross_refs
        USING gin (regexp_replace(upper(number), '[^0-9A-ZА-ЯЁ]', '', 'g') gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS ix_parts_name_ru_trgm ON parts USING gin (name_ru gin_trgm_ops);
    CREATE INDEX IF NOT EXISTS ix_client_machines_serial_norm_trgm ON client_machines
        USING gin (regexp_replace(upper(serial), '[^0-9A-ZА-ЯЁ]', '', 'g') gin_trgm_ops);
END $$;
