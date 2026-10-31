#!/usr/bin/env bash
# Вызывается GitHub Actions по SSH после зелёных тестов: обновить код и пересобрать изменившиеся сервисы.
set -euo pipefail
cd /opt/gsho-costing
git fetch -q origin main && git reset -q --hard origin/main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build backend frontend caddy
for i in $(seq 1 30); do docker compose exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" >/dev/null 2>&1 && break; sleep 3; done
docker compose exec -T backend python -m scripts.seed >/dev/null || true
docker image prune -f >/dev/null
echo "deployed $(git rev-parse --short HEAD) at $(date)"
