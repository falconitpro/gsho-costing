#!/usr/bin/env bash
# Краткий статус резервного копирования без секретов — вывод можно вставить в диалог или отчёт.
#   sudo gsho-backup-status.sh            последний запуск + 5 последних снимков
#   sudo gsho-backup-status.sh --check    дополнительно restic check (только метаданные)
# Читает /etc/gsho/backup.env, но не печатает его значения.
set -Eeuo pipefail
umask 077
ENV_FILE="${BACKUP_ENV_FILE:-/etc/gsho/backup.env}"
if [ -r "$ENV_FILE" ]; then set -a; # shellcheck disable=SC1090
  . "$ENV_FILE"; set +a; fi
STATUS_DIR="${BACKUP_STATUS_DIR:-/var/lib/gsho-backup/status}"

echo "== Последний запуск ($STATUS_DIR/last-run.json)"
if [ -r "$STATUS_DIR/last-run.json" ]; then
  python3 - "$STATUS_DIR/last-run.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
keys = ["reason", "status", "started_at", "finished_at", "snapshot_id", "git_commit", "schema_revision", "storage_provider",
        "database_bytes", "files_bytes", "files_count", "manifest_sha256", "check_status", "retention_status", "error_code", "error_summary"]
for k in keys:
    v = d.get(k)
    if k == "snapshot_id" and v:
        v = v[:8]
    print(f"  {k:17} {v if v not in (None, '') else '—'}")
PY
else
  echo "  нет файла статуса — копирование ещё не запускалось"
fi

echo "== Таймер"
systemctl list-timers gsho-backup.timer --no-pager 2>/dev/null | sed -n '1,2p' || echo "  systemd недоступен"

echo "== Последние снимки"
if [ -n "${RESTIC_REPOSITORY:-}" ]; then
  restic snapshots --compact --latest 5 --tag gsho 2>&1 | grep -v -i -E 'password|secret|key' || echo "  restic: ошибка доступа к репозиторию"
  if [ "${1:-}" = "--check" ]; then
    echo "== restic check"
    if restic check > /dev/null 2>&1; then echo "  ok"; else echo "  FAILED"; fi
  fi
else
  echo "  RESTIC_REPOSITORY не задан (нет $ENV_FILE)"
fi
