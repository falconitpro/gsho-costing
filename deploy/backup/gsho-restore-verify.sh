#!/usr/bin/env bash
# Восстановление снимка restic в отдельный каталог и проверка артефактов — ТОЛЬКО на изолированной recovery-ВМ (docs/operations/13 §4).
#
#   gsho-restore-verify.sh --snapshot=<ID> --target=<ПУСТОЙ_КАТАЛОГ> [--pg-restore-to=<имя_новой_БД>]
#
# Окружение: RESTIC_REPOSITORY, RESTIC_PASSWORD (или RESTIC_PASSWORD_FILE) и ключ restore SA (storage.viewer) — из защищённого env recovery-ВМ.
# Шаги: restic restore → SHA256SUMS → manifest → pg_restore --list → (опционально) pg_restore в НОВУЮ пустую БД по PG* → распаковка файлов.
# Печатает отчёт без секретов и без содержимого данных: только ID, размеры, хэши, число объектов.
# Отказывается работать, если в окружении есть признаки production-стенда (каталог /opt/gsho-costing с запущенным backend).
set -Eeuo pipefail
umask 077

SNAP=""; TARGET=""; PGDB=""
for a in "$@"; do
  case "$a" in
    --snapshot=*) SNAP="${a#--snapshot=}" ;;
    --target=*) TARGET="${a#--target=}" ;;
    --pg-restore-to=*) PGDB="${a#--pg-restore-to=}" ;;
    *) echo "неизвестный аргумент: $a" >&2; exit 2 ;;
  esac
done
[[ "$SNAP" =~ ^[0-9a-f]{8,64}$ ]] || { echo "укажите --snapshot=<ID> (не latest: снимок выбирают осознанно)" >&2; exit 2; }
[ -n "$TARGET" ] || { echo "укажите --target=<каталог>" >&2; exit 2; }
[ -n "${RESTIC_REPOSITORY:-}" ] || { echo "RESTIC_REPOSITORY не задан" >&2; exit 2; }

if [ "${GSHO_RECOVERY_ALLOW_PROD_HOST:-}" != "yes" ] && command -v docker >/dev/null 2>&1 && \
   docker ps --format '{{.Names}}' 2>/dev/null | grep -q -E '(^|[-_])backend([-_]|$)' && [ -d /opt/gsho-costing ]; then
  echo "СТОП: похоже на рабочий стенд (запущен backend и есть /opt/gsho-costing). Восстановление — только на отдельной recovery-ВМ." >&2
  exit 3
fi
if [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then echo "каталог $TARGET не пустой" >&2; exit 2; fi
mkdir -p "$TARGET"

t0=$(date +%s)
echo "== Снимок"
restic snapshots --compact "$SNAP"
restic restore "$SNAP" --target "$TARGET" --quiet
DIR=$(find "$TARGET" -type f -name manifest.json -path '*gsho-backup*' -printf '%h\n' | head -1)
[ -n "$DIR" ] || { echo "в снимке нет manifest.json" >&2; exit 1; }

echo "== Контрольные суммы"
(cd "$DIR" && sha256sum -c SHA256SUMS)

echo "== Manifest"
python3 - "$DIR/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(f"  reason {m.get('reason')}, создан {m.get('created_at')}, commit {str(m.get('git_commit') or '—')[:12]}, схема {m.get('schema_revision')}, хранилище {m.get('storage_provider')}")
for a in m.get("artifacts", []):
    extra = f", файлов {a['files']}" if "files" in a else ""
    print(f"  {a['name']}: {a['bytes']} Б{extra}, sha256 {a['sha256'][:16]}…")
PY

echo "== Дамп PostgreSQL"
pg_restore --list "$DIR/db.dump" > "$DIR/.db.list"
echo "  объектов в оглавлении: $(grep -c -v '^;' "$DIR/.db.list")"
echo "  таблиц с данными: $(grep -c 'TABLE DATA' "$DIR/.db.list" || true)"
rm -f "$DIR/.db.list"

if [ -n "$PGDB" ]; then
  [[ "$PGDB" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "имя БД: только [a-z0-9_]" >&2; exit 2; }
  echo "== Восстановление в новую БД $PGDB (${PGHOST:-local socket})"
  if psql -d postgres -Atc "select 1 from pg_database where datname='$PGDB'" | grep -q 1; then
    echo "БД $PGDB уже существует — восстанавливаем только в новую пустую" >&2; exit 2
  fi
  createdb "$PGDB"
  pg_restore --no-owner --exit-on-error -d "$PGDB" "$DIR/db.dump"
  echo "  таблиц: $(psql -d "$PGDB" -Atc "select count(*) from information_schema.tables where table_schema='public'")"
  echo "  пользователей: $(psql -d "$PGDB" -Atc 'select count(*) from users' 2>/dev/null || echo '—')"
  echo "  сделок: $(psql -d "$PGDB" -Atc 'select count(*) from deals' 2>/dev/null || echo '—')"
fi

echo "== Вложения"
mkdir -p "$DIR/storage"
tar -C "$DIR/storage" -xf "$DIR/files.tar"
echo "  файлов распаковано: $(find "$DIR/storage" -type f | wc -l)"

echo "== Итог: снимок ${SNAP:0:8} восстановлен и проверен за $(( $(date +%s) - t0 )) с. Каталог: $DIR"
