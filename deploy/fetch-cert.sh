#!/usr/bin/env bash
# Сертификат домена из Yandex Certificate Manager (Let's Encrypt с DNS-проверкой — не зависит от входящих соединений из-за рубежа).
# Нужно: CM_CERT_ID в /opt/gsho-costing/.env и сервисный аккаунт ВМ с ролью certificate-manager.certificates.downloader.
# Запускается таймером раз в сутки; при смене сертификата перезагружает Caddy.
set -euo pipefail
cd /opt/gsho-costing
CM_CERT_ID=$(grep -E '^CM_CERT_ID=' .env | cut -d= -f2- || true)
[ -n "$CM_CERT_ID" ] || { echo "CM_CERT_ID не задан"; exit 0; }
TOKEN=$(curl -fsS -m 10 -H Metadata-Flavor:Google http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
JSON=$(curl -fsS -m 30 -H "Authorization: Bearer $TOKEN" "https://data.certificate-manager.api.cloud.yandex.net/certificate-manager/v1/certificates/$CM_CERT_ID:getContent")
install -d -m 750 deploy/certs deploy/caddy-tls
TMP=$(mktemp -d)
printf '%s' "$JSON" | python3 -c '
import json,sys
d=json.load(sys.stdin)
open(sys.argv[1]+"/fullchain.pem","w").write("".join(c if c.endswith("\n") else c+"\n" for c in d["certificateChain"]))
open(sys.argv[1]+"/key.pem","w").write(d["privateKey"])' "$TMP"
if ! cmp -s "$TMP/fullchain.pem" deploy/certs/fullchain.pem 2>/dev/null; then
  install -m 644 "$TMP/fullchain.pem" deploy/certs/fullchain.pem
  install -m 640 "$TMP/key.pem" deploy/certs/key.pem
  echo "tls /certs/fullchain.pem /certs/key.pem" > deploy/caddy-tls/domain.caddy
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile \
    || docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate caddy
  echo "$(date -Is) сертификат обновлён"
fi
rm -rf "$TMP"
