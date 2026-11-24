#!/usr/bin/env bash
# shellcheck disable=SC2086   # списки файлов SQL намеренно разворачиваются по словам: в именах нет пробелов
# Обновление РАБОЧЕГО сервера. Запускать вручную, после того как версия проверена на стенде:
#   sudo bash /opt/gsho-costing/deploy/production/update.sh          # показать, что изменится, и спросить подтверждение
#   sudo bash /opt/gsho-costing/deploy/production/update.sh --yes    # без вопроса
# Ставится только ПРОВЕРЕННАЯ версия (D-158): ветка origin/verified — последний коммит main, прошедший в CI все проверки
# (backend, сборка, интерфейс по ролям, сквозные сценарии, автоматическая приёмка). Непроверенную — только явно:
#   sudo UNVERIFIED="причина" bash /opt/gsho-costing/deploy/production/update.sh   # причина пишется в /var/log/gsho-unverified-deploys.log
# Порядок: копия в Yandex Object Storage → новые SQL из backend/migrations → новая версия → проверка.
# Без успешной копии ничего не меняется. Автоматического обновления на рабочем сервере нет.
set -euo pipefail
export LC_ALL=C
cd "${GSHO_DIR:-/opt/gsho-costing}"
YES=""; [ "${1:-}" = "--yes" ] && YES=1
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
BIN="${GSHO_BACKUP_BIN:-/usr/local/sbin/gsho-backup.sh}"

timeout 120 git fetch -q origin main
timeout 120 git fetch -q origin verified 2>/dev/null || true
CUR="$(git rev-parse HEAD)"; MAIN="$(git rev-parse origin/main)"
if [ -n "${UNVERIFIED:-}" ]; then
  NEW="$MAIN"
  echo "ВНИМАНИЕ: ставится версия БЕЗ проверки CI ($(git rev-parse --short "$NEW")). Причина: $UNVERIFIED"
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$(git rev-parse --short "$CUR")" "$(git rev-parse --short "$NEW")" "$UNVERIFIED" >> "${GSHO_UNVERIFIED_LOG:-/var/log/gsho-unverified-deploys.log}"
elif git rev-parse -q --verify origin/verified >/dev/null; then
  NEW="$(git rev-parse origin/verified)"
  if [ "$NEW" != "$MAIN" ]; then
    echo "В main есть версии, ещё не прошедшие проверку CI (или не прошедшие её):"
    git log --oneline --no-merges "$NEW..$MAIN" | sed 's/^/  /' | head -20
    echo "Ставится последняя проверенная: $(git rev-parse --short "$NEW"). Статус проверок: https://github.com/falconitpro/gsho-costing/actions"
  fi
  if [ "$CUR" != "$NEW" ] && git merge-base --is-ancestor "$NEW" "$CUR"; then
    echo "На сервере уже версия новее проверенной ($(git rev-parse --short "$CUR")) — ничего не делаю."; exit 0
  fi
else
  echo "ОСТАНОВЛЕНО: нет отметки проверенной версии (ветка verified). Дождитесь зелёного CI на main или запустите с UNVERIFIED=\"причина\"." >&2
  exit 1
fi
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
# настройки копии — из того же файла, что у ежедневной копии по расписанию (systemd читает его так же, как gsho-backup.service)
ENV_FILE="${BACKUP_ENV_FILE:-/etc/gsho/backup.env}"
backup() {
  if [ -z "${RESTIC_REPOSITORY:-}" ] && [ -f "$ENV_FILE" ] && command -v systemd-run >/dev/null; then
    systemd-run --quiet --pipe --wait --collect -p EnvironmentFile="$ENV_FILE" -p Environment=HOME=/root "$BIN" --reason=pre_deploy
  else
    "$BIN" --reason=pre_deploy
  fi
}
rc=0; backup || rc=$?
if [ "$rc" = 75 ]; then sleep "${GSHO_BACKUP_RETRY_SLEEP:-60}"; rc=0; backup || rc=$?; fi
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
DEPLOY_REF="$NEW" BACKUP_REQUIRED=false bash deploy/deploy-remote.sh    # копия уже сделана выше; ставится ровно выбранная версия
echo "== антивирус"   # после пересборки clamd 1–2 мин загружает базы; пока он не готов, загрузки файлов получают 503 (D-068)
CID="$("${DC[@]}" --profile scanner ps -q clamav 2>/dev/null || true)"
if [ -n "$CID" ]; then
  for i in $(seq 1 "${GSHO_SCANNER_WAIT:-60}"); do
    H="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CID" 2>/dev/null || echo unknown)"
    if "${DC[@]}" exec -T backend python -c "from app.core.config import get_settings as g; from app.services.file_scanner import version as v; s=g(); v(s.clamd_host, s.clamd_port, 5)" >/dev/null 2>&1; then
      echo "  антивирус отвечает (через ~$((i * 5)) с; состояние контейнера: $H)"; break
    fi
    [ "$i" = "${GSHO_SCANNER_WAIT:-60}" ] && echo "  ВНИМАНИЕ: антивирус не ответил за $((i * 5)) с (состояние: $H) — файлы не принимаются; см. deploy/clamav/install.md «Антивирус не отвечает»"
    sleep 5
  done
else
  echo "  контейнер антивируса не найден"
fi
echo "== резервное копирование: версия скриптов"   # D-152: исправления скриптов копии доходят до сервера с обычным обновлением
upd=0
install_if_changed() {   # $1 — файл из репозитория, $2 — установленный, $3 — права
  if [ -f "$1" ] && [ -f "$2" ] && ! cmp -s "$1" "$2"; then
    if install -m "$3" "$1" "$2"; then echo "  обновлён $2"; upd=1; else echo "  ВНИМАНИЕ: не удалось обновить $2 — установите вручную (deploy/backup/install.md)"; fi
  fi
}
install_if_changed deploy/backup/gsho-backup.sh "$BIN" 0750
install_if_changed deploy/backup/gsho-backup-status.sh /usr/local/sbin/gsho-backup-status.sh 0750
for u in deploy/backup/*.service deploy/backup/*.timer; do install_if_changed "$u" "/etc/systemd/system/$(basename "$u")" 0644; done
if [ "$upd" = 1 ]; then systemctl daemon-reload 2>/dev/null || true; else echo "  без изменений"; fi
echo "== проверка"
"${DC[@]}" exec -T backend python -m scripts.prod_init check
