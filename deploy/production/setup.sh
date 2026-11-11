#!/usr/bin/env bash
# Первичная настройка НОВОГО рабочего сервера (Ubuntu 22.04/24.04). Запускать от root один раз:
#   bash setup.sh --domain crm.example.ru --email admin@example.ru
# Что делает: файл подкачки, Docker, пользователь deploy, код в /opt/gsho-costing, настройки рабочего режима со СВОИМИ секретами
# (создаются на сервере, на экран не выводятся), файрвол, сборка, запуск базы и антивируса.
# Чего НЕ делает: не запускает CRM (сначала deploy/production/init-db.sh), не добавляет демо-данные, не включает
# автоматическое обновление и старые локальные копии. Дальше — deploy/production/install.md.
set -euo pipefail
DOMAIN=""; EMAIL=""; REPO="https://github.com/falconitpro/gsho-costing.git"; SWAP_GB=2; MSS=""
while [ $# -gt 0 ]; do case "$1" in
  --domain) DOMAIN="$2"; shift 2;; --email) EMAIL="$2"; shift 2;; --repo) REPO="$2"; shift 2;;
  --swap-gb) SWAP_GB="$2"; shift 2;; --yandex-mss) MSS=1; shift;; *) echo "неизвестный параметр $1"; exit 1;; esac; done
[ -n "$DOMAIN" ] || { echo "нужен --domain (адрес рабочей системы, DNS уже должен указывать на этот сервер)"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "запускать от root"; exit 1; }
DIR=/opt/gsho-costing
if [ -f "$DIR/backend/.env" ]; then echo "$DIR/backend/.env уже есть — сервер настроен раньше. Остановлено, ничего не изменено."; exit 1; fi

echo "== файл подкачки"
if [ "$SWAP_GB" != 0 ] && ! swapon --show | grep -q .; then
  fallocate -l "${SWAP_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Docker"
if ! command -v docker >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 git openssl >/dev/null || curl -fsSL https://get.docker.com | sh
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx >/dev/null 2>&1 || true
fi
if [ ! -f /etc/docker/daemon.json ]; then
  install -d /etc/docker
  echo '{ "registry-mirrors": ["https://mirror.gcr.io", "https://dockerhub.timeweb.cloud"] }' > /etc/docker/daemon.json
  systemctl restart docker || true
fi
systemctl enable --now docker

echo "== пользователь deploy и код"
id deploy >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker deploy
install -d -o deploy -g deploy "$DIR"
[ -d "$DIR/.git" ] || sudo -u deploy git clone -q "$REPO" "$DIR"
git config --system --add safe.directory "$DIR"
cd "$DIR"

echo "== настройки рабочего режима"
IP="$(curl -fsS -m 5 https://ifconfig.me 2>/dev/null || echo localhost)"
umask 077
cat > .env <<ENV
DOMAIN=$DOMAIN
FALLBACK_HOST=$IP
ACME_EMAIL=$EMAIL
POSTGRES_PASSWORD=$(openssl rand -hex 24)
ENV
cp backend/.env.example backend/.env
setenv() { sed -i "/^$1=/d" backend/.env; echo "$1=$2" >> backend/.env; }
setenv APP_ENV prod
setenv AUTH_PROVIDER password
setenv SECRET_KEY "$(openssl rand -hex 32)"
setenv MFA_ENCRYPTION_KEY "$(openssl rand -base64 32)"
setenv MFA_POLICY optional
setenv FILE_SCAN clamav
setenv CORS_ORIGINS "https://$DOMAIN"
setenv OIDC_REDIRECT_URI "https://$DOMAIN/api/auth/callback"
sed -i '/^DATABASE_URL=/d' backend/.env     # адрес базы с паролем задаёт docker-compose из POSTGRES_PASSWORD
chown deploy:deploy .env backend/.env; chmod 600 .env backend/.env
install -d -m 750 /etc/gsho
[ -f /etc/gsho/deploy.env ] || echo "BACKUP_REQUIRED=false" > /etc/gsho/deploy.env   # true — после настройки копий (install.md, шаг 6)

echo "== файрвол"
if command -v ufw >/dev/null; then ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw --force enable >/dev/null; fi

if [ -n "$MSS" ]; then   # только Yandex Cloud: полноразмерные пакеты теряются по пути
  cat > /etc/systemd/system/gsho-mss.service <<UNIT
[Unit]
Description=Clamp TCP MSS on eth0
After=network-online.target docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -t mangle -C POSTROUTING -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360 2>/dev/null || iptables -t mangle -A POSTROUTING -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1360"
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload && systemctl enable --now gsho-mss.service
fi

echo "== сборка (5–10 минут) и запуск базы и антивируса"
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile scanner)
sudo -u deploy "${DC[@]}" build -q backend frontend clamav
sudo -u deploy "${DC[@]}" up -d db clamav

cat <<TXT

ГОТОВО. CRM ещё НЕ запущена — так и должно быть.
Секреты созданы на сервере и на экран не выводились. Сохраните их в защищённую заметку (в чат не присылать):
  sudo cat $DIR/.env                # пароль базы
  sudo grep -E '^(SECRET_KEY|MFA_ENCRYPTION_KEY)=' $DIR/backend/.env
Без MFA_ENCRYPTION_KEY после потери сервера коды из приложения у всех сотрудников перестанут работать.
Дальше: $DIR/deploy/production/install.md, шаг 3.
TXT
