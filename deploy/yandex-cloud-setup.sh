#!/usr/bin/env bash
# Первичная настройка ВМ Yandex Cloud (Ubuntu 22.04/24.04) под тестовый стенд. Запускать от root или через sudo ОДИН раз:
#   curl -fsSL https://raw.githubusercontent.com/falconitpro/gsho-costing/main/deploy/yandex-cloud-setup.sh | sudo bash -s -- \
#       --domain test.berghaus.ru --email admin@berghaus.ru --password 'ПарольСтенда' --deploy-key "ssh-ed25519 AAAA... deploy@github"
# Что делает: ставит Docker, создаёт пользователя deploy с ключом для GitHub Actions, клонирует репозиторий в /opt/gsho-costing,
# создаёт .env с паролями, открывает 80/443 в ufw, поднимает стенд, включает ежедневные копии (контейнер backup).
set -euo pipefail
DOMAIN=""; EMAIL=""; PASSWORD=""; DEPLOY_KEY=""; AUTH_USER="berghaus"; EXTRA_HOSTS=""; REPO="https://github.com/falconitpro/gsho-costing.git"
while [ $# -gt 0 ]; do case "$1" in
  --domain) DOMAIN="$2"; shift 2;; --email) EMAIL="$2"; shift 2;; --password) PASSWORD="$2"; shift 2;;
  --deploy-key) DEPLOY_KEY="$2"; shift 2;; --user) AUTH_USER="$2"; shift 2;; --also) EXTRA_HOSTS="$2"; shift 2;; --repo) REPO="$2"; shift 2;; *) echo "неизвестный параметр $1"; exit 1;; esac; done
if [ -z "$DOMAIN" ] || [ "$DOMAIN" = "auto" ]; then   # без своего домена: <ip>.sslip.io (HTTPS тоже работает)
  IP=$(curl -fsS -m 5 -H Metadata-Flavor:Google http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || curl -fsS -m 5 https://ifconfig.me)
  DOMAIN="$(echo "$IP" | tr . -).sslip.io"
fi
[ -n "$PASSWORD" ] || { echo "нужен --password"; exit 1; }

echo "== Docker"
# download.docker.com и Docker Hub недоступны из части РФ-сетей: ставим docker из репозитория Ubuntu и включаем зеркала реестра
if ! command -v docker >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 git openssl >/dev/null \
    || curl -fsSL https://get.docker.com | sh
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx >/dev/null 2>&1 || true
fi
if [ ! -f /etc/docker/daemon.json ]; then
  install -d /etc/docker
  cat > /etc/docker/daemon.json <<'JSON'
{ "registry-mirrors": ["https://mirror.gcr.io", "https://dockerhub.timeweb.cloud"] }
JSON
  systemctl restart docker || true
fi
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
DOMAIN=$DOMAIN${EXTRA_HOSTS:+, $EXTRA_HOSTS}
FALLBACK_HOST=$(curl -fsS -m 5 -H Metadata-Flavor:Google http://169.254.169.254/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || echo localhost)
ACME_EMAIL=$EMAIL
POSTGRES_PASSWORD=$PGPASS
BASIC_AUTH_USER=$AUTH_USER
BASIC_AUTH_HASH=$HASH
ENV
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  sed -i "s#^CORS_ORIGINS=.*#CORS_ORIGINS=https://$DOMAIN#; s#^OIDC_REDIRECT_URI=.*#OIDC_REDIRECT_URI=https://$DOMAIN/api/auth/callback#; s#^SECRET_KEY=.*#SECRET_KEY=$(openssl rand -hex 32)#" backend/.env
fi
chown -R deploy:deploy /opt/gsho-costing; chmod 600 .env backend/.env

echo "== файрвол"
if command -v ufw >/dev/null; then ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null; fi

echo "== MSS clamping"
# В Yandex Cloud полноразмерные пакеты (MTU 1500) по пути теряются: TLS-рукопожатие с цепочкой сертификатов «висит».
cat > /etc/systemd/system/gsho-mss.service <<UNIT
[Unit]
Description=Clamp TCP MSS on eth0 (path MTU in Yandex Cloud)
After=network-online.target docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -t mangle -C POSTROUTING -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || iptables -t mangle -A POSTROUTING -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360"
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now gsho-mss.service

echo "== автодеплой (pull, без секретов в GitHub)"
cat > /etc/systemd/system/gsho-autodeploy.service <<UNIT
[Unit]
Description=GSHO costing pull autodeploy
[Service]
Type=oneshot
User=deploy
ExecStart=/bin/bash /opt/gsho-costing/deploy/autodeploy.sh
TimeoutStartSec=40min
UNIT
cat > /etc/systemd/system/gsho-autodeploy.timer <<UNIT
[Unit]
Description=GSHO costing autodeploy every 2 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable --now gsho-autodeploy.timer
git config --system --add safe.directory /opt/gsho-costing
cat > /etc/systemd/system/gsho-cert.service <<UNIT
[Unit]
Description=GSHO certificate from Yandex Certificate Manager
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/gsho-costing/deploy/fetch-cert.sh
UNIT
cat > /etc/systemd/system/gsho-cert.timer <<UNIT
[Unit]
Description=GSHO certificate refresh daily
[Timer]
OnBootSec=3min
OnUnitActiveSec=12h
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable --now gsho-cert.timer

echo "== запуск"
sudo -u deploy bash -c "cd /opt/gsho-costing && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
sleep 15
sudo -u deploy bash -c "cd /opt/gsho-costing && docker compose exec -T backend python -m scripts.seed && docker compose exec -T db psql -U gsho -d gsho -q < backend/migrations/001_audit_immutable.sql" || true
echo
echo "ГОТОВО: https://$DOMAIN  (логин $AUTH_USER / ваш пароль), API: https://$DOMAIN/api/docs"
echo "Пароль PostgreSQL сохранён в /opt/gsho-costing/.env"
