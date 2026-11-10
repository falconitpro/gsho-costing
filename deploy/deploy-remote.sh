#!/usr/bin/env bash
# Вызывается GitHub Actions по SSH (или autodeploy.sh) после зелёных тестов: обновить код и пересобрать изменившиеся сервисы.
#
# Копия перед обновлением (docs/operations/03 §9): при BACKUP_REQUIRED=true в /etc/gsho/deploy.env деплой сначала делает
# зашифрованную копию в Yandex Object Storage и останавливается ДО git reset/сборки, если копия не удалась.
# До прохождения restore gate флаг не задан — деплой работает как раньше и не блокируется отсутствием настройки.
# Миграции здесь не запускаются (D-019): их применяет владелец вручную после копии и репетиции.
set -euo pipefail
cd "${GSHO_DIR:-/opt/gsho-costing}"

DEPLOY_ENV="${GSHO_DEPLOY_ENV:-/etc/gsho/deploy.env}"
BACKUP_REQUIRED="${BACKUP_REQUIRED:-}"
if [ -z "$BACKUP_REQUIRED" ] && [ -r "$DEPLOY_ENV" ]; then
  BACKUP_REQUIRED="$(sed -n 's/^BACKUP_REQUIRED=//p' "$DEPLOY_ENV" | tail -1)"
fi
if [ "${BACKUP_REQUIRED:-false}" = "true" ]; then
  echo "pre-deploy: копия в Yandex Object Storage…"
  BIN="${GSHO_BACKUP_BIN:-/usr/local/sbin/gsho-backup.sh}"
  if [ "$(id -u)" = 0 ]; then BK=("$BIN" --reason=pre_deploy); else BK=(sudo -n "$BIN" --reason=pre_deploy); fi
  rc=0; "${BK[@]}" || rc=$?
  if [ "$rc" = 75 ]; then   # идёт ежедневная копия — дождаться её и повторить один раз
    sleep "${GSHO_BACKUP_RETRY_SLEEP:-60}"; rc=0; "${BK[@]}" || rc=$?
  fi
  if [ "$rc" != 0 ]; then
    echo "pre-deploy: копия НЕ создана (код $rc) — обновление остановлено, стенд не изменён" >&2
    exit 1
  fi
fi

timeout 120 git fetch -q origin main && git reset -q --hard origin/main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build backend frontend caddy
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || true   # Caddyfile смонтирован файлом — изменения подхватываются перезагрузкой конфига
for _ in $(seq 1 30); do docker compose exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" >/dev/null 2>&1 && break; sleep 3; done
# демо-данные — только на стенде с APP_ENV=dev; в рабочем режиме (APP_ENV=prod) seed не запускается (03 §9)
if docker compose exec -T backend python -c "import sys; from app.core.config import get_settings; sys.exit(0 if get_settings().is_dev else 1)" >/dev/null 2>&1; then
  docker compose exec -T backend python -m scripts.seed >/dev/null || true
fi
docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD) at $(date)"
