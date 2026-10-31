#!/usr/bin/env bash
# Первичная настройка ВМ Yandex Cloud (Ubuntu 22.04/24.04) под тестовый стенд. Запускать от root или через sudo ОДИН раз:
#   curl -fsSL https://raw.githubusercontent.com/falconitpro/gsho-costing/main/deploy/yandex-cloud-setup.sh | sudo bash -s -- \
#       --domain test.berghaus.ru --email admin@berghaus.ru --password 'ПарольСтенда' --deploy-key "ssh-ed25519 AAAA... deploy@github"
# Что делает: ставит Docker, создаёт пользователя deploy с ключом для GitHub Actions, клонирует репозиторий в /opt/gsho-costing,
# создаёт .env с паролями, открывает 80/443 в ufw, поднимает стенд, включает ежедневные копии (контейнер backup).
set -euo pipefail
DOMAIN=""; EMAIL=""; PASSWORD=""; DEPLOY_KEY=""; REPO="https://github.com/falconitpro/gsho-costing.git"
while [ $# -gt 0 ]; do case "$1" in
  --domain) DOMAIN="$2"; shift 2;; --email) EMAIL="$2"; shift 2;; --password) PASSWORD="$2"; shift 2;;
  --deploy-key) DEPLOY_KEY="$2"; shift 2;; --repo) REPO="$2"; shift 2;; *) echo "неизвестный параметр $1"; exit 1;; esac; done
[ -n "$DOMAIN" ] && [ -n "$PASSWORD" ] || { echo "нужны --domain и --password"; exit 1; }

echo "== Docker"
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
systemctl enable --now docker

echo "== пользователь deploy"
id deploy >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker deploy
if [ -n "$DEPLOY_KEY" ]; then
  install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
  grep -qF "$DEPLOY_KEY" /home/deploy/.ssh/authorized_keys 2>/dev/null || echo "$DEPLOY_KEY" >> /home/deploy/.ssh/authorized_keys
  chown deploy:deploy /home/deploy/.ssh/authorized_keys; chmod 600 /home/deploy/.ssh/authorized_keys
fi

echo "== репозиторий"
install -d -o deploy -g deploy /opt/gsho-costing
if [ ! -d /opt/gsho-costing/.git ]; then sudo -u deploy git clone "$REPO" /opt/gsho-costing; fi
cd /opt/gsho-costing

echo "== секреты"
HASH=$(docker run --rm caddy:2 caddy hash-password --plaintext "$PASSWORD" | sed 's/\$/$$/g')   # $ → $$ для docker compose
PGPASS=$(openssl rand -hex 16)
cat > .env <<ENV
DOMAIN=$DOMAIN
ACME_EMAIL=$EMAIL
POSTGRES_PASSWORD=$PGPASS
BASIC_AUTH_USER=berghaus
BASIC_AUTH_HASH=$HASH
ENV
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  sed -i "s#^CORS_ORIGINS=.*#CORS_ORIGINS=https://$DOMAIN#; s#^OIDC_REDIRECT_URI=.*#OIDC_REDIRECT_URI=https://$DOMAIN/api/auth/callback#; s#^SECRET_KEY=.*#SECRET_KEY=$(openssl rand -hex 32)#" backend/.env
fi
chown -R deploy:deploy /opt/gsho-costing; chmod 600 .env backend/.env

echo "== файрвол"
if command -v ufw >/dev/null; then ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null; fi

echo "== запуск"
sudo -u deploy bash -c "cd /opt/gsho-costing && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
sleep 15
sudo -u deploy bash -c "cd /opt/gsho-costing && docker compose exec -T backend python -m scripts.seed && docker compose exec -T db psql -U gsho -d gsho -q < backend/migrations/001_audit_immutable.sql" || true
echo
echo "ГОТОВО: https://$DOMAIN  (логин berghaus / ваш пароль), API: https://$DOMAIN/api/docs"
echo "Пароль PostgreSQL сохранён в /opt/gsho-costing/.env"
