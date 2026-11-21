-- D-133: напоминания о сроках писем и эскалация — отметки отправленных напоминаний; D-134: ИИ для писем — журнал обращений
ALTER TABLE office_docs ADD COLUMN IF NOT EXISTS alerts JSONB NOT NULL DEFAULT '{}'::jsonb;
