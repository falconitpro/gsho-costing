#!/usr/bin/env bash
# Возврат с резерва crm2 на рабочий сервер (D-084, deploy/dr/README.md §4). Три шага, по порядку:
#   1. на РЕЗЕРВНОЙ ВМ:    sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=standby
#        останавливает приложение резерва, делает финальную копию, печатает её ID;
#   2. на РАБОЧЕМ сервере: sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=primary --snapshot=<ID>
#        восстанавливает эту копию (пока стоит флаг, рабочий сервер продолжает переадресовывать на crm2);
#   3. на РЕЗЕРВНОЙ ВМ:    sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=finish
#        снимает флаг (через минуту crm.dobrov.pro снова пускает в приложение), выключает стенд резерва.
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/dr/dr-lib.sh
. "$HERE/dr-lib.sh"

STAGE=""; SNAP=""
for a in "$@"; do case "$a" in
  --stage=*) STAGE="${a#--stage=}" ;; --snapshot=*) SNAP="${a#--snapshot=}" ;;
  *) echo "неизвестный аргумент: $a" >&2; exit 2 ;; esac; done
[ "$(id -u)" = 0 ] || dr_die "запускать от root"

case "$STAGE" in
  standby)
    [ -r /etc/gsho/backup-failover.env ] || dr_die "нет /etc/gsho/backup-failover.env — некуда сделать финальную копию"
    dr_log "останавливаю приложение резерва (база остаётся для копии)"
    dr_compose stop caddy frontend >/dev/null
    systemctl disable --now gsho-backup-frequent.timer >/dev/null 2>&1 || true
    out="$(systemd-run --quiet --pipe --wait --collect -p EnvironmentFile=/etc/gsho/backup-failover.env \
      -p Environment=HOME=/root /usr/local/sbin/gsho-backup.sh --reason=manual 2>&1)" || { echo "$out" >&2; dr_die "финальная копия не создана"; }
    dr_compose stop backend >/dev/null
    snap="$(printf '%s\n' "$out" | sed -n 's/.*готово: снимок \([0-9a-f]*\).*/\1/p' | tail -1)"
    echo "Финальная копия резерва: $snap. Дальше на рабочем сервере: sudo bash /usr/local/lib/gsho-dr/failback.sh --stage=primary --snapshot=$snap"
    ;;
  primary)
    [[ "$SNAP" =~ ^[0-9a-f]{8,64}$ ]] || dr_die "укажите --snapshot=<ID> из шага standby"
    [ -r /etc/gsho/restore-failover.env ] || dr_die "нет /etc/gsho/restore-failover.env (ключ чтения копий резерва)"
    [ -f "$GSHO_DIR/deploy/caddy-tls/zz-dr-redirect.caddy" ] || dr_die "флаг не виден рабочему серверу (нет переадресации) — восстановление прервало бы работу на crm2"
    dr_restore_latest /etc/gsho/restore-failover.env "$SNAP"
    echo "Данные резерва восстановлены. Пока стоит флаг, crm.dobrov.pro переадресует на crm2. Дальше на резервной ВМ: --stage=finish"
    ;;
  finish)
    [ -r /etc/gsho/dr.env ] || dr_die "нет /etc/gsho/dr.env"
    set -a
    # shellcheck disable=SC1091  # файл есть только на сервере
    . /etc/gsho/dr.env
    set +a
    dr_flag_delete
    dr_log "флаг снят — в течение минуты crm.dobrov.pro снова пускает в приложение"
    systemctl disable --now gsho-backup.timer >/dev/null 2>&1 || true
    rm -f /etc/gsho/backup.env
    dr_compose down >/dev/null
    echo "Готово. Выключите резервную ВМ в консоли Yandex Cloud (или: shutdown -h now)."
    ;;
  *) echo "укажите --stage=standby | primary | finish" >&2; exit 2 ;;
esac
