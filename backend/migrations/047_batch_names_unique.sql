-- D-147: одинаковые названия партий в одной сделке (экран подставлял «Партия N» по числу партий — после удаления номер повторялся).
-- Повторы получают номер в скобках: «Партия 2», «Партия 2 (2)». Первая по времени создания партия сохраняет название.
UPDATE batches b SET name = left(b.name, 56) || ' (' || d.rn || ')'
FROM (SELECT id, row_number() OVER (PARTITION BY deal_id, lower(trim(name)) ORDER BY created_at, id) AS rn FROM batches) d
WHERE d.id = b.id AND d.rn > 1;
