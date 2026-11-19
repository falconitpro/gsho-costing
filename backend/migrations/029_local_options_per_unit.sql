-- D-089: доукомплектование в РФ — на 1 машину (новые и изменённые опции); прежние — на всю позицию
ALTER TABLE local_options ADD COLUMN IF NOT EXISTS per_unit BOOLEAN NOT NULL DEFAULT false;
