#!/bin/bash
# macOS: двойной клик в Finder — поднимает стенд локально (Docker Desktop), лог в scripts/start-local.log
cd "$(dirname "$0")/.."
exec > >(tee -a scripts/start-local.log) 2>&1
echo "=== $(date) запуск стенда ==="
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin"
if ! docker info >/dev/null 2>&1; then echo "Запускаю Docker Desktop…"; open -a Docker; for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done; fi
docker info >/dev/null 2>&1 || { echo "ОШИБКА: Docker недоступен"; exit 1; }
[ -f backend/.env ] || cp backend/.env.example backend/.env
PORT=80; (lsof -iTCP:80 -sTCP:LISTEN >/dev/null 2>&1) && PORT=8080
export FRONTEND_PORT=$PORT
docker compose up --build -d || { echo "ОШИБКА: сборка не удалась"; exit 1; }
for i in $(seq 1 40); do curl -sf http://localhost:8000/api/health >/dev/null && break; sleep 3; done
curl -s http://localhost:8000/api/health; echo
docker compose exec -T backend python -m scripts.seed
docker compose exec -T db psql -U gsho -d gsho -q < backend/migrations/001_audit_immutable.sql 2>/dev/null || true
docker compose ps --format "table {{.Service}}\t{{.Status}}"
echo "=== ГОТОВО: http://localhost:$PORT  (API: http://localhost:8000/api/docs) ==="
open "http://localhost:$PORT"
