#!/bin/bash
# macOS: обновить код из GitHub, пересобрать и перезапустить backend и frontend
cd "$(dirname "$0")/.."
export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin"
{
  echo "=== $(date) rebuild ==="
  rm -f .git/index.lock
  git pull --ff-only origin main || echo "git pull не удался"
  git log --oneline -1
  docker compose up --build -d frontend backend
  for i in $(seq 1 40); do curl -sf http://localhost:8000/api/health >/dev/null && break; sleep 3; done
  curl -s http://localhost:8000/api/health; echo
  docker compose exec -T backend python -m scripts.seed
  docker compose ps --format "table {{.Service}}\t{{.Status}}"
  echo "=== rebuild done ==="
} 2>&1 | tee -a scripts/start-local.log
