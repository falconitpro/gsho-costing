"""Сбор ошибок системы (D-117): о сбоях узнаём раньше пользователей.

Источники: необработанная ошибка сервера (обработчик исключений в main.py) и ошибка страницы в браузере
(POST /api/client-errors из frontend/src/errorReporter.ts). Одна запись на «отпечаток» — где + тип + место в коде;
повторы увеличивают счётчик. Уведомление «в системе» получают держатели права «Журнал безопасности» (администратор, ЗГД) —
при первой ошибке с новым отпечатком и при повторе после «Исправлено».

Что не сохраняется: тела запросов, параметры адресов, содержимое файлов, значения переменных. Сообщение ошибки
обрезается и очищается от адресов почты, телефонов и длинных чисел.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AppError, NotificationLog, User

log = logging.getLogger("gsho.errors")

MAX_MESSAGE = 500
MAX_TRACE = 4000
RELEASE = (os.environ.get("GSHO_RELEASE") or "")[:40]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s()\-]{8,}\d")
_LONGNUM = re.compile(r"\b\d{6,}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def clean(text: str, limit: int = MAX_MESSAGE) -> str:
    """Без персональных данных: почта, телефоны, длинные номера и идентификаторы — заменяются."""
    t = _EMAIL.sub("<почта>", text or "")
    t = _UUID.sub("<id>", t)
    t = _PHONE.sub("<телефон>", t)
    t = _LONGNUM.sub("<число>", t)
    return t.strip()[:limit]


def path_template(path: str) -> str:
    """Путь без идентификаторов и параметров — чтобы одна и та же ошибка на разных сделках была одной записью."""
    p = (path or "").split("?")[0].split("#")[0]
    p = _UUID.sub("{id}", p)
    p = re.sub(r"/\d+(?=/|$)", "/{n}", p)
    return p[:300]


_WRAPPERS = ("app/core/download_audit.py", "app/core/upload_gate.py", "app/main.py")   # обёртки каждого запроса — не место ошибки


def _app_frames(exc: BaseException) -> list[str]:
    """Строки трассировки только из кода приложения: файл, строка, функция — без значений переменных."""
    out = []
    for fr in traceback.extract_tb(exc.__traceback__):
        f = fr.filename.replace("\\", "/")
        if "/app/" not in f or "site-packages" in f or any(m in f for m in _WRAPPERS):
            continue
        out.append(f"{f[f.rindex('/app/') + 1:]}:{fr.lineno} in {fr.name}")
    return out[-12:]


def _fingerprint(source: str, kind: str, where: str, top: str) -> str:
    return hashlib.sha256(f"{source}|{kind}|{where}|{top}".encode()).hexdigest()[:40]


def _holders(db: Session) -> list[str]:
    from .access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if perms_for(db, u).get("security_audit", 0) >= 1]


def _notify(db: Session, e: AppError, again: bool) -> None:
    text = (f"{'Ошибка повторилась после «Исправлено»' if again else 'Новая ошибка системы'} ({'сервер' if e.source == 'server' else 'страница'}): "
            f"{e.kind} — {e.where or 'без адреса'}. Подробности — «Настройки → Ошибки системы».")
    for uid in _holders(db):
        db.add(NotificationLog(user_id=uid, deal_id=None, event="app_error", channel="app", status="sent", text=text[:500],
                               link="/settings?s=app_errors"))


def record(db: Session, *, source: str, kind: str, message: str, where: str, trace: list[str], user_id: Optional[str],
           request_id: str = "", release: str = "") -> Optional[AppError]:
    """Записать ошибку (новая — уведомить; повтор — счётчик; повтор после «Исправлено» — снова открыть и уведомить)."""
    kind = (kind or "Error")[:120]
    where = path_template(where)
    top = trace[-1] if trace else clean(message, 120)
    fp = _fingerprint(source, kind, where, top)
    now = datetime.now(timezone.utc)
    e = db.scalar(select(AppError).where(AppError.fingerprint == fp).with_for_update())
    if e is None:
        e = AppError(fingerprint=fp, source=source, kind=kind, message=clean(message), where=where, trace="\n".join(trace)[:MAX_TRACE],
                     count=1, users=[user_id] if user_id else [], last_request_id=request_id[:64], release=(release or RELEASE)[:40],
                     first_seen=now, last_seen=now)
        db.add(e)
        try:
            db.flush()
        except IntegrityError:            # одновременно записала другая копия процесса — посчитать как повтор
            db.rollback()
            return record(db, source=source, kind=kind, message=message, where=where, trace=trace, user_id=user_id, request_id=request_id, release=release)
        _notify(db, e, again=False)
        return e
    again = e.resolved_at is not None
    e.count += 1
    e.last_seen = now
    e.message = clean(message)
    e.last_request_id = request_id[:64]
    if release or RELEASE:
        e.release = (release or RELEASE)[:40]
    if user_id and user_id not in (e.users or []):
        e.users = ([*e.users, user_id] if e.users else [user_id])[-10:]
    if again:
        e.resolved_at = None
        e.resolved_by = None
        _notify(db, e, again=True)
    return e


def record_exception(exc: BaseException, *, method: str, path: str, user_id: Optional[str], request_id: str) -> None:
    """Из обработчика исключений сервера: своя сессия, любые сбои записи только в лог — ответ пользователю не меняется."""
    from ..db import SessionLocal
    try:
        with SessionLocal() as db:
            record(db, source="server", kind=type(exc).__name__, message=str(exc), where=f"{method} {path}", trace=_app_frames(exc),
                   user_id=user_id, request_id=request_id)
            db.commit()
    except Exception:  # pragma: no cover — сбор ошибок не должен ронять обработку ошибки
        log.exception("app_errors: не удалось записать ошибку")


def record_background(job: str, exc: BaseException) -> None:
    """Сбой фонового задания (курсы ЦБ, напоминания, проверки по часам, разовые исправления данных после обновления):
    раньше — только строка в журнале контейнера, теперь — запись в «Ошибки системы» и уведомление."""
    from ..db import SessionLocal
    try:
        with SessionLocal() as db:
            record(db, source="server", kind=type(exc).__name__, message=str(exc), where=f"фоновое задание: {job}", trace=_app_frames(exc),
                   user_id=None)
            db.commit()
    except Exception:  # pragma: no cover
        log.exception("app_errors: не удалось записать сбой фонового задания")


# --- приём из браузера: не больше 30 сообщений с одного адреса за 10 минут
_rate: dict[str, list[datetime]] = {}
RATE_N, RATE_WINDOW = 30, timedelta(minutes=10)


def allow(key: str) -> bool:
    now = datetime.now(timezone.utc)
    q = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW]
    if len(q) >= RATE_N:
        _rate[key] = q
        return False
    q.append(now)
    _rate[key] = q
    if len(_rate) > 5000:
        _rate.clear()
    return True


def browser_frames(stack: str) -> list[str]:
    """Стек из браузера: только файлы нашего приложения (assets/…, src/…), без адресов с параметрами."""
    out = []
    for line in (stack or "").splitlines()[:30]:
        m = re.search(r"((?:assets|src)/[\w./-]+\.(?:js|tsx?|jsx))(?:\?[^:)]*)?:(\d+)(?::\d+)?", line)
        if m:
            fn = re.match(r"\s*at\s+([\w$.<>]+)\s", line)
            out.append(f"{m.group(1)}:{m.group(2)}" + (f" in {fn.group(1)}" if fn else ""))
    return list(reversed(out[:12]))
