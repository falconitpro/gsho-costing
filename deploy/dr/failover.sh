#!/usr/bin/env bash
# Переключение на резервный сервер crm2 (D-084, deploy/dr/README.md §3). Запускать на РЕЗЕРВНОЙ ВМ в Yandex Cloud от root:
#   sudo bash /usr/local/lib/gsho-dr/failover.sh          # спросит подтверждение
#   sudo bash /usr/local/lib/gsho-dr/failover.sh --yes
# Скрипты стоят вне каталога кода (deploy/dr/install.sh): восстановление переключает код на commit копии.
# Порядок: флаг «работаем на резерве» → последняя копия рабочего сервера → запуск стенда на crm2 → проверка
#          → частые копии резерва в отдельное хранилище → IP для записи crm2.
# Решение о переключении принимает владелец. Рабочий сервер, если оживёт, сам переадресует crm → crm2 (flag-guard.sh).
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/dr/dr-lib.sh
. "$HERE/dr-lib.sh"

YES=""; [ "${1:-}" = "--yes" ] && YES=1
[ "$(id -u)" = 0 ] || dr_die "запускать от root"
[ -r /etc/gsho/dr.env ] || dr_die "нет /etc/gsho/dr.env (deploy/dr/README.md §2)"
set -a
# shellcheck disable=SC1091  # файл есть только на сервере
. /etc/gsho/dr.env
set +a
DOMAIN="${DR_STANDBY_DOMAIN:-crm2.dobrov.pro}"
grep -q "^DOMAIN=$DOMAIN$" "$GSHO_DIR/.env" || dr_die "в $GSHO_DIR/.env DOMAIN не $DOMAIN — это не резервная ВМ?"

if [ -z "$YES" ]; then
  echo "Переключение на резерв: рабочий сервер перестанет принимать данные, сотрудники перейдут на https://$DOMAIN."
  read -r -p "Переключаемся? Введите «да»: " A
  [ "$A" = "да" ] || { echo "Отменено, ничего не изменено."; exit 0; }
fi

dr_log "ставлю флаг «работаем на резерве»"
dr_flag_put "{\"active\":true,\"target\":\"https://$DOMAIN\",\"since\":\"$(date -u +%FT%TZ)\"}"

dr_restore_latest /etc/gsho/restore.env
dr_wait_health "$DOMAIN" || dr_die "стенд не ответил на /api/health — смотрите: docker compose logs backend"

if [ -r /etc/gsho/backup-failover.env ]; then
  dr_log "включаю копии резерва каждые 15 минут ($(grep -o '^RESTIC_REPOSITORY=.*' /etc/gsho/backup-failover.env | cut -d= -f2-))"
  ln -sfn /etc/gsho/backup-failover.env /etc/gsho/backup.env
  systemctl enable --now gsho-backup-frequent.timer gsho-backup.timer >/dev/null
else
  dr_log "ВНИМАНИЕ: нет /etc/gsho/backup-failover.env — копии резерва не включены"
fi

IP="$(curl -fsS -m 10 https://ifconfig.me 2>/dev/null || echo '?')"
if [ -n "${DR_DNS_UPDATE_CMD:-}" ]; then
  dr_log "обновляю запись $DOMAIN → $IP"
  DR_IP="$IP" bash -c "$DR_DNS_UPDATE_CMD" || dr_log "ВНИМАНИЕ: запись не обновлена — сделайте вручную у R01"
fi

dr_notify "CRM переключён на резервный сервер $DOMAIN" "Переключение выполнено $(date -u '+%F %T') UTC.
Данные на $DR_SNAPSHOT_CREATED UTC (снимок ${DR_SNAPSHOT:0:8}). Сотрудники работают на https://$DOMAIN.
Возврат на рабочий сервер — раздел 5 инструкции администратора."

cat <<TXT

ГОТОВО: резерв работает на https://$DOMAIN
  данные на:  $DR_SNAPSHOT_CREATED (снимок ${DR_SNAPSHOT:0:8}, commit ${DR_COMMIT:0:12})
  IP сервера: $IP — статический; A-запись ${DOMAIN%%.*} у R01 указывает на него постоянно (проверка: nslookup $DOMAIN)
Сообщение сотрудникам: «CRM временно работает на https://$DOMAIN. Данные на $DR_SNAPSHOT_CREATED UTC,
введённое позже — повторите. Вход прежний».
Возврат на рабочий сервер — deploy/dr/README.md §4 (failback.sh).
TXT
