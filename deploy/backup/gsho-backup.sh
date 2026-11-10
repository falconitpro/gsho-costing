#!/usr/bin/env bash
# Зашифрованная резервная копия CRM в Yandex Object Storage (docs/operations/03 §4.2).
#
#   gsho-backup.sh --reason=daily|pre_deploy|manual|restore_check
#
# Запускается systemd (gsho-backup.service, EnvironmentFile=/etc/gsho/backup.env) или вручную администратором.
# Шаги: lock → staging → pg_dump --format=custom → pg_restore --list → архив вложений → manifest + SHA-256
#       → restic backup (шифрование на сервере) → проверка снимка → restic check → forget --prune → статус.
# Статус пишется атомарно в $BACKUP_STATUS_DIR/last-run.json и runs/<время>_<run_key>.json (только безопасные поля).
# Секреты (RESTIC_PASSWORD, ключи SA) читаются restic из окружения; скрипт их не печатает и не передаёт аргументами.
#
# Коды выхода: 0 — копия создана (возможно, с предупреждением retention); 1 — ошибка; 2 — неверные аргументы;
#              75 — уже выполняется другой запуск (already_running).
# shellcheck disable=SC2317  # функции вызываются из trap
set -Eeuo pipefail
umask 077

REASON=""
for a in "$@"; do
  case "$a" in
    --reason=*) REASON="${a#--reason=}" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "gsho-backup: неизвестный аргумент: $a" >&2; exit 2 ;;
  esac
done
case "$REASON" in daily|pre_deploy|manual|restore_check) ;; *) echo "gsho-backup: укажите --reason=daily|pre_deploy|manual|restore_check" >&2; exit 2 ;; esac

STATUS_DIR="${BACKUP_STATUS_DIR:-/var/lib/gsho-backup/status}"
STAGING_ROOT="${BACKUP_STAGING_DIR:-/var/lib/gsho-backup/staging}"
LOCK_FILE="${BACKUP_LOCK_FILE:-/run/lock/gsho-backup.lock}"
COMPOSE_DIR="${BACKUP_COMPOSE_DIR:-/opt/gsho-costing}"
DB_SOURCE="${BACKUP_DB_SOURCE:-compose}"          # compose — pg_dump внутри контейнера db; direct — pg_dump по PG* (тесты, recovery)
FILES_SOURCE="${BACKUP_FILES_SOURCE:-compose}"    # compose — tar из контейнера backend; dir — каталог BACKUP_FILES_DIR
MIN_FREE_MB="${BACKUP_MIN_FREE_MB:-1024}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-90}"
KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"
DEEP_CHECK_WEEKDAY="${BACKUP_DEEP_CHECK_WEEKDAY:-7}"   # 1..7 (пн..вс): в этот день restic check читает 5% данных
HISTORY_KEEP="${BACKUP_HISTORY_KEEP:-120}"
HOST_TAG="${BACKUP_HOST_TAG:-gsho-prod}"
export TZ="${BACKUP_TIMEZONE:-Europe/Moscow}"

RUN_KEY="$(cat /proc/sys/kernel/random/uuid)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STEP="config_missing"
STAGE=""
STATUS="running"; FINISHED_AT=""; SNAPSHOT=""; GIT_COMMIT=""; SCHEMA_REV=""; PROVIDER="unknown"
DB_BYTES=""; FILES_BYTES=""; FILES_COUNT=""; MANIFEST_SHA=""; CHECK_STATUS=""; CHECKED_AT=""; RETENTION_STATUS=""
ERROR_CODE=""; ERROR_SUMMARY=""

log() { printf '%s gsho-backup[%s] %s\n' "$(date -Is)" "${RUN_KEY:0:8}" "$*" >&2; }

json_str() {   # безопасная JSON-строка или null; входят только значения, сформированные этим скриптом
  if [ -z "${1:-}" ]; then printf 'null'; else printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\000-\037')"; fi
}
json_num() { if [[ "${1:-}" =~ ^[0-9]+$ ]]; then printf '%s' "$1"; else printf 'null'; fi; }

