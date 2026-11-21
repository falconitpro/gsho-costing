-- D-148: столбец clients.registry → registry_data (имя «registry» зарезервировано SQLAlchemy — предупреждение при каждом запуске).
-- Если новый столбец уже создан (тестовый стенд досоздаёт столбцы сам) — данные переносятся, старый столбец удаляется.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'clients' AND column_name = 'registry') THEN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'clients' AND column_name = 'registry_data') THEN
      ALTER TABLE clients RENAME COLUMN registry TO registry_data;
    ELSE
      UPDATE clients SET registry_data = registry WHERE (registry_data IS NULL OR registry_data::text IN ('{}', 'null')) AND registry IS NOT NULL;
      ALTER TABLE clients DROP COLUMN registry;
    END IF;
  END IF;
END $$;
