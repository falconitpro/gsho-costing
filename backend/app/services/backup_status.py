"""Статус резервного копирования для CRM (docs/operations/03 §5, 02 §4; D-024…D-027).

Скрипт на хосте (deploy/backup/gsho-backup.sh) пишет атомарно `last-run.json` и `runs/<…>.json` в каталог статуса,
который смонтирован в backend только для чтения (BACKUP_STATUS_DIR). Здесь:

- разбор файла по фиксированной схеме с лимитом размера; неизвестные поля отбрасываются, строки обрезаются и чистятся;
- перенос запусков в `backup_runs` (идемпотентно по run_key);
- вычисление состояния: not_configured / running / ok / failed / stale (нет успеха дольше 26 ч);
- уведомления только «в системе» (NotificationLog channel="app") тем, у кого есть `backup_status`, без повторов.

Backend никогда не запускает shell-команды и не видит ключей бакета или пароля restic.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import BackupRun, IntegrationLog, NotificationLog, User

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 16 * 1024          # last-run.json — несколько сотен байт; больше — значит, не наш файл
MAX_RUN_FILES = 200                 # сколько файлов истории читать за один проход
STALE_AFTER = timedelta(hours=26)   # 03 §12: последний успешный daily моложе 26 часов
SCHEDULE_TZ = ZoneInfo("Europe/Moscow")
SCHEDULE_AT = (19, 0)               # gsho-backup.timer: OnCalendar=*-*-* 19:00:00, Timezone=Europe/Moscow

REASONS = {"daily", "pre_deploy", "manual", "restore_check"}
STATUSES = {"running", "success", "failed"}
PROVIDERS = {"local", "s3", "unknown"}
SUB_STATUSES = {"", "ok", "failed", "skipped"}
ERROR_CODES = {
    "", "config_missing", "staging_failed", "disk_full", "db_dump_failed", "dump_unreadable", "files_failed",
    "unsupported_storage", "restic_backup_failed", "snapshot_missing", "check_failed", "retention_failed",
    "status_write_failed", "interrupted", "unknown",
}
ERROR_TEXTS = {
    "config_missing": "Не заданы обязательные параметры в /etc/gsho/backup.env",
    "staging_failed": "Не удалось подготовить временный каталог на сервере",
    "disk_full": "Не хватает места во временном каталоге на сервере",
    "db_dump_failed": "Не удалось выгрузить базу данных (pg_dump)",
    "dump_unreadable": "Выгрузка базы не читается (pg_restore --list)",
    "files_failed": "Не удалось упаковать вложения",
    "unsupported_storage": "Вложения хранятся не локально — этот контур их не копирует",
    "restic_backup_failed": "Не удалось отправить копию в Yandex Object Storage",
    "snapshot_missing": "Снимок не найден в репозитории после отправки",
    "check_failed": "Проверка целостности репозитория (restic check) не прошла",
    "retention_failed": "Копия создана, но очистка старых снимков (forget --prune) не прошла",
    "status_write_failed": "Не удалось записать статус на сервере",
    "interrupted": "Копирование прервано",
    "unknown": "Неизвестная ошибка",
}

_SECRETISH = [
    re.compile(r"(?i)(password|passwd|secret|token|key|credential)[^\s=:]*\s*[=:]\s*\S+"),
    re.compile(r"(?i)(X-Amz-[A-Za-z-]+|Signature|AWSAccessKeyId)=[^&\s]+"),
    re.compile(r"https?://\S+"),
    re.compile(r"s3:\S+"),
    re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}"),
]
_HEX = re.compile(r"^[0-9a-f]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class StatusFileError(ValueError):
    """Файл статуса не соответствует схеме — в UI это «ошибка статуса», не «успех»."""


def status_dir() -> str:
    return os.environ.get("BACKUP_STATUS_DIR", "")


def sanitize(text: Any, limit: int = 300) -> str:
    s = str(text or "").replace("\n", " ").replace("\r", " ")
    for rx in _SECRETISH:
        s = rx.sub("[скрыто]", s)
    return s[:limit].strip()


def _dt(v: Any, field: str) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or len(v) > 40:
        raise StatusFileError(f"{field}: ожидается время ISO 8601")
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise StatusFileError(f"{field}: ожидается время ISO 8601") from e
    if d.tzinfo is None:
        raise StatusFileError(f"{field}: время без часового пояса")
    return d.astimezone(timezone.utc)


def _int(v: Any, field: str) -> Optional[int]:
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 10**15:
        raise StatusFileError(f"{field}: ожидается неотрицательное целое")
    return v


def _enum(v: Any, allowed: set[str], field: str, default: str = "") -> str:
    v = default if v is None else v
    if not isinstance(v, str) or v not in allowed:
        raise StatusFileError(f"{field}: недопустимое значение")
    return v


def _token(v: Any, rx: re.Pattern, maxlen: int, field: str) -> Optional[str]:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or len(v) > maxlen or not rx.match(v):
        raise StatusFileError(f"{field}: недопустимый формат")
    return v


def parse_status(raw: bytes) -> dict:
    """Разбор одного файла статуса. Возвращает только известные поля в безопасном виде."""
    if len(raw) > MAX_FILE_BYTES:
        raise StatusFileError("файл статуса слишком большой")
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise StatusFileError("файл статуса — не JSON") from e
    if not isinstance(d, dict) or d.get("schema") != 1:
        raise StatusFileError("неизвестная версия схемы статуса")
    run_key = d.get("run_key")
    if not isinstance(run_key, str) or not _UUID.match(run_key):
        raise StatusFileError("run_key: ожидается UUID")
    out = {
        "run_key": run_key,
        "reason": _enum(d.get("reason"), REASONS, "reason"),
        "status": _enum(d.get("status"), STATUSES, "status"),
        "started_at": _dt(d.get("started_at"), "started_at"),
        "finished_at": _dt(d.get("finished_at"), "finished_at"),
        "snapshot_id": _token(d.get("snapshot_id"), _HEX, 16, "snapshot_id"),
        "git_commit": _token(d.get("git_commit"), _HEX, 40, "git_commit"),
        "schema_revision": _token(d.get("schema_revision"), re.compile(r"^[0-9A-Za-z_.-]+$"), 64, "schema_revision"),
        "storage_provider": _enum(d.get("storage_provider"), PROVIDERS, "storage_provider", "unknown"),
        "database_bytes": _int(d.get("database_bytes"), "database_bytes"),
        "files_bytes": _int(d.get("files_bytes"), "files_bytes"),
        "files_count": _int(d.get("files_count"), "files_count"),
        "manifest_sha256": _token(d.get("manifest_sha256"), _HEX, 64, "manifest_sha256"),
        "check_status": _enum(d.get("check_status"), SUB_STATUSES, "check_status"),
        "checked_at": _dt(d.get("checked_at"), "checked_at"),
        "retention_status": _enum(d.get("retention_status"), SUB_STATUSES, "retention_status"),
        "error_code": _enum(d.get("error_code"), ERROR_CODES, "error_code"),
        "error_summary": sanitize(d.get("error_summary")),
    }
    if out["status"] == "success" and not out["snapshot_id"]:
        raise StatusFileError("успех без snapshot_id не принимается")
    return out


@dataclass
class HostStatus:
    last: Optional[dict]
    runs: list[dict]
    errors: list[str]
    configured: bool


def read_host(path: Optional[str] = None) -> HostStatus:
    """Чтение каталога статуса. Ошибки отдельных файлов не валят чтение, но видны в UI."""
    root = path if path is not None else status_dir()
    if not root or not os.path.isdir(root):
        return HostStatus(None, [], [], False)
    errors: list[str] = []

    def _read(p: str) -> Optional[dict]:
        try:
            if os.path.islink(p) or not os.path.isfile(p):
                return None
            with open(p, "rb") as f:
                return parse_status(f.read(MAX_FILE_BYTES + 1))
        except StatusFileError as e:
            errors.append(f"{os.path.basename(p)}: {e}")
        except OSError:
            errors.append(f"{os.path.basename(p)}: не читается")
        return None

    last_path = os.path.join(root, "last-run.json")
    last = _read(last_path)
    runs: list[dict] = []
    rdir = os.path.join(root, "runs")
    if os.path.isdir(rdir):
        names = sorted((n for n in os.listdir(rdir) if n.endswith(".json")), reverse=True)[:MAX_RUN_FILES]
        for n in names:
            r = _read(os.path.join(rdir, n))
            if r:
                runs.append(r)
    configured = os.path.exists(last_path) or bool(runs)
    return HostStatus(last, runs, errors, configured)


def ingest(db: Session, host: HostStatus) -> int:
    """Перенос запусков в backup_runs. Идемпотентно: run_key уникален, финальный статус не откатывается на running."""
    items: dict[str, dict] = {}
    for r in host.runs + ([host.last] if host.last else []):
        prev = items.get(r["run_key"])
        if prev is None or prev["status"] == "running":
            items[r["run_key"]] = r
    if not items:
        return 0
    have = {b.run_key: b for b in db.scalars(select(BackupRun).where(BackupRun.run_key.in_(list(items)))).all()}
    changed = 0
    for key, r in items.items():
        row = have.get(key)
        if row is not None and row.status != "running":
            continue
        if row is None:
            row = BackupRun(run_key=key)
            db.add(row)
        for k, v in r.items():
            if k != "run_key":
                setattr(row, k, v)
        changed += 1
    if changed:
        db.flush()
    return changed


def next_run(now: Optional[datetime] = None) -> datetime:
    n = (now or datetime.now(timezone.utc)).astimezone(SCHEDULE_TZ)
    t = n.replace(hour=SCHEDULE_AT[0], minute=SCHEDULE_AT[1], second=0, microsecond=0)
    if t <= n:
        t += timedelta(days=1)
    return t.astimezone(timezone.utc)


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


def run_dto(r: Any) -> dict:
    g = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k))
    code = g("error_code") or ""
    return {
        "run_key": g("run_key"), "reason": g("reason"), "status": g("status"),
        "started_at": _iso(g("started_at")), "finished_at": _iso(g("finished_at")),
        "snapshot_id": (g("snapshot_id") or "")[:8] or None,
        "git_commit": (g("git_commit") or "")[:7] or None, "schema_revision": g("schema_revision"),
        "storage_provider": g("storage_provider"), "database_bytes": g("database_bytes"), "files_bytes": g("files_bytes"),
        "files_count": g("files_count"), "manifest_sha256": g("manifest_sha256"),
        "check_status": g("check_status") or "", "checked_at": _iso(g("checked_at")), "retention_status": g("retention_status") or "",
        "error_code": code, "error_text": ERROR_TEXTS.get(code, "") if code else "", "error_summary": sanitize(g("error_summary")),
    }


def compute(db: Session, host: HostStatus, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    last_success = db.scalar(select(BackupRun).where(BackupRun.status == "success").order_by(BackupRun.finished_at.desc().nullslast()).limit(1))
    hl = host.last
    if hl and hl["status"] == "success" and (last_success is None or (hl["finished_at"] and (last_success.finished_at is None or hl["finished_at"] > last_success.finished_at))):
        last_success_dto = run_dto(hl)
        last_success_at = hl["finished_at"]
    else:
        last_success_dto = run_dto(last_success) if last_success else None
        last_success_at = last_success.finished_at if last_success else None
    last_run = run_dto(hl) if hl else None
    if last_run is None:
        lr = db.scalar(select(BackupRun).order_by(BackupRun.started_at.desc().nullslast()).limit(1))
        last_run = run_dto(lr) if lr else None
    configured = host.configured or last_run is not None
    stale = bool(configured and (last_success_at is None or now - last_success_at > STALE_AFTER))
    if not configured:
        state = "not_configured"
    elif last_run and last_run["status"] == "failed":
        state = "failed"
    elif stale:
        state = "stale"
    elif last_run and last_run["status"] == "running":
        state = "running"
    elif last_run and (last_run["retention_status"] == "failed" or last_run["check_status"] == "failed"):
        state = "warning"
    else:
        state = "ok"
    return {
        "state": state, "configured": configured, "stale": stale, "stale_after_hours": int(STALE_AFTER.total_seconds() // 3600),
        "host_status_available": host.last is not None, "status_errors": [sanitize(e, 200) for e in host.errors][:10],
        "last_run": last_run, "last_success": last_success_dto,
        "next_run_at": _iso(next_run(now)), "schedule": "ежедневно в 19:00 по Москве, включая выходные",
        "retention": "90 ежедневных и 12 ежемесячных снимков",
        "timeweb_cutover_allowed": False,   # 03 §7: только после restore gate и явного подтверждения владельца — не вычисляется по статусу
        "manual_command": "sudo systemctl start gsho-backup.service",
        "runbook": "docs/operations/13_Эксплуатационные_инструкции.md",
    }


def _recipients(db: Session) -> list[str]:
    from .access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if perms_for(db, u).get("backup_status", 0) >= 1]


def _notify_once(db: Session, operation: str, key: str, text: str) -> bool:
    """App-only уведомление с защитой от повторов через IntegrationLog(system="backup"). Внешние каналы не вызываются."""
    seen = db.scalars(select(IntegrationLog).where(IntegrationLog.system == "backup", IntegrationLog.operation == operation)).all()
    if any((x.details or {}).get("key") == key for x in seen):
        return False
    for uid in _recipients(db):
        db.add(NotificationLog(user_id=uid, deal_id=None, event="backup", channel="app", status="sent", text=text))
    db.add(IntegrationLog(system="backup", operation=operation, ok=True, details={"key": key}))
    return True


def alert(db: Session, status: dict) -> list[str]:
    sent = []
    lr = status.get("last_run") or {}
    if status["state"] == "failed" and lr.get("run_key"):
        code = lr.get("error_code") or "unknown"
        if _notify_once(db, "failed_alert", lr["run_key"], f"Резервная копия не создана: {ERROR_TEXTS.get(code, ERROR_TEXTS['unknown'])}"):
            sent.append("failed")
    elif lr.get("retention_status") == "failed" and lr.get("run_key"):
        if _notify_once(db, "retention_alert", lr["run_key"], "Резервная копия создана, но очистка старых снимков не прошла — проверьте сервер"):
            sent.append("retention")
    if status["stale"]:
        ls = status.get("last_success") or {}
        key = ls.get("run_key") or "never"
        hours = status["stale_after_hours"]
        if _notify_once(db, "stale_alert", key, f"Нет успешной резервной копии дольше {hours} ч — проверьте сервер"):
            sent.append("stale")
    return sent


def refresh(db: Session, now: Optional[datetime] = None, path: Optional[str] = None) -> dict:
    """Один проход: прочитать хост, перенести историю, посчитать состояние, при необходимости уведомить."""
    host = read_host(path)
    ingest(db, host)
    st = compute(db, host, now)
    alert(db, st)
    db.commit()
    return st
