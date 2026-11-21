#!/usr/bin/env bash
# Подключение почты для писем системы (D-132: ссылки «Задать пароль», «Пароль изменён»).
# Запуск на сервере:  sudo bash /opt/gsho-costing/deploy/production/mail-setup.sh
# iCloud+ со своим доменом (dobrov.pro): сервер smtp.mail.me.com:587 (STARTTLS);
#   логин — основной адрес Apple ID в iCloud (…@icloud.com), пароль — ПАРОЛЬ ПРИЛОЖЕНИЯ (account.apple.com → Вход и безопасность → Пароли приложений);
#   отправитель — адрес вашего домена, заведённый в iCloud+ → Свой домен почты (например noreply@dobrov.pro).
# Пароль вводится скрытно и пишется только в backend/.env (права 600) — в чат и в журнал не попадает.
set -euo pipefail
cd "${GSHO_DIR:-/opt/gsho-costing}"
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
ENVF=backend/.env
[ -f "$ENVF" ] || { echo "Нет $ENVF — сервер не настроен"; exit 1; }

read -rp "SMTP-сервер [smtp.mail.me.com]: " HOST; HOST="${HOST:-smtp.mail.me.com}"
read -rp "Порт [587]: " PORT; PORT="${PORT:-587}"
read -rp "Логин (адрес Apple ID в iCloud, …@icloud.com): " USER_
read -rsp "Пароль приложения (ввод не отображается): " PASS; echo
read -rp "Отправитель (адрес вашего домена, например noreply@dobrov.pro): " FROM
read -rp "Имя отправителя [CRM Берг-Хаус]: " NAME; NAME="${NAME:-CRM Берг-Хаус}"
read -rp "Куда отправить проверочное письмо (ваш e-mail): " TEST
if [ -z "$USER_" ] || [ -z "$PASS" ] || [ -z "$FROM" ]; then echo "Логин, пароль и отправитель обязательны — ничего не изменено"; exit 1; fi

cp -p "$ENVF" "$ENVF.bak-mail-$(date +%Y%m%d%H%M%S)"
setenv() { sed -i "/^$1=/d" "$ENVF"; printf '%s=%s\n' "$1" "$2" >> "$ENVF"; }
setenv MAIL_PROVIDER smtp
setenv SMTP_HOST "$HOST"
setenv SMTP_PORT "$PORT"
setenv SMTP_USER "$USER_"
setenv SMTP_PASSWORD "$PASS"
setenv SMTP_FROM "$NAME <$FROM>"
chmod 600 "$ENVF"
unset PASS

echo "Перезапуск сервера приложения…"
"${DC[@]}" up -d backend >/dev/null
sleep 8
echo "Проверочное письмо на $TEST…"
if "${DC[@]}" exec -T -e TEST_TO="$TEST" backend python -c '
import os
from app.adapters import mail
from app.adapters.mail import OutgoingMail
mail().send(OutgoingMail(to=[os.environ["TEST_TO"]], subject="Проверка почты CRM", body_html="<p>Почта системы подключена. Ссылки «Задать пароль» будут приходить с этого адреса.</p>"))
print("Отправлено")
'; then
  echo "Готово. Проверьте ящик $TEST (и папку «Спам»)."
else
  echo "Письмо не ушло. Проверьте логин (…@icloud.com), пароль приложения и что адрес отправителя заведён в iCloud+ для этого Apple ID."
  echo "Пока почта не работает, ссылки «Задать пароль» показываются администратору на экране — система работает и так."
fi