write_status() {
  [ -d "$STATUS_DIR" ] || return 1
  mkdir -p "$STATUS_DIR/runs" || return 1
  chmod 0755 "$STATUS_DIR" "$STATUS_DIR/runs" 2>/dev/null || true   # статус без секретов читает backend (mount :ro)
  local tmp="$STATUS_DIR/.last-run.json.tmp.$$" body
  body=$(printf '{"schema":1,"run_key":%s,"reason":%s,"status":%s,"started_at":%s,"finished_at":%s,"snapshot_id":%s,"git_commit":%s,"schema_revision":%s,"storage_provider":%s,"database_bytes":%s,"files_bytes":%s,"files_count":%s,"manifest_sha256":%s,"check_status":%s,"checked_at":%s,"retention_status":%s,"error_code":%s,"error_summary":%s}\n' \
    "$(json_str "$RUN_KEY")" "$(json_str "$REASON")" "$(json_str "$STATUS")" "$(json_str "$STARTED_AT")" "$(json_str "$FINISHED_AT")" \
    "$(json_str "$SNAPSHOT")" "$(json_str "$GIT_COMMIT")" "$(json_str "$SCHEMA_REV")" "$(json_str "$PROVIDER")" \
    "$(json_num "$DB_BYTES")" "$(json_num "$FILES_BYTES")" "$(json_num "$FILES_COUNT")" "$(json_str "$MANIFEST_SHA")" \
    "$(json_str "$CHECK_STATUS")" "$(json_str "$CHECKED_AT")" "$(json_str "$RETENTION_STATUS")" "$(json_str "$ERROR_CODE")" "$(json_str "$ERROR_SUMMARY")")
  printf '%s' "$body" > "$tmp" && sync "$tmp" 2>/dev/null; chmod 0644 "$tmp" || return 1
  cp -p "$tmp" "$STATUS_DIR/runs/.tmp.$$" && mv -f "$STATUS_DIR/runs/.tmp.$$" "$STATUS_DIR/runs/${STARTED_AT//:/}_${RUN_KEY}.json" || return 1
  mv -f "$tmp" "$STATUS_DIR/last-run.json" || return 1
  # история: не больше HISTORY_KEEP файлов (это журнал статусов без данных, не копия)
  local files=("$STATUS_DIR"/runs/*.json) i
  if [ "${#files[@]}" -gt "$HISTORY_KEEP" ]; then
    for ((i = 0; i < ${#files[@]} - HISTORY_KEEP; i++)); do rm -f "${files[$i]}"; done   # glob отсортирован: старые — первые
  fi
  return 0
}

cleanup() {
  if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then rm -rf --one-file-system "$STAGE"; fi
}

on_error() {
  local rc=$?
  trap - ERR
  STATUS="failed"; FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  ERROR_CODE="$STEP"
  if [ "$STEP" != "config_missing" ] && [ -d "$STAGING_ROOT" ]; then
    local free_mb; free_mb=$(df -Pm "$STAGING_ROOT" 2>/dev/null | awk 'NR==2{print $4}')
    if [[ "${free_mb:-}" =~ ^[0-9]+$ ]] && [ "$free_mb" -lt "$MIN_FREE_MB" ]; then ERROR_CODE="disk_full"; fi
  fi
  ERROR_SUMMARY="шаг ${STEP}, код выхода ${rc}"
  cleanup
  write_status || log "не удалось записать статус"
  log "ОШИБКА: ${ERROR_CODE} (${ERROR_SUMMARY})"
  exit 1
}
trap on_error ERR
trap cleanup EXIT
trap 'STEP=interrupted; on_error' INT TERM

compose() { (cd "$COMPOSE_DIR" && docker compose "$@"); }

# ---------- 1. конфигурация и блокировка ----------
missing=()
[ -n "${RESTIC_REPOSITORY:-}" ] || missing+=("RESTIC_REPOSITORY")
[ -n "${RESTIC_PASSWORD:-}${RESTIC_PASSWORD_FILE:-}" ] || missing+=("RESTIC_PASSWORD")
if [[ "${RESTIC_REPOSITORY:-}" == s3:* ]]; then
  for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do [ -n "${!v:-}" ] || missing+=("$v"); done
fi
mkdir -p "$STATUS_DIR" 2>/dev/null || true
if [ "${#missing[@]}" -gt 0 ]; then
  log "не заданы: ${missing[*]}"
  ERROR_SUMMARY="не заданы: ${missing[*]}"
  STATUS="failed"; ERROR_CODE="config_missing"; FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  trap - ERR; write_status || true; exit 1
fi

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  trap - ERR
  log "already_running: другой запуск ещё выполняется — этот завершается без изменений"
  exit 75
fi

# ---------- 2. staging ----------
STEP="staging_failed"
mkdir -p "$STAGING_ROOT"
find "$STAGING_ROOT" -mindepth 1 -maxdepth 1 -name 'gsho-backup' -exec rm -rf --one-file-system {} +   # остаток прерванного запуска
STAGE="$STAGING_ROOT/gsho-backup"     # постоянный путь под lock: restic видит один и тот же путь и выбирает родительский снимок
mkdir -m 0700 "$STAGE"
free_mb=$(df -Pm "$STAGING_ROOT" | awk 'NR==2{print $4}')
if [ "${free_mb:-0}" -lt "$MIN_FREE_MB" ]; then STEP="disk_full"; false; fi

GIT_COMMIT="$(git -C "$COMPOSE_DIR" rev-parse HEAD 2>/dev/null || true)"
[[ "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]] || GIT_COMMIT=""
SCHEMA_REV=""
for f in "$COMPOSE_DIR"/backend/migrations/[0-9][0-9][0-9]_*.sql; do [ -e "$f" ] && SCHEMA_REV="$(basename "$f" .sql)"; done   # последний по имени
write_status || log "статус running не записан (каталог $STATUS_DIR недоступен)"

# ---------- 3. база данных ----------
STEP="db_dump_failed"
if [ "$DB_SOURCE" = "compose" ]; then
  compose exec -T db pg_dump -U "${BACKUP_DB_USER:-gsho}" -d "${BACKUP_DB_NAME:-gsho}" --format=custom --no-owner > "$STAGE/db.dump"
else
  pg_dump --format=custom --no-owner > "$STAGE/db.dump"    # PGHOST/PGUSER/PGDATABASE; пароль — PGPASSFILE, не аргументом
fi
STEP="dump_unreadable"
if [ "$DB_SOURCE" = "compose" ]; then
  compose exec -T db pg_restore --list < "$STAGE/db.dump" > "$STAGE/db.list"
else
  pg_restore --list "$STAGE/db.dump" > "$STAGE/db.list"
fi
[ -s "$STAGE/db.list" ] || false
DB_BYTES=$(stat -c %s "$STAGE/db.dump")

# ---------- 4. вложения ----------
STEP="unsupported_storage"
if [ "$FILES_SOURCE" = "compose" ]; then
  # shellcheck disable=SC2016  # переменная раскрывается внутри контейнера
  PROVIDER="$(compose exec -T backend sh -c 'printf %s "${STORAGE_PROVIDER:-local}"')"
else
  PROVIDER="${BACKUP_STORAGE_PROVIDER:-local}"
fi
case "$PROVIDER" in local) ;; s3) false ;; *) PROVIDER="unknown"; false ;; esac
STEP="files_failed"
if [ "$FILES_SOURCE" = "compose" ]; then
  # shellcheck disable=SC2016
  compose exec -T backend sh -c 'cd "${STORAGE_LOCAL_ROOT:-/app/data/storage}" && tar -cf - .' > "$STAGE/files.tar"
else
  [ -d "${BACKUP_FILES_DIR:-}" ] || false
  tar -C "$BACKUP_FILES_DIR" -cf - . > "$STAGE/files.tar"
fi
FILES_BYTES=$(stat -c %s "$STAGE/files.tar")
FILES_COUNT=$(tar -tf "$STAGE/files.tar" | grep -cv '/$' || true)

# ---------- 5. manifest ----------
STEP="staging_failed"
DB_SHA=$(sha256sum "$STAGE/db.dump" | cut -d' ' -f1)
FILES_SHA=$(sha256sum "$STAGE/files.tar" | cut -d' ' -f1)
cat > "$STAGE/manifest.json" <<JSON
{"schema":1,"run_key":"$RUN_KEY","reason":"$REASON","created_at":"$STARTED_AT","git_commit":$(json_str "$GIT_COMMIT"),"schema_revision":$(json_str "$SCHEMA_REV"),
 "storage_provider":"$PROVIDER","artifacts":[
  {"name":"db.dump","format":"pg_dump custom","bytes":$DB_BYTES,"sha256":"$DB_SHA"},
  {"name":"files.tar","format":"tar","bytes":$FILES_BYTES,"files":$FILES_COUNT,"sha256":"$FILES_SHA"}]}
JSON
rm -f "$STAGE/db.list"
MANIFEST_SHA=$(sha256sum "$STAGE/manifest.json" | cut -d' ' -f1)
(cd "$STAGE" && sha256sum db.dump files.tar manifest.json > SHA256SUMS)

# ---------- 6. отправка в репозиторий restic ----------
STEP="restic_backup_failed"
restic backup "$STAGE" --host "$HOST_TAG" --tag gsho --tag "reason=$REASON" --tag "run=$RUN_KEY" \
  ${GIT_COMMIT:+--tag "commit=${GIT_COMMIT:0:12}"} --json --quiet > "$STAGING_ROOT/.restic-backup.json"
SNAPSHOT=$(grep '"message_type":"summary"' "$STAGING_ROOT/.restic-backup.json" | tail -1 | sed -n 's/.*"snapshot_id":"\([0-9a-f]*\)".*/\1/p')
rm -f "$STAGING_ROOT/.restic-backup.json"
STEP="snapshot_missing"
[[ "$SNAPSHOT" =~ ^[0-9a-f]{8,64}$ ]] || false
SNAPSHOT="${SNAPSHOT:0:16}"
restic snapshots --json --tag "run=$RUN_KEY" | grep -q "\"${SNAPSHOT:0:8}" || false
log "снимок ${SNAPSHOT:0:8} создан (${REASON})"

# копия уже в Яндексе — staging больше не нужен
rm -rf --one-file-system "$STAGE"; STAGE=""

# ---------- 7. проверка целостности ----------
STEP="check_failed"
if [ "$(date +%u)" = "$DEEP_CHECK_WEEKDAY" ]; then
  restic check --read-data-subset=5% > /dev/null
else
  restic check > /dev/null
fi
CHECK_STATUS="ok"; CHECKED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------- 8. хранение: 90 ежедневных и 12 ежемесячных ----------
# Ошибка prune не отменяет созданный снимок, но не скрывается: retention_status=failed и уведомление в CRM (03 §11).
trap - ERR
if restic forget --host "$HOST_TAG" --tag gsho --group-by host --keep-daily "$KEEP_DAILY" --keep-monthly "$KEEP_MONTHLY" --prune > /dev/null; then
  RETENTION_STATUS="ok"
else
  RETENTION_STATUS="failed"; ERROR_CODE="retention_failed"; ERROR_SUMMARY="forget --prune завершился с ошибкой"
  log "ПРЕДУПРЕЖДЕНИЕ: retention_failed — снимок сохранён, старые не очищены"
fi

# ---------- 9. статус ----------
STATUS="success"; FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! write_status; then
  log "ПРЕДУПРЕЖДЕНИЕ: status_write_failed — копия ${SNAPSHOT:0:8} создана, но статус не записан"
fi
log "готово: снимок ${SNAPSHOT:0:8}, БД ${DB_BYTES} Б, файлы ${FILES_BYTES} Б (${FILES_COUNT} шт.), check ${CHECK_STATUS}, retention ${RETENTION_STATUS}"
exit 0
