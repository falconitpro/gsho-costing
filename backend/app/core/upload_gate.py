"""Единый вход для загрузок (docs/operations/04 §8.1, 12 FILE-01…03, FILE-07).

ASGI-прослойка (UploadGateMiddleware): если запрос к API — multipart/form-data с файлами, до вызова обработчика
1) проверяется, кто загружает (тот же get_current_user; без входа — обработчик сам ответит 401, файл не проверяется);
2) архивы и офисные файлы проверяются на zip-бомбы и вложенность — до любого разбора импортом;
3) при FILE_SCAN=clamav каждый файл проверяется clamd. Пока идёт проверка, файл лежит только во временном файле
   multipart (вне хранилища, имя непредсказуемо) — это и есть карантин. Обработчик (сохранение, разбор импорта)
   вызывается только если все файлы чистые.

Заражён → 422, файл не сохраняется (образец не храним, только хэш и имя сигнатуры), событие и критическое предупреждение.
Антивирус недоступен, таймаут, превышены лимиты → 503 «проверка временно недоступна», файл не принят (fail-closed).
Новый маршрут с загрузкой проходит через тот же вход без доработки.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional

import anyio
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

log = logging.getLogger("gsho.security")

# sha256 → id проверки (clean) или None (принят без проверки при FILE_SCAN=off) — для статуса новых attachments
_UPLOADS: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("upload_scans", default=None)


def current_uploads() -> Optional[dict]:
    return _UPLOADS.get()


def _safe_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    return "".join(ch for ch in base if ch.isprintable())[:255]


def _digest(f: UploadFile) -> tuple[str, int]:
    fh = f.file
    fh.seek(0)
    h, n = hashlib.sha256(), 0
    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
        h.update(chunk)
        n += len(chunk)
    fh.seek(0)
    return h.hexdigest(), n


def _reject(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}}, headers={"Cache-Control": "no-store"})


async def _actor(request: Request):
    from ..db import SessionLocal
    from .auth import get_current_user
    from .config import get_settings
    with SessionLocal() as db:
        try:
            return await get_current_user(request, Response(), db, get_settings())
        except HTTPException:
            return None


def _scan_one(f: UploadFile, sha: str, size: int, route: str, actor, request: Request) -> tuple[str, Optional[str], str]:
    """Возвращает (status, scan_id, signature|error_code). Пишет attachment_scans и событие."""
    from ..db import SessionLocal
    from ..models import AttachmentScan
    from ..services import file_scanner
    from ..services import security_events as sev
    from .config import get_settings
    s = get_settings()
    name = _safe_name(f.filename or "")
    with SessionLocal() as db:
        row = AttachmentScan(source="upload", route=route[:160], file_name=name, status="pending", sha256=sha, size=size,
                             uploaded_by=actor.id if actor else None)
        db.add(row)
        db.commit()
        f.file.seek(0)
        res = file_scanner.scan_stream(f.file, s.clamd_host, s.clamd_port, timeout=float(s.clamd_timeout))
        f.file.seek(0)
        row.status, row.engine_version, row.signature_version = res.status, res.engine_version, res.signature_version
        row.detected_name, row.last_error_code = res.signature, res.error_code
        row.scanned_at = datetime.now(timezone.utc)
        ext = os.path.splitext(name)[1].lower().lstrip(".")[:16]
        if res.status == "infected":
            sev.record(db, "file", "infected", result="denied", actor_id=actor.id if actor else None, subject_id=actor.id if actor else None,
                       request=request, session_id=actor.sid if actor else None, entity_type="attachment_scan", entity_id=row.id, fmt=ext,
                       byte_size=size, meta={"route": route, "threat": res.signature, "sha256_prefix": sha[:16]})
        elif res.status == "error":
            sev.record(db, "file", "scan_error", result="error", actor_id=actor.id if actor else None, subject_id=None, request=request,
                       session_id=actor.sid if actor else None, entity_type="attachment_scan", entity_id=row.id, fmt=ext, byte_size=size,
                       meta={"route": route, "error": res.error_code})
        db.commit()
        return res.status, row.id, res.signature or res.error_code


async def _gate(request: Request, path: str) -> Optional[Response]:
    from ..services import file_scanner
    from .config import get_settings
    form = await request.form(max_files=1000, max_fields=1000)
    try:
        files = [v for _, v in form.multi_items() if isinstance(v, UploadFile)]
        if not files:
            return None
        actor = await _actor(request)
        if actor is None:
            return None      # без входа обработчик сам ответит 401/403; файл не проверяем и не сохраняем
        state = _UPLOADS.get()
        for f in files:
            why = await anyio.to_thread.run_sync(file_scanner.zip_guard, f.file, f.filename or "")
            if why:
                return _reject(422, "file_rejected", f"Файл «{_safe_name(f.filename or '')}» не принят: {why}.")
            sha, size = await anyio.to_thread.run_sync(_digest, f)
            if get_settings().file_scan != "clamav":
                state[sha] = None
                continue
            status, scan_id, info = await anyio.to_thread.run_sync(_scan_one, f, sha, size, path, actor, request)
            if status == "infected" and info.startswith("Heuristics.Limits"):
                return _reject(422, "file_rejected", f"Файл «{_safe_name(f.filename or '')}» не принят: превышены ограничения проверки "
                                                      "(слишком большой архив, много вложенных файлов или глубокая вложенность).")
            if status == "infected":
                return _reject(422, "file_infected", f"Файл «{_safe_name(f.filename or '')}» не принят: антивирус нашёл угрозу ({info}). "
                                                      "Не открывайте исходный файл и сообщите администратору.")
            if status != "clean":
                log.error("file scan unavailable: %s %s", path, info)
                return _reject(503, "scan_unavailable", "Проверка файлов антивирусом временно недоступна — файл не принят. Повторите через несколько минут.")
            state[sha] = scan_id
        return None
    finally:
        await form.close()


class UploadGateMiddleware:
    """ASGI: тело multipart-запроса к API сохраняется во временный файл (вне хранилища), файлы из него проверяются,
    затем тело заново отдаётся приложению. Не зависит от того, как FastAPI устроит маршруты; новый маршрут с загрузкой
    проходит проверку без доработки. Ставится внутри проверки размера (413 раньше, чем тело читается)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        token = _UPLOADS.set({})
        try:
            ctype = ""
            for k, v in scope.get("headers") or []:
                if k == b"content-type":
                    ctype = v.decode("latin-1")
            if (scope.get("method") not in ("POST", "PUT", "PATCH") or not ctype.startswith("multipart/form-data")
                    or not scope.get("path", "").startswith("/api/")):
                return await self.app(scope, receive, send)
            spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, prefix="gsho-upload-")
            try:
                more = True
                while more:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        return
                    spool.write(msg.get("body", b""))
                    more = msg.get("more_body", False)

                def replay():
                    spool.seek(0)
                    done = False

                    async def _receive():
                        nonlocal done
                        if done:
                            return await receive()     # после тела — как обычно (ожидание отключения клиента)
                        chunk = spool.read(256 * 1024)
                        if len(chunk) < 256 * 1024:
                            done = True
                        return {"type": "http.request", "body": chunk, "more_body": not done}
                    return _receive

                # в журнал — путь без ID (шаблон): /api/deals/{id}/documents
                tpl = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?<=/)\d+(?=/|$)", "{id}", scope.get("path", ""))
                resp = await _gate(Request(scope, replay()), tpl)
                if resp is not None:
                    return await resp(scope, receive, send)
                await self.app(scope, replay(), send)
            finally:
                spool.close()
        finally:
            _UPLOADS.reset(token)


def _attachment_listeners() -> None:
    """Статус проверки новых attachments: файл из проверенной загрузки — clean, иначе legacy_unscanned (D-049)."""
    from sqlalchemy import event, update

    from ..models import Attachment, AttachmentScan

    @event.listens_for(Attachment, "before_insert")
    def _before(mapper, connection, target):   # noqa: ANN001
        st = _UPLOADS.get()
        if st and target.sha256 in st and st[target.sha256]:
            target.scan_status = "clean"
            target.scanned_at = datetime.now(timezone.utc)
        elif not target.scan_status:
            target.scan_status = "legacy_unscanned"

    @event.listens_for(Attachment, "after_insert")
    def _after(mapper, connection, target):   # noqa: ANN001
        st = _UPLOADS.get()
        scan_id = st.get(target.sha256) if st else None
        if scan_id:
            connection.execute(update(AttachmentScan).where(AttachmentScan.id == scan_id, AttachmentScan.attachment_id.is_(None))
                               .values(attachment_id=target.id))


_attachment_listeners()
