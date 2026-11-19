#!/usr/bin/env bash
# Защита от расхождения данных (D-084): на РАБОЧЕМ сервере раз в минуту (gsho-dr-guard.timer) читает флаг
# «работаем на резерве» из Object Storage. Флаг стоит → Caddy на crm.dobrov.pro переадресует на crm2 и не пускает
# в приложение (устаревшая база не принимает данные). Флага нет → обычная работа.
# Нет ответа или непонятный ответ → ничего не меняется: сбой Яндекса не должен останавливать рабочий CRM.
#
# Окружение (/etc/gsho/dr-guard.env): DR_FLAG_URL — публичный адрес failover.json (или путь к файлу — для тестов).
set -uo pipefail
GSHO_DIR="${GSHO_DIR:-/opt/gsho-costing}"
SNIPPET="${DR_SNIPPET:-$GSHO_DIR/deploy/caddy-tls/zz-dr-redirect.caddy}"
URL="${DR_FLAG_URL:-}"
[ -n "$URL" ] || { echo "gsho-dr-guard: DR_FLAG_URL не задан" >&2; exit 0; }

reload() {
  if [ -n "${DR_CADDY_RELOAD_CMD:-}" ]; then bash -c "$DR_CADDY_RELOAD_CMD"; return; fi
  (cd "$GSHO_DIR" && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1) || echo "gsho-dr-guard: caddy reload не удался" >&2
}

TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
if [[ "$URL" == /* ]]; then
  if [ -f "$URL" ]; then cp "$URL" "$TMP"; CODE=200; else CODE=404; fi
else
  CODE="$(curl -sS -m 10 -o "$TMP" -w '%{http_code}' "$URL" 2>/dev/null || true)"
fi

case "$CODE" in
  200)
    TARGET="$(python3 - "$TMP" <<'PY' 2>/dev/null
import json, re, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
t = str(d.get("target") or "")
if d.get("active") is True and re.fullmatch(r"https://[a-z0-9.-]+", t):
    print(t)
elif d.get("active") is False:
    print("off")
PY
)"
    ;;
  403|404) TARGET="off" ;;   # объекта нет (Object Storage отвечает 404, а при закрытом списке — 403)
  *) exit 0 ;;               # нет ответа — состояние не меняем
esac

if [ "$TARGET" = "off" ]; then
  if [ -f "$SNIPPET" ]; then rm -f "$SNIPPET"; echo "gsho-dr-guard: флаг снят — переадресация на резерв выключена"; reload; fi
elif [ -n "$TARGET" ]; then
  WANT="# создано deploy/dr/flag-guard.sh: идёт работа на резервном сервере (D-084)
redir $TARGET{uri} 302"
  if [ ! -f "$SNIPPET" ] || [ "$(cat "$SNIPPET")" != "$WANT" ]; then
    printf '%s\n' "$WANT" > "$SNIPPET"; echo "gsho-dr-guard: флаг стоит — переадресация на $TARGET"; reload
  fi
fi
exit 0
