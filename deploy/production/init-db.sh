#!/usr/bin/env bash
# Первичное создание рабочей базы на НОВОМ сервере (deploy/production/install.md, шаг 4). Запускать один раз, вручную.
# Порядок: таблицы по моделям → SQL backend/migrations по порядку → справочники без демо-данных → проверка.
# На базе, где уже есть сотрудники, скрипт останавливается: обновления существующей базы — deploy/production/update.sh.
set -euo pipefail
export LC_ALL=C   # порядок файлов: 016_… раньше 016b_… (в других локалях «_» при сортировке не учитывается)
cd "${GSHO_DIR:-/opt/gsho-costing}"
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
PSQL=("${DC[@]}" exec -T db psql -U gsho -d gsho -v ON_ERROR_STOP=1 -q)

"${DC[@]}" up -d db
for _ in $(seq 1 30); do "${DC[@]}" exec -T db pg_isready -U gsho >/dev/null 2>&1 && break; sleep 2; done

if [ "$("${PSQL[@]}" -Atc "select to_regclass('public.users') is not null")" = "t" ] \
   && [ "$("${PSQL[@]}" -Atc "select count(*) from users")" != "0" ] && [ "${1:-}" != "--existing" ]; then
  echo "В базе уже есть сотрудники — это не новая база. Остановлено, ничего не изменено." >&2
  echo "Если база перенесена со стенда и нужно только довести схему: $0 --existing" >&2
  exit 1
fi

echo "== таблицы"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.prod_init schema
echo "== SQL"
for f in backend/migrations/*.sql; do
  printf '  %s … ' "$(basename "$f")"
  "${PSQL[@]}" < "$f" >/dev/null
  echo ok
done
echo "== справочники"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.prod_init reference
echo "== проверка"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.prod_init check || true
echo "Дальше: первый пользователь — docker compose run --rm backend python -m scripts.prod_init admin --email … --name \"…\""
