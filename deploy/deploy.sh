#!/usr/bin/env bash
# Развёртывание тестового стенда на сервере с Docker (Ubuntu/Debian). Запуск на сервере из корня репозитория:
#   DOMAIN=test.example.ru ACME_EMAIL=you@example.ru POSTGRES_PASSWORD=strong ./deploy/deploy.sh
set -euo pipefail
: "${DOMAIN:?DOMAIN не задан}"
: "${POSTGRES_PASSWORD:=gsho}"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  sed -i "s#^CORS_ORIGINS=.*#CORS_ORIGINS=https://${DOMAIN}#; s#^OIDC_REDIRECT_URI=.*#OIDC_REDIRECT_URI=https://${DOMAIN}/api/auth/callback#; s#^SECRET_KEY=.*#SECRET_KEY=$(openssl rand -hex 32)#" backend/.env
fi
export DOMAIN POSTGRES_PASSWORD ACME_EMAIL="${ACME_EMAIL:-}"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
sleep 10
docker compose exec -T backend python -m scripts.seed || true
docker compose exec -T db psql -U gsho -d gsho < backend/migrations/001_audit_immutable.sql || true
echo "Готово: https://${DOMAIN}  (API: https://${DOMAIN}/api/health, Swagger: https://${DOMAIN}/api/docs)"
