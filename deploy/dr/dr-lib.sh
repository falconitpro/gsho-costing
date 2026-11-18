#!/usr/bin/env bash
# Общие функции резервного сервера (D-082, deploy/dr/README.md). Подключается через `source`, сам не запускается.
#
# dr_restore_latest <env-файл restic> [снимок]  — восстанавливает снимок (по умолчанию последний с тегом gsho)
#   в локальный стенд /opt/gsho-costing: код того же commit, база gsho заменяется дампом, файлы — архивом.
#   Переменные: GSHO_DIR (/opt/gsho-costing), DR_WORK (/var/lib/gsho-dr), DR_COMPOSE_EXTRA (доп. файл compose, напр. drill).
# Секреты (пароль restic, ключи) читаются restic из окружения; функции их не печатают.
# shellcheck shell=bash

GSHO_DIR="${GSHO_DIR:-/opt/gsho-costing}"
DR_WORK="${DR_WORK:-/var/lib/gsho-dr}"

dr_log() { printf '%s gsho-dr %s\n' "$(date '+%F %T')" "$*" >&2; }
dr_die() { dr_log "ОСТАНОВЛЕНО: $*"; exit 1; }

dr_compose() {
  local files=(-f docker-compose.yml -f docker-compose.prod.yml)
  [ -n "${DR_COMPOSE_EXTRA:-}" ] && files+=(-f "$DR_COMPOSE_EXTRA")
  (cd "$GSHO_DIR" && docker compose "${files[@]}" --profile scanner "$@")
}

# Загружает env-файл restic в текущую оболочку (только переменные restic/S3)
dr_load_restic_env() {
  local f="$1"
  [ -r "$f" ] || dr_die "нет файла $f (настройки доступа к копиям)"
  set -a
  # shellcheck disable=SC1090
  . "$f"
  set +a
  [ -n "${RESTIC_REPOSITORY:-}" ] || dr_die "в $f не задан RESTIC_REPOSITORY"
}

# Печатает ID последнего снимка с тегом gsho (restic --no-lock: ключ восстановления только читает)
dr_latest_snapshot() {
  restic snapshots --no-lock --tag gsho --latest 1 --json | python3 -c '
import json, sys
s = sorted(json.load(sys.stdin), key=lambda x: x["time"])
print(s[-1]["short_id"] if s else "")'
}

# Ждёт ответа backend (/api/health изнутри сети compose) до 5 минут; затем проверяет HTTPS через Caddy (только предупреждение)
dr_wait_health() {
  local domain="$1" _
  for _ in $(seq 1 60); do
    if dr_compose exec -T backend python -c "import urllib.request as u; u.urlopen('http://localhost:8000/api/health', timeout=5)" >/dev/null 2>&1; then
      curl -fsk -m 10 --resolve "$domain:443:127.0.0.1" "https://$domain/api/health" >/dev/null 2>&1 \
        || dr_log "ВНИМАНИЕ: backend отвечает, но https://$domain через Caddy — нет (сертификат? deploy/dr/README.md §2)"
      return 0
    fi
    sleep 5
  done
  return 1
}

dr_restore_latest() {
  local envf="$1" snap="${2:-}" root dir manifest commit created
  dr_load_restic_env "$envf"
  [ -n "$snap" ] || snap="$(dr_latest_snapshot)"
  [[ "$snap" =~ ^[0-9a-f]{8,64}$ ]] || dr_die "снимок не найден в $RESTIC_REPOSITORY"

  install -d -m 0700 "$DR_WORK"
  root="$DR_WORK/restore-$(date +%Y%m%d-%H%M%S)"
  dr_log "восстанавливаю снимок ${snap:0:8} в $root"
  restic restore --no-lock "$snap" --target "$root" --quiet
  manifest="$(find "$root" -type f -name manifest.json -path '*gsho-backup*' | head -1)"
  [ -n "$manifest" ] || dr_die "в снимке нет manifest.json"
  dir="$(dirname "$manifest")"
  (cd "$dir" && sha256sum -c --quiet SHA256SUMS) || dr_die "контрольные суммы снимка не совпали — данные не запускаются"
  commit="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("git_commit") or "")' "$manifest")"
  created="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("created_at") or "")' "$manifest")"
  dr_log "снимок создан $created, commit ${commit:0:12}"

  # код того же commit, что и копия: схема базы совпадает с дампом
  if [[ "$commit" =~ ^[0-9a-f]{40}$ ]] && [ "$(git -C "$GSHO_DIR" rev-parse HEAD)" != "$commit" ]; then
    dr_log "переключаю код на ${commit:0:12} и пересобираю (5–10 минут)"
    timeout 300 git -C "$GSHO_DIR" fetch -q origin || dr_log "git fetch не удался — пробую с тем, что есть"
    git -C "$GSHO_DIR" -c advice.detachedHead=false checkout -q "$commit" || dr_die "commit ${commit:0:12} недоступен — обновите код вручную"
    dr_compose build -q backend frontend clamav
  fi

  dr_log "заменяю базу и файлы"
  dr_compose up -d db
  for _ in $(seq 1 30); do dr_compose exec -T db pg_isready -U gsho >/dev/null 2>&1 && break; sleep 2; done
  dr_compose stop caddy frontend backend >/dev/null 2>&1 || true
  dr_compose exec -T db psql -U gsho -d postgres -v ON_ERROR_STOP=1 -q \
    -c 'DROP DATABASE IF EXISTS gsho WITH (FORCE)' -c 'CREATE DATABASE gsho OWNER gsho'
  dr_compose exec -T db pg_restore -U gsho -d gsho --no-owner --exit-on-error < "$dir/db.dump"
  # shellcheck disable=SC2016  # переменная раскрывается внутри контейнера
  dr_compose run --rm --no-deps -T backend sh -c \
    'r="${STORAGE_LOCAL_ROOT:-/app/data/storage}"; mkdir -p "$r" && find "$r" -mindepth 1 -delete && tar -C "$r" -xf -' < "$dir/files.tar"

  dr_log "запускаю стенд"
  dr_compose up -d
  rm -rf --one-file-system "$root"   # распакованная копия больше не нужна: данные уже в базе и томе файлов
  DR_SNAPSHOT="$snap"; DR_SNAPSHOT_CREATED="$created"; DR_COMMIT="$commit"
  export DR_SNAPSHOT DR_SNAPSHOT_CREATED DR_COMMIT
}

# Флаг «работаем на резерве» в Object Storage: рабочий сервер читает его (deploy/dr/flag-guard.sh) и переадресует на crm2
dr_flag_put() {
  local body="$1"
  [ -n "${DR_FLAG_BUCKET_URL:-}" ] && [ -n "${DR_FLAG_ACCESS_KEY_ID:-}" ] || dr_die "в /etc/gsho/dr.env не заданы DR_FLAG_BUCKET_URL / ключ флага"
  printf '%s' "$body" | curl -fsS -m 30 -X PUT --aws-sigv4 "aws:amz:ru-central1:s3" \
    --user "$DR_FLAG_ACCESS_KEY_ID:$DR_FLAG_SECRET_ACCESS_KEY" -H 'Content-Type: application/json' \
    -H 'x-amz-acl: public-read' --data-binary @- "$DR_FLAG_BUCKET_URL/failover.json" >/dev/null
}

dr_flag_delete() {
  curl -fsS -m 30 -X DELETE --aws-sigv4 "aws:amz:ru-central1:s3" \
    --user "$DR_FLAG_ACCESS_KEY_ID:$DR_FLAG_SECRET_ACCESS_KEY" "$DR_FLAG_BUCKET_URL/failover.json" >/dev/null
}
