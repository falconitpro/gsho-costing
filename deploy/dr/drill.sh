#!/usr/bin/env bash
# Еженедельная проверка резерва (D-084, deploy/dr/README.md §5). На РЕЗЕРВНОЙ ВМ от root; запускается сам при старте ВМ
# по расписанию (gsho-dr-drill.service) или вручную:  sudo bash /usr/local/lib/gsho-dr/drill.sh [--poweroff]
# Восстанавливает последнюю копию рабочего сервера в режиме APP_ENV=recovery (без почты, мессенджера и фоновых заданий),
# проверяет /api/health и число записей, пишет отчёт /var/lib/gsho-dr/drill-last.json (без данных), останавливает стенд.
# Флаг «работаем на резерве» НЕ ставится: рабочий сервер ничего не замечает.
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/dr/dr-lib.sh
. "$HERE/dr-lib.sh"

POWEROFF=""; [ "${1:-}" = "--poweroff" ] && POWEROFF=1
[ "$(id -u)" = 0 ] || dr_die "запускать от root"
set -a
if [ -r /etc/gsho/dr.env ]; then
  # shellcheck disable=SC1091  # файл есть только на сервере
  . /etc/gsho/dr.env
fi
set +a
DOMAIN="${DR_STANDBY_DOMAIN:-crm2.dobrov.pro}"
REPORT="$DR_WORK/drill-last.json"
install -d -m 0700 "$DR_WORK"
if [ -L /etc/gsho/backup.env ] || systemctl is-active -q gsho-backup-frequent.timer 2>/dev/null; then
  dr_log "резерв сейчас рабочий (идёт переключение) — проверка не запускается"; exit 0
fi

t0=$(date +%s); OK=false; USERS=""; DEALS=""
finish() {
  local rc=$?
  python3 - "$REPORT" "$OK" "${DR_SNAPSHOT:-}" "${DR_SNAPSHOT_CREATED:-}" "${DR_COMMIT:-}" "$USERS" "$DEALS" "$(( $(date +%s) - t0 ))" "$rc" <<'PY'
import json, sys, datetime
_, path, ok, snap, created, commit, users, deals, secs, rc = sys.argv
json.dump({"finished_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": ok == "true",
           "snapshot": snap, "snapshot_created": created, "commit": commit, "users": users, "deals": deals,
           "seconds": int(secs), "exit_code": int(rc)}, open(path, "w"), ensure_ascii=False)
PY
  dr_compose down >/dev/null 2>&1 || true
  if [ -n "$POWEROFF" ]; then shutdown -h +1 "проверка резерва завершена"; fi
}
trap finish EXIT

export DR_COMPOSE_EXTRA="$HERE/docker-compose.drill.yml"
dr_restore_latest /etc/gsho/restore.env
dr_wait_health "$DOMAIN" || dr_die "нет ответа /api/health"
USERS="$(dr_compose exec -T db psql -U gsho -d gsho -Atc 'select count(*) from users')"
DEALS="$(dr_compose exec -T db psql -U gsho -d gsho -Atc 'select count(*) from deals')"
OK=true
dr_log "проверка пройдена: снимок ${DR_SNAPSHOT:0:8} от $DR_SNAPSHOT_CREATED, пользователей $USERS, сделок $DEALS, $(( $(date +%s) - t0 )) с"
