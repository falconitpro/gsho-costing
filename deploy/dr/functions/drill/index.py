"""Еженедельная проверка резерва crm2 (D-084). Yandex Cloud Function по таймеру (ночь на воскресенье).

Записывает в метаданные ВМ `dr-drill-until` = сейчас + DRILL_WINDOW_MIN и запускает её. На ВМ gsho-dr-drill.service
видит метку и выполняет drill.sh --poweroff: восстановление последней копии в режиме recovery, проверка, письмо
с результатом, выключение. Если ВМ не выключена (идёт переключение или работа) — ничего не делает.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = "https://compute.api.cloud.yandex.net/compute/v1/instances/"


def call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def wait_label(vm: str, token: str, value: str, limit: int = 90) -> None:
    """Ждёт, пока метка появится в метаданных ВМ (статус операции через Operation API сервисному аккаунту с ролью
    только на ВМ не виден — 403, поэтому смотрим на саму ВМ)."""
    for _ in range(limit // 3):
        inst = call("GET", API + vm + "?view=FULL", token)
        if (inst.get("metadata") or {}).get("dr-drill-until") == value:
            return
        time.sleep(3)
    raise TimeoutError("метка dr-drill-until не появилась в метаданных ВМ")


def start(vm: str, token: str) -> None:
    for attempt in range(10):  # пока метаданные применяются, ВМ может быть «занята»
        try:
            call("POST", API + vm + ":start", token, {})
            return
        except urllib.error.HTTPError as e:
            if e.code not in (400, 409) or attempt == 9:
                raise
            time.sleep(5)


def handler(event, context):
    token = context.token["access_token"]
    vm = os.environ["STANDBY_VM_ID"]
    inst = call("GET", API + vm + "?view=FULL", token)
    if inst.get("status") != "STOPPED":
        print(json.dumps({"skipped": inst.get("status")}))
        return {"statusCode": 200, "body": f"ВМ в статусе {inst.get('status')} — проверка не запускается"}
    # Окно короткое: ВМ загружается за 1–2 минуты, а ручной запуск для переключения в это окно запустил бы проверку
    until = int(time.time()) + int(os.environ.get("DRILL_WINDOW_MIN", "15")) * 60
    # В REST нет updateMetadata: метаданные меняются PATCH целиком — берём текущие (OS Login, серийная консоль) и добавляем метку
    meta = dict(inst.get("metadata") or {})
    meta["dr-drill-until"] = str(until)
    call("PATCH", API + vm, token, {"updateMask": "metadata", "metadata": meta})
    wait_label(vm, token, str(until))
    start(vm, token)
    print(json.dumps({"started": vm, "until": until}))
    return {"statusCode": 200, "body": "проверка запущена"}
