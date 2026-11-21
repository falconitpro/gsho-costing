#!/usr/bin/env bash
# Подключение ИИ для писем Канцелярии — Yandex AI Studio (D-134). Данные остаются в РФ.
# Запуск на сервере:  sudo bash /opt/gsho-costing/deploy/production/ai-setup.sh
# Заранее в Yandex Cloud (console.yandex.cloud): каталог → сервисный аккаунт с ролью ai.languageModels.user →
#   API-ключ этого аккаунта с областью yc.ai.languageModels.execute. Нужны ID каталога (b1g…) и секрет ключа.
# Ключ вводится скрытно и пишется только в backend/.env (права 600) — в чат и в журнал не попадает.
set -euo pipefail
cd "${GSHO_DIR:-/opt/gsho-costing}"
DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
ENVF=backend/.env
[ -f "$ENVF" ] || { echo "Нет $ENVF — сервер не настроен"; exit 1; }

read -rp "ID каталога Yandex Cloud (b1g…): " FOLDER
read -rsp "Секрет API-ключа (ввод не отображается): " KEY; echo
read -rp "Модель для писем [yandexgpt/rc — YandexGPT Pro 5.1]: " MODEL; MODEL="${MODEL:-yandexgpt/rc}"
read -rp "Модель для перевода [qwen3-235b-a22b-fp8/latest — Qwen3 235B]: " TMODEL; TMODEL="${TMODEL:-qwen3-235b-a22b-fp8/latest}"
if [ -z "$FOLDER" ] || [ -z "$KEY" ]; then echo "Каталог и ключ обязательны — ничего не изменено"; exit 1; fi

cp -p "$ENVF" "$ENVF.bak-ai-$(date +%Y%m%d%H%M%S)"
setenv() { sed -i "/^$1=/d" "$ENVF"; printf '%s=%s\n' "$1" "$2" >> "$ENVF"; }
setenv YANDEX_FOLDER_ID "$FOLDER"
setenv LLM_YANDEX_API_KEY "$KEY"
setenv OFFICE_AI_MODEL "$MODEL"
setenv OFFICE_AI_MODEL_TRANSLATE "$TMODEL"
setenv OFFICE_AI on
chmod 600 "$ENVF"
unset KEY

echo "Перезапуск сервера приложения…"
"${DC[@]}" up -d backend >/dev/null
sleep 8
echo "Проверочный запрос к модели…"
if "${DC[@]}" exec -T backend python -c '
from app.services import office_ai as oa
out, warn, llm = oa.edit("Просим сообщить срок поставки ковша по договору 45-П до 05.12.2026.", "business", "Исходящее письмо")
print("Модель ответила:", out[:200])
'; then
  echo "Готово: в карточках писем появился блок «ИИ». Выключить — OFFICE_AI=off в backend/.env и перезапуск."
else
  echo "Модель не ответила. Проверьте ID каталога, роль ai.languageModels.user у сервисного аккаунта и область ключа yc.ai.languageModels.execute."
fi
