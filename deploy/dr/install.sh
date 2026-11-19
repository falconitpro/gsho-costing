#!/usr/bin/env bash
# Ставит скрипты резерва crm2 ВНЕ каталога кода (D-084): восстановление переключает /opt/gsho-costing на commit копии,
# а в старом commit каталога deploy/dr может не быть — скрипты и docker-compose.drill.yml пропали бы посреди работы.
# Запускать от root из актуального кода (повторный запуск обновляет установленное):
#   sudo bash /opt/gsho-costing/deploy/dr/install.sh --standby   # резервная ВМ crm2 (сразу после setup.sh)
#   sudo bash /opt/gsho-costing/deploy/dr/install.sh --primary   # рабочий сервер
# Куда: /usr/local/lib/gsho-dr (скрипты), /usr/local/sbin/gsho-backup*.sh, /etc/systemd/system/gsho-*.
set -euo pipefail
ROLE="${1:-}"
DEPLOY="$(cd "$(dirname "$0")/.." && pwd)"
LIB="${DR_LIB:-/usr/local/lib/gsho-dr}"
UNITS="${DR_UNITS:-/etc/systemd/system}"
SBIN="${DR_SBIN:-/usr/local/sbin}"
case "$ROLE" in --standby|--primary) ;; *) echo "укажите --standby или --primary" >&2; exit 2 ;; esac
[ -n "${DR_LIB:-}" ] || [ "$(id -u)" = 0 ] || { echo "запускать от root" >&2; exit 1; }

install -d -m 0755 "$LIB" "$UNITS" "$SBIN"
install -m 0644 "$DEPLOY/dr/dr-lib.sh" "$DEPLOY/dr/docker-compose.drill.yml" "$DEPLOY/dr/README.md" "$LIB/"
install -m 0750 "$DEPLOY/dr/failover.sh" "$DEPLOY/dr/failback.sh" "$DEPLOY/dr/drill.sh" "$DEPLOY/dr/flag-guard.sh" "$LIB/"
install -m 0750 "$DEPLOY/backup/gsho-backup.sh" "$DEPLOY/backup/gsho-backup-status.sh" "$SBIN/"
install -m 0644 "$DEPLOY/backup/gsho-backup.service" "$DEPLOY/backup/gsho-backup.timer" \
  "$DEPLOY/backup/gsho-backup-frequent.service" "$DEPLOY/backup/gsho-backup-frequent.timer" "$UNITS/"
git -C "$DEPLOY/.." rev-parse --short=12 HEAD > "$LIB/VERSION" 2>/dev/null || echo unknown > "$LIB/VERSION"

reload() { [ -n "${DR_LIB:-}" ] || systemctl daemon-reload; }
if [ "$ROLE" = --standby ]; then
  install -m 0644 "$DEPLOY/dr/gsho-dr-drill.service" "$UNITS/"
  reload
  [ -n "${DR_LIB:-}" ] || systemctl enable gsho-dr-drill.service >/dev/null
  # Таймеры копий на резерве не включаются: их включает failover.sh
  echo "резерв: скрипты в $LIB ($(cat "$LIB/VERSION")); переключение — sudo bash $LIB/failover.sh"
else
  install -m 0644 "$DEPLOY/dr/gsho-dr-guard.service" "$DEPLOY/dr/gsho-dr-guard.timer" "$UNITS/"
  # первая ручная установка 19.11 (до этого скрипта): своя копия и override — больше не нужны
  rm -f "$UNITS/gsho-dr-guard.service.d/override.conf" "$SBIN/gsho-dr-guard.sh"
  rmdir "$UNITS/gsho-dr-guard.service.d" 2>/dev/null || true
  reload
  [ -n "${DR_LIB:-}" ] || systemctl enable --now gsho-backup-frequent.timer gsho-dr-guard.timer >/dev/null
  [ -f /etc/gsho/dr-guard.env ] || echo "ВНИМАНИЕ: нет /etc/gsho/dr-guard.env (DR_FLAG_URL) — проверка флага ничего не делает"
  echo "рабочий: скрипты в $LIB ($(cat "$LIB/VERSION")); проверка флага раз в минуту включена"
fi
