#!/usr/bin/env bash
# Перенос данных со стенда на НОВЫЙ рабочий сервер из копии в Yandex Object Storage (deploy/production/transfer.md).
#   sudo bash deploy/production/transfer-from-stand.sh --snapshot=<ID снимка стенда>
# Нужно: /etc/gsho/restore.env (хранилище СТЕНДА, пароль копий и ключ только для чтения), restic, запущенная пустая база,
# CRM НЕ запущена. Шаги: снимок → контрольные суммы → база → файлы → схема и SQL → сброс паролей, кодов и служебных данных → курсы ЦБ.
# Сделки, справочники, сотрудники и права переносятся как есть. В базу, где уже есть сотрудники, скрипт ничего не пишет.
set -Eeuo pipefail
umask 077
cd "${GSHO_DIR:-/opt/gsho-costing}"
SNAP=""
for a in "$@"; do case "$a" in --snapshot=*) SNAP="${a#--snapshot=}" ;; *) echo "неизвестный аргумент: $a" >&2; exit 2 ;; esac; done
[[ "$SNAP" =~ ^[0-9a-f]{8,64}$ ]] || { echo "укажите --snapshot=<ID> (снимок выбирают осознанно, не latest)" >&2; exit 2; }
ENVF="${GSHO_RESTORE_ENV:-/etc/gsho/restore.env}"
[ -r "$ENVF" ] || { echo "нет $ENVF — см. deploy/production/transfer.md, шаг 2" >&2; exit 2; }
command -v restic >/dev/null || { echo "нет restic: apt-get install -y restic" >&2; exit 2; }
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
PSQL=("${DC[@]}" exec -T db psql -U gsho -d gsho -v ON_ERROR_STOP=1 -q)

if [ -n "$("${DC[@]}" ps -q backend 2>/dev/null)" ]; then
  echo "СТОП: CRM уже запущена. Перенос выполняется только до первого запуска." >&2; exit 3
fi
"${DC[@]}" up -d db
for _ in $(seq 1 30); do "${DC[@]}" exec -T db pg_isready -U gsho >/dev/null 2>&1 && break; sleep 2; done
if [ "$("${PSQL[@]}" -Atc "select count(*) from information_schema.tables where table_schema = 'public'")" != "0" ]; then
  echo "СТОП: база не пустая. Перенос выполняется только в пустую базу; ничего не изменено." >&2; exit 3
fi

T="${GSHO_TRANSFER_DIR:-/srv/gsho-transfer}/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$T"
set -a
# shellcheck disable=SC1090
. "$ENVF"
set +a
echo "== снимок $SNAP"
restic snapshots --compact "$SNAP"
restic restore "$SNAP" --target "$T" --quiet
DIR="$(find "$T" -type f -name manifest.json -path '*gsho-backup*' -printf '%h\n' | head -1)"
[ -n "$DIR" ] || { echo "в снимке нет manifest.json" >&2; exit 1; }
echo "== контрольные суммы"
(cd "$DIR" && sha256sum -c SHA256SUMS)
echo "== база"
"${DC[@]}" exec -T db pg_restore -U gsho -d gsho --no-owner --exit-on-error < "$DIR/db.dump"
echo "== файлы"
# shellcheck disable=SC2016   # переменная раскрывается внутри контейнера
"${DC[@]}" run --rm -T --no-deps backend sh -c 'mkdir -p "${STORAGE_LOCAL_ROOT:-/app/data/storage}" && cd "${STORAGE_LOCAL_ROOT:-/app/data/storage}" && tar -xf - && find . -type f | wc -l' < "$DIR/files.tar"
echo "== схема и SQL"
bash deploy/production/init-db.sh --existing
echo "== сброс паролей, кодов и служебных данных стенда"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.prod_init adopt --yes
echo "== курсы ЦБ"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.prod_init fx || echo "курсы не загружены — повторите позже: python -m scripts.prod_init fx"
echo "== что в базе"
"${DC[@]}" run --rm -T --no-deps backend python -m scripts.db_inventory

cat <<TXT

ПЕРЕНОС ЗАВЕРШЁН. CRM ещё не запущена.
1. Сверьте список выше со стендом: сотрудники, номера сделок и заявок.
2. Удалите ключ и распакованную копию стенда с этого сервера:
     sudo rm -f $ENVF && sudo rm -rf $T
3. Дальше — deploy/production/install.md, шаг 5 (первый пользователь и запуск).
TXT
