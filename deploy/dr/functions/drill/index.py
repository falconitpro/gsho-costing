"""Еженедельная проверка резерва crm2 (D-084). Yandex Cloud Function по таймеру (ночь на воскресенье).

Записывает в метаданные ВМ `dr-drill-until` = сейчас + DRILL_WINDOW_MIN и запускает её. На ВМ gsho-dr-drill.service
видит метку и выполняет drill.sh --poweroff: восстановление последней копии в режиме recovery, проверка, письмо
с результатом, выключение. Если ВМ не выключена (идёт переключение или работа) — ничего не делает.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

API = "https://compute.api.cloud.yandex.net/compute/v1/instances/"
OPS = "https://operation.api.cloud.yandex.net/operations/"


def call(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def wait_op(op: dict, token: str, limit: int = 90) -> None:
    for _ in range(limit // 3):
        if op.get("done"):
            if "error" in op:
                raise RuntimeError(op["error"])
            return
        time.sleep(3)
        op = call("GET", OPS + op["id"], token)
    raise TimeoutError(f"операция {op.get('id')} не завершилась")


def handler(event, context):
    token = context.token["access_token"]
    vm = os.environ["STANDBY_VM_ID"]
    inst = call("GET", API + vm, token)
    if inst.get("status") != "STOPPED":
        print(json.dumps({"skipped": inst.get("status")}))
        return {"statusCode": 200, "body": f"ВМ в статусе {inst.get('status')} — проверка не запускается"}
    until = int(time.time()) + int(os.environ.get("DRILL_WINDOW_MIN", "50")) * 60
    wait_op(call("POST", API + vm + ":updateMetadata", token, {"upsert": {"dr-drill-until": str(until)}}), token)
    call("POST", API + vm + ":start", token, {})
    print(json.dumps({"started": vm, "until": until}))
    return {"statusCode": 200, "body": "проверка запущена"}
