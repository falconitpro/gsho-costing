#!/usr/bin/env bash
# shellcheck disable=SC2086   # списки файлов SQL намеренно разворачиваются по словам: в именах нет пробелов
# Обновление РАБОЧЕГО сервера. Запускать вручную, после того как версия проверена на стенде:
#   sudo bash /opt/gsho-costing/deploy/production/update.sh          # показать, что изменится, и спросить подтверждение
#   sudo bash /opt/gsho-costing/deploy/production/update.sh --yes    # без вопроса
# Порядок: копия в Yandex Object Storage → новые SQL из backend/migrations → новая версия → проверка.
# Без успешной копии ничего не меняется. Автоматического обновления на рабочем сервере нет.
set -euo pipefail
export LC_ALL=C
cd "${GSHO_DIR:-/opt/gsho-costing}"
YES=""; [ "${1:-}" = "--yes" ] && YES=1
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
BIN="${GSHO_BACKUP_BIN:-/usr/local/sbin/gsho-backup.sh}"

timeout 120 git fetch -q origin main
CUR="$(git rev-parse HEAD)"; NEW="$(git rev-parse origin/main)"
if [ "$CUR" = "$NEW" ]; then echo "Обновлений нет: $(git rev-parse --short HEAD)"; exit 0; fi

echo "Изменения $(git rev-parse --short "$CUR") → $(git rev-parse --short "$NEW"):"
git log --oneline --no-merges "$CUR..$NEW" | sed 's/^/  /'
NEW_SQL="$(git diff --name-only --diff-filter=A "$CUR" "$NEW" -- backend/migrations | grep '\.sql$' || true)"
CHANGED_SQL="$(git diff --name-only --diff-filter=MD "$CUR" "$NEW" -- backend/migrations | grep '\.sql$' || true)"
if [ -n "$CHANGED_SQL" ]; then
  echo "ОСТАНОВЛЕНО: изменены уже применённые SQL-файлы — это нужно разобрать вручную:" >&2
  printf '  %s\n' $CHANGED_SQL >&2
  exit 1
fi
if [ -n "$NEW_SQL" ]; then echo "Новые изменения базы:"; printf '  %s\n' $NEW_SQL; else echo "Изменений базы нет."; fi

if [ ! -x "$BIN" ]; then
  echo "ОСТАНОВЛЕНО: резервное копирование не установлено ($BIN) — рабочий сервер без копии не обновляется (deploy/backup/install.md)" >&2
  exit 1
fi
if [ -z "$YES" ]; then
  read -r -p "Сделать копию и обновить? Введите «да»: " ANSWER
  [ "$ANSWER" = "да" ] || { echo "Отменено, ничего не изменено."; exit 0; }
fi

echo "== копия перед обновлением"
rc=0; "$BIN" --reason=pre_deploy || rc=$?
if [ "$rc" = 75 ]; then sleep "${GSHO_BACKUP_RETRY_SLEEP:-60}"; rc=0; "$BIN" --reason=pre_deploy || rc=$?; fi
if [ "$rc" != 0 ]; then echo "ОСТАНОВЛЕНО: копия не создана (код $rc) — ничего не изменено" >&2; exit 1; fi

if [ -n "$NEW_SQL" ]; then
  echo "== изменения базы"
  for f in $NEW_SQL; do
    printf '  %s … ' "$(basename "$f")"
    git show "$NEW:$f" | "${DC[@]}" exec -T db psql -U gsho -d gsho -v ON_ERROR_STOP=1 -q >/dev/null
    echo ok
  done
fi

echo "== новая версия"
BACKUP_REQUIRED=false bash deploy/deploy-remote.sh    # копия уже сделана выше
echo "== проверка"
"${DC[@]}" exec -T backend python -m scripts.prod_init check
