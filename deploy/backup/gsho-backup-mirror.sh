#!/usr/bin/env bash
# Дубль копий рабочего CRM в S3 Timeweb (D-084): зеркало репозитория restic из Yandex Object Storage.
#   sudo gsho-backup-mirror.sh --setup   # один раз: спросит ключи пользователя бакета Timeweb (ввод скрыт)
#   sudo gsho-backup-mirror.sh           # копирование (запускает gsho-backup-mirror.timer раз в сутки)
# Зеркало — это тот же репозиторий restic (пароль тот же), его можно восстанавливать как обычный.
# `rclone copy`, а не `sync`: удаление в источнике (очистка restic, ошибка, злоумышленник) дубль не трогает.
# Источник читается ключом только для чтения (sa-crm-prod-restore из /etc/gsho/restore-failover.env).
set -euo pipefail
umask 077
ENV="${MIRROR_ENV:-/etc/gsho/backup-mirror.env}"
STATUS_DIR="${MIRROR_STATUS_DIR:-/var/lib/gsho-backup/status}"

setup() {
  [ "$(id -u)" = 0 ] || { echo "запускать от root" >&2; exit 1; }
  [ -e "$ENV" ] && { echo "$ENV уже есть — удалите его, чтобы задать ключи заново" >&2; exit 1; }
  local src=/etc/gsho/restore-failover.env kid sec
  [ -r "$src" ] || { echo "нет $src (ключ чтения копий в Яндексе)" >&2; exit 1; }
  read -r -p "Timeweb S3 Access Key: " kid
  read -r -s -p "Timeweb S3 Secret Access Key (ввод не виден): " sec; echo
  if [ -z "$kid" ] || [ -z "$sec" ]; then echo "ключи не введены — ничего не записано" >&2; exit 1; fi
  {
    echo "# создано gsho-backup-mirror.sh --setup (D-084); секреты не выводить"
    echo "MIRROR_SRC_KEY_ID=$(sed -n 's/^AWS_ACCESS_KEY_ID=//p' "$src")"
    echo "MIRROR_SRC_SECRET=$(sed -n 's/^AWS_SECRET_ACCESS_KEY=//p' "$src")"
    echo "MIRROR_SRC_PATH=berghaus-crm-prod-backup/crm-prod"
    echo "MIRROR_DST_KEY_ID=$kid"
    echo "MIRROR_DST_SECRET=$sec"
    echo "MIRROR_DST_PATH=berghaus-crm-prod-copy/crm-prod"
  } > "$ENV"
  chmod 600 "$ENV"
  echo "записан $ENV; первое копирование:"
}

SETUP=""
if [ "${1:-}" = "--setup" ]; then SETUP=1; setup; fi
command -v rclone >/dev/null || { echo "нет rclone: apt-get install -y rclone" >&2; exit 1; }
[ -r "$ENV" ] || { echo "нет $ENV — сначала: gsho-backup-mirror.sh --setup" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$ENV"
set +a

export RCLONE_CONFIG_SRC_TYPE=s3 RCLONE_CONFIG_SRC_PROVIDER=Other RCLONE_CONFIG_SRC_REGION=ru-central1 \
  RCLONE_CONFIG_SRC_ENDPOINT="${MIRROR_SRC_ENDPOINT:-https://storage.yandexcloud.net}" \
  RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$MIRROR_SRC_KEY_ID" RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$MIRROR_SRC_SECRET"
export RCLONE_CONFIG_DST_TYPE=s3 RCLONE_CONFIG_DST_PROVIDER=Other RCLONE_CONFIG_DST_REGION="${MIRROR_DST_REGION:-ru-1}" \
  RCLONE_CONFIG_DST_ENDPOINT="${MIRROR_DST_ENDPOINT:-https://s3.twcstorage.ru}" \
  RCLONE_CONFIG_DST_ACCESS_KEY_ID="$MIRROR_DST_KEY_ID" RCLONE_CONFIG_DST_SECRET_ACCESS_KEY="$MIRROR_DST_SECRET"

t0=$(date +%s); rc=0
# locks/ не копируем: это временные блокировки restic, в зеркале они были бы «вечными»
out="$(rclone copy "src:$MIRROR_SRC_PATH" "dst:$MIRROR_DST_PATH" --exclude 'locks/**' --s3-no-check-bucket \
  --transfers 4 --retries 3 --stats-one-line --stats 0 -v 2>&1)" || rc=$?
printf '%s\n' "$out" | tail -3
install -d -m 0750 "$STATUS_DIR"
python3 - "$STATUS_DIR/mirror-last.json" "$rc" "$(( $(date +%s) - t0 ))" <<'PY'
import json, sys, datetime
path, rc, secs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
json.dump({"finished_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "ok": rc == 0, "exit_code": rc, "seconds": secs}, open(path, "w"))
PY
if [ "$rc" = 0 ]; then echo "gsho-backup-mirror: готово"; else echo "gsho-backup-mirror: ОШИБКА (код $rc)" >&2; fi
if [ -n "$SETUP" ] && [ "$rc" = 0 ]; then systemctl enable --now gsho-backup-mirror.timer >/dev/null && echo "таймер gsho-backup-mirror.timer включён (раз в сутки)"; fi
exit "$rc"
