"""Мониторинг рабочего CRM и резерва crm2 (D-084). Yandex Cloud Function, таймер раз в 5 минут.

Проверяет:
  health   — https://crm.dobrov.pro/api/health (две попытки с паузой; при переключении crm переадресует на crm2 — это норма);
  backup   — свежесть копий рабочего CRM в Object Storage (последний снимок restic не старше BACKUP_MAX_AGE_MIN);
  standby  — резервная ВМ включена дольше STANDBY_MAX_HOURS без флага «работаем на резерве» (забыли выключить);
  failover — флаг стоит: работаем на резерве (напоминание раз в FAILOVER_REMIND_MIN).
Письма — через Yandex Cloud Postbox по IAM-токену сервисного аккаунта функции (паролей и ключей нет).
Состояние (когда началась проблема, когда писали) — в приватном бакете STATE_BUCKET: письмо при появлении проблемы,
напоминание раз в REMIND_MIN, письмо «восстановлено» при исчезновении.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
MSK = dt.timezone(dt.timedelta(hours=3))
TITLES = {
    "health": "CRM не отвечает",
    "backup": "нет свежих копий CRM",
    "standby": "резервный сервер включён без флага",
    "failover": "CRM работает на резервном сервере crm2",
}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def http(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


# ---------- проверки ----------

def check_health() -> str | None:
    url = env("HEALTH_URL", "https://crm.dobrov.pro/api/health")
    err = ""
    for attempt in range(2):
        try:
            status, _ = http("GET", url, timeout=15)
            if status == 200:
                return None
            err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — любая сетевая ошибка = недоступен
            err = f"{type(e).__name__}: {str(e)[:200]}"
        if attempt == 0:
            time.sleep(int(env("HEALTH_RETRY_SEC", "20")))
    return f"{url} не отвечает ({err})"


def newest_snapshot_age_min(token: str, now: float) -> float | None:
    bucket = env("BACKUP_BUCKET", "berghaus-crm-prod-backup")
    prefix = env("BACKUP_PREFIX", "crm-prod/snapshots/")
    newest, cont = None, None
    for _ in range(10):  # до 10 000 объектов
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if cont:
            q["continuation-token"] = cont
        _, body = http("GET", f"https://storage.yandexcloud.net/{bucket}?{urllib.parse.urlencode(q)}",
                       headers={"X-YaCloud-SubjectToken": token})
        root = ET.fromstring(body)
        for el in root.iter(f"{S3_NS}LastModified"):
            t = dt.datetime.fromisoformat(el.text.replace("Z", "+00:00")).timestamp()
            newest = t if newest is None or t > newest else newest
        if (root.findtext(f"{S3_NS}IsTruncated") or "").lower() != "true":
            break
        cont = root.findtext(f"{S3_NS}NextContinuationToken")
    return None if newest is None else (now - newest) / 60


def check_backup(token: str, now: float) -> str | None:
    limit = int(env("BACKUP_MAX_AGE_MIN", "50"))
    age = newest_snapshot_age_min(token, now)
    if age is None:
        return "в хранилище копий нет ни одного снимка рабочего CRM"
    if age > limit:
        return f"последняя копия рабочего CRM сделана {int(age)} мин назад (должна каждые 15 мин)"
    return None


def flag_active() -> bool:
    url = env("FLAG_URL", "https://storage.yandexcloud.net/berghaus-crm-dr-flag/failover.json")
    try:
        status, body = http("GET", url, timeout=10)
        return status == 200 and json.loads(body).get("active") is True
    except Exception:  # noqa: BLE001 — 403/404/нет ответа = флага нет
        return False


def standby_running(token: str) -> bool:
    vm = env("STANDBY_VM_ID")
    if not vm:
        return False
    _, body = http("GET", f"https://compute.api.cloud.yandex.net/compute/v1/instances/{vm}",
                   headers={"Authorization": f"Bearer {token}"})
    return json.loads(body).get("status") == "RUNNING"


# ---------- решение: что и когда писать (без сети — проверяется тестами) ----------

def decide(state: dict, current: dict, now: float, cfg: dict) -> tuple[dict, list]:
    """state: {key: {since, notified, text}}; current: {key: text} — проблемы сейчас.
    Возвращает новое состояние и письма [(kind, key, text, since)], kind = problem | resolved."""
    new, mails = {}, []
    for key, text in current.items():
        prev = state.get(key) or {}
        since = prev.get("since", now)
        notified = prev.get("notified", 0)
        entry = {"since": since, "notified": notified, "text": text}
        ready = now - since >= cfg.get("delay", {}).get(key, 0) * 60
        remind = cfg.get("remind", {}).get(key, 60) * 60
        if ready and (not notified or now - notified >= remind):
            mails.append(("problem", key, text, since))
            entry["notified"] = now
        new[key] = entry
    for key, prev in state.items():
        if key not in current and prev.get("notified"):
            mails.append(("resolved", key, prev.get("text", ""), prev.get("since", now)))
    return new, mails


# ---------- состояние и письма ----------

def state_url() -> str:
    return f"https://storage.yandexcloud.net/{env('STATE_BUCKET', 'berghaus-crm-monitor-state')}/monitor-state.json"


def load_state(token: str) -> dict:
    try:
        _, body = http("GET", state_url(), headers={"X-YaCloud-SubjectToken": token})
        return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def save_state(token: str, state: dict) -> None:
    http("PUT", state_url(), body=json.dumps(state).encode(),
         headers={"X-YaCloud-SubjectToken": token, "Content-Type": "application/json"})


def fmt(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, MSK).strftime("%d.%m.%Y %H:%M МСК")


def send_mail(token: str, subject: str, text: str) -> None:
    to = [a for a in re.split(r"[;,\s]+", env("NOTIFY_TO")) if a]
    if not to:
        return
    body = {"FromEmailAddress": env("NOTIFY_FROM", "crm-monitor@notify.dobrov.pro"),
            "Destination": {"ToAddresses": to},
            "Content": {"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"},
                                   "Body": {"Text": {"Data": text, "Charset": "UTF-8"}}}}}
    http("POST", "https://postbox.cloud.yandex.net/v2/email/outbound-emails", body=json.dumps(body).encode(),
         headers={"X-YaCloud-SubjectToken": token, "Content-Type": "application/json"})


def mail_text(kind: str, key: str, text: str, since: float, now: float) -> tuple[str, str]:
    guide = env("GUIDE_URL", "https://claude.ai/artifact/8t7uWEbDt8dz1UTvpaajK6")
    if kind == "resolved":
        return (f"CRM: восстановлено — {TITLES[key]}",
                f"Проблема устранена: {text}\nНачалась: {fmt(since)}\nЗакрыта: {fmt(now)}\n")
    lines = [text, f"Началась: {fmt(since)}", ""]
    if key == "health":
        lines += ["Порядок действий — инструкция администратора, раздел 1 «Когда переключаться»:", guide]
    elif key == "failover":
        lines += ["Это напоминание: после восстановления рабочего сервера — возврат по разделу 5 инструкции:", guide]
    elif key == "standby":
        lines += ["Если переключения нет и проверка резерва не идёт — выключите crm2-standby в консоли Yandex Cloud."]
    else:
        lines += ["Проверьте на рабочем сервере: systemctl list-timers 'gsho-*' и gsho-backup-status.sh --check"]
    return f"CRM: {TITLES[key]}", "\n".join(lines) + "\n"


def handler(event, context):
    token = context.token["access_token"]
    now = time.time()
    current: dict = {}
    for key, fn in (("health", check_health), ("backup", lambda: check_backup(token, now))):
        try:
            msg = fn()
        except Exception as e:  # noqa: BLE001 — сбой самой проверки тоже сообщаем
            msg = f"проверка не выполнилась: {type(e).__name__}: {str(e)[:200]}"
        if msg:
            current[key] = msg
    try:
        flag = flag_active()
        if flag:
            current["failover"] = "стоит флаг «работаем на резерве»: сотрудники работают на https://crm2.dobrov.pro"
        elif standby_running(token):
            current["standby"] = "резервная ВМ crm2-standby включена, а флага переключения нет"
    except Exception as e:  # noqa: BLE001
        print(f"standby check failed: {e}")
    cfg = {"delay": {"standby": int(env("STANDBY_MAX_HOURS", "3")) * 60},
           "remind": {"health": int(env("REMIND_MIN", "60")), "backup": int(env("REMIND_MIN", "60")),
                      "standby": 24 * 60, "failover": int(env("FAILOVER_REMIND_MIN", "360"))}}
    state = load_state(token)
    new_state, mails = decide(state, current, now, cfg)
    for kind, key, text, since in mails:
        subject, body = mail_text(kind, key, text, since, now)
        try:
            send_mail(token, subject, body)
        except Exception as e:  # noqa: BLE001
            print(f"mail failed: {e}")
            if kind == "problem":
                new_state[key]["notified"] = 0  # не отправилось — попробуем в следующий раз
    if new_state != state:
        save_state(token, new_state)
    result = {"problems": sorted(current), "mails": [m[:2] for m in mails]}
    print(json.dumps(result, ensure_ascii=False))
    return {"statusCode": 200, "body": json.dumps(result, ensure_ascii=False)}
