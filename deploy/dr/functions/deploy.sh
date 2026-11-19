#!/usr/bin/env bash
# Мониторинг CRM и еженедельная проверка резерва crm2 в Yandex Cloud (D-084). Запускать в Cloud Shell консоли Яндекса
# (там yc уже вошёл от владельца); повторный запуск обновляет код функций и ничего не дублирует:
#   git clone -q https://github.com/falconitpro/gsho-costing && bash gsho-costing/deploy/dr/functions/deploy.sh
# Создаёт: SA sa-crm-monitor (postbox.sender на каталог, compute.editor только на ВМ crm2-standby), бакет состояния,
# функции crm-monitor (раз в 5 минут) и crm2-drill (ночь на воскресенье, 03:30 МСК), их таймеры.
# Права SA на бакеты (чтение копий, запись состояния) назначаются в консоли — см. deploy/dr/README.md §5.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FOLDER_ID="${FOLDER_ID:-$(yc config get folder-id)}"
VM_NAME="${VM_NAME:-crm2-standby}"
SA_NAME="${SA_NAME:-sa-crm-monitor}"
STATE_BUCKET="${STATE_BUCKET:-berghaus-crm-monitor-state}"
NOTIFY_FROM="${NOTIFY_FROM:-crm-monitor@notify.dobrov.pro}"
NOTIFY_TO="${NOTIFY_TO:-evgeniy@dobrov.pro;e.dobrov@berg-haus.com;s.volokhov@berg-haus.com}"
id_of() { python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'; }

VM_ID="$(yc compute instance get "$VM_NAME" --format json | id_of)"
VM_SA_ID="$(yc compute instance get "$VM_NAME" --format json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("service_account_id",""))')"
SA_ID="$(yc iam service-account get "$SA_NAME" --format json 2>/dev/null | id_of || true)"
[ -n "$SA_ID" ] || SA_ID="$(yc iam service-account create --name "$SA_NAME" \
  --description 'Мониторинг CRM и проверка резерва crm2 (D-084)' --format json | id_of)"
echo "SA $SA_NAME: $SA_ID; ВМ $VM_NAME: $VM_ID"

# роли: ошибки не глушим — 19.11 привязка сразу после создания SA молча не применилась
sleep 5
yc resource-manager folder add-access-binding "$FOLDER_ID" --role postbox.sender --subject "serviceAccount:$SA_ID" >/dev/null
[ -z "$VM_SA_ID" ] || yc resource-manager folder add-access-binding "$FOLDER_ID" --role postbox.sender \
  --subject "serviceAccount:$VM_SA_ID" >/dev/null
yc compute instance add-access-binding "$VM_NAME" --role compute.editor --service-account-id "$SA_ID" >/dev/null
yc resource-manager folder list-access-bindings "$FOLDER_ID" | grep -q "postbox.sender.*$SA_ID" || echo "ВНИМАНИЕ: нет postbox.sender у $SA_NAME"

yc storage bucket get "$STATE_BUCKET" >/dev/null 2>&1 \
  || yc storage bucket create --name "$STATE_BUCKET" --default-storage-class standard --max-size 10485760 >/dev/null
echo "бакет состояния: $STATE_BUCKET"

deploy_fn() {  # deploy_fn <имя> <каталог> <таймаут> <переменные>
  yc serverless function get --name "$1" >/dev/null 2>&1 || yc serverless function create --name "$1" >/dev/null
  yc serverless function version create --function-name "$1" --runtime python312 --entrypoint index.handler \
    --memory 128m --execution-timeout "$3" --service-account-id "$SA_ID" --source-path "$2" --environment "$4" >/dev/null
  yc serverless function add-access-binding --name "$1" --role functions.functionInvoker \
    --service-account-id "$SA_ID" >/dev/null 2>&1 || true
  echo "функция $1: новая версия"
}
deploy_fn crm-monitor "$HERE/monitor" 120s \
  "NOTIFY_FROM=$NOTIFY_FROM,NOTIFY_TO=$NOTIFY_TO,STANDBY_VM_ID=$VM_ID,STATE_BUCKET=$STATE_BUCKET"
deploy_fn crm2-drill "$HERE/drill" 120s "STANDBY_VM_ID=$VM_ID"

trigger() {  # trigger <имя> <cron> <функция>
  yc serverless trigger get --name "$1" >/dev/null 2>&1 && { echo "таймер $1 уже есть"; return; }
  yc serverless trigger create timer --name "$1" --cron-expression "$2" --invoke-function-name "$3" \
    --invoke-function-service-account-id "$SA_ID" >/dev/null
  echo "таймер $1: $2"
}
trigger crm-monitor-5min '*/5 * ? * * *' crm-monitor
trigger crm2-drill-weekly '30 0 ? * SUN *' crm2-drill   # 00:30 UTC = 03:30 МСК, воскресенье
echo "ГОТОВО. Проверка: yc serverless function invoke --name crm-monitor"
echo "Пробное письмо: yc serverless function invoke --name crm-monitor -d '{\"test_mail\": true}'"
