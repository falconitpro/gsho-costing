"""Журнал скачиваний и выгрузок (docs/operations/04 §7, 12 AUD-01/02).

Единая ASGI-прослойка вместо отметок в каждом обработчике: любой ответ API с заголовком Content-Disposition — это выдача
файла, и событие пишется до того, как браузер получит первый байт. Так новый маршрут с файлом не может «забыть» журнал.

- attachment → download (сохранённый файл) или export (выгрузка данных: маршрут из EXPORT_ROUTES или обработчик пометил
  download_audit.note(kind="export"));
- inline (картинки, PDF, видео «как страница») → download с meta.inline=true: в журнале, но не в порогах «частых скачиваний»;
- перенаправление на короткую ссылку хранилища (S3, note(presigned=True)) — тоже download;
- 401/403/404 на маршрутах выдачи файлов → событие result=denied; содержимое ответа об отказе не меняется.

Fail-closed: если событие не записалось, вместо файла уходит 503 «Журнал недоступен». Для inline-картинок исключение
(документированная политика D-046): файл отдаётся, сбой пишется в журнал приложения уровнем ERROR — событие не теряется
молча, но просмотр карточки каталога не ломается из-за телеметрии.

В событие не попадают: имя файла (бывает с ФИО/паспортом), путь хранения, подписанная ссылка, cookie, заголовки запроса,
содержимое. Только шаблон маршрута, ID объектов из пути, формат, размер, число строк и фильтры по белому списку.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from typing import Any, Optional

import contextvars

import anyio

log = logging.getLogger("gsho.security")

# Выгрузки данных (списки, отчёты, справочники, комплекты) — в отличие от выдачи одного сохранённого файла
EXPORT_ROUTES = {
    "/api/finance/export", "/api/admin/hs/export", "/api/parts/stock/balances/xlsx", "/api/parts/po/{pid}/xlsx",
    "/api/parts/rfq/{rfq_id}/xlsx", "/api/parts/claims/{cid}/xlsx", "/api/legal/packages/{pid}/zip",
    "/api/deals/{deal_id}/positions/{pos_id}/zip/for-hours.xlsx", "/api/deals/{deal_id}/positions/{pos_id}/zip",
}
_FILE_MARKERS = re.compile(r"file_response\(|FileResponse\(|StreamingResponse\(|Content-Disposition|RedirectResponse\(")
_FILE_PATH = re.compile(r"(export|download|\.xlsx|\.docx|/xlsx|/docx|/file|/files|/zip|/photo|/print|/preview|/card|test-render|/images/)", re.I)
_SAFE_FILTER_KEYS = {"kind", "as_of", "direction", "currency", "status", "source_type", "date_from", "date_to", "group_by", "horizon_days",
                     "queue", "reconciliation", "include_inactive", "legal_entity_id", "contract_id", "deal_id"}
_DENIED = {401, 403, 404}
_EXT = re.compile(r"\.([A-Za-z0-9]{1,8})\"?\s*$")

_file_routes: Optional[set[str]] = None
_CURRENT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("download_audit", default=None)


def note(**fields) -> None:
    """Обработчик дополняет событие: kind ("export"), entity_type/entity_id, deal_id, row_count, filters, presigned, inline.
    Работает и в синхронных обработчиках (контекст копируется в поток)."""
    cur = _CURRENT.get()
    if cur is not None:
        cur.update({k: v for k, v in fields.items() if v is not None})


def walk_routes(routes):
    """Маршруты приложения, включая вложенные роутеры (FastAPI ≥0.160 оборачивает include_router)."""
    for r in routes:
        if hasattr(r, "original_router"):
            yield from walk_routes(r.original_router.routes)
        else:
            yield r


def file_routes(app) -> set[str]:
    """Шаблоны маршрутов выдачи файлов: обработчик возвращает файл/поток/перенаправление или путь похож на выдачу файла.
    Нужны только для журналирования отказов (успех ловится по заголовку на любом маршруте)."""
    global _file_routes
    if _file_routes is None:
        out = set(EXPORT_ROUTES)
        for r in walk_routes(getattr(app, "routes", [])):
            path, ep = getattr(r, "path", ""), getattr(r, "endpoint", None)
            if not path.startswith("/api/") or ep is None or "GET" not in (getattr(r, "methods", None) or set()):
                continue
            try:
                src = inspect.getsource(ep)
            except (OSError, TypeError):   # pragma: no cover
                src = ""
            if _FILE_MARKERS.search(src) or _FILE_PATH.search(path):
                out.add(path)
        _file_routes = out
    return _file_routes


def _headers(message) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in message.get("headers", [])}


def _format(headers: dict[str, str]) -> str:
    cd = headers.get("content-disposition", "")
    m = _EXT.search(cd.split(";")[-1]) if cd else None
    if m:
        return m.group(1).lower()[:16]
    ct = headers.get("content-type", "").split(";")[0].strip()
    return {"application/pdf": "pdf", "text/csv": "csv", "application/zip": "zip",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}.get(ct, ct.split("/")[-1][:16])


def _entity(template: str, params: dict[str, Any]) -> tuple[str, str]:
    """Тип и ID объекта по пути: последний параметр маршрута. Путь хранения (/api/files/...) — только как хэш."""
    if "path" in params:
        return "file", "sha256:" + hashlib.sha256(str(params["path"]).encode()).hexdigest()[:16]
    keys = [k for k in params if k != "deal_id"]
    if not keys:
        return (template.rstrip("/").split("/")[-1][:32], "")
    k = keys[-1]
    return k[:32], "/".join(str(params[x]) for x in keys)[:128]


class DownloadAuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})
        state = scope["state"]
        state["audit"] = {}
        token = _CURRENT.set(state["audit"])
        blocked = False

        async def send_wrapper(message):
            nonlocal blocked
            if blocked:
                return
            if message["type"] == "http.response.start":
                try:
                    ok = await self._record(scope, state, message)
                except Exception:   # pragma: no cover — защитный контур, сам _record ловит ошибки
                    ok = False
                if not ok:
                    blocked = True
                    body = json.dumps({"detail": {"code": "audit_unavailable",
                                                  "message": "Журнал скачиваний временно недоступен — файл не выдан. Повторите через минуту."}},
                                      ensure_ascii=False).encode()
                    await send({"type": "http.response.start", "status": 503,
                                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                                            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    return
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _CURRENT.reset(token)

    async def _record(self, scope, state: dict, message) -> bool:
        status = int(message.get("status", 200))
        headers = _headers(message)
        audit: dict = dict(state.get("audit") or {})
        route = scope.get("route")
        template = getattr(route, "path", "") or scope.get("path", "")
        cd = headers.get("content-disposition", "")
        presigned = bool(audit.get("presigned")) and 300 <= status < 400
        if cd and 200 <= status < 300:
            inline = cd.strip().lower().startswith("inline")
            result = "success"
        elif presigned:
            inline, result = bool(audit.get("inline")), "success"
        elif status in _DENIED and scope.get("method") == "GET" and template in file_routes(scope.get("app")):
            inline, result = False, "denied"
        else:
            return True
        kind = audit.get("kind") or ("export" if template in EXPORT_ROUTES else "download")
        params = dict(scope.get("path_params") or {})
        etype, eid = _entity(template, params)
        meta = {"route": template, "status": status}
        if inline:
            meta["inline"] = True
        if presigned:
            meta["delivery"] = "presigned_300s"
        filters = audit.get("filters") or {}
        if filters:
            meta["filters"] = {k: v for k, v in filters.items() if k in _SAFE_FILTER_KEYS}
            if filters.get("search"):
                meta["filters"]["search"] = True   # сам текст поиска не храним
        try:
            size = int(headers["content-length"]) if "content-length" in headers else None
        except ValueError:
            size = None
        fields = dict(event_type=kind, action=("view" if inline else kind) if result == "success" else kind,
                      result=result, actor_id=state.get("audit_user_id"), subject_id=state.get("audit_user_id"),
                      session_id=state.get("audit_sid"), entity_type=str(audit.get("entity_type") or etype)[:32],
                      entity_id=str(audit.get("entity_id") or eid)[:128], deal_id=audit.get("deal_id") or params.get("deal_id"),
                      fmt="" if result != "success" else _format(headers), row_count=audit.get("row_count"),
                      byte_size=size if result == "success" else None, meta=meta)
        try:
            await anyio.to_thread.run_sync(_write, scope, fields)
            return True
        except Exception as e:
            if inline or result == "denied":
                # D-046: картинки «как страница» и записи об отказе не блокируют ответ; сбой — в журнал приложения
                log.error("download audit write failed (%s %s %s): %s", result, template, status, type(e).__name__)
                return True
            log.error("download audit write failed, file blocked (%s %s): %s", template, status, type(e).__name__)
            return False


def _write(scope, fields: dict) -> None:
    from starlette.requests import Request

    from ..db import SessionLocal
    from ..services import security_events as sev
    deal_id = fields.pop("deal_id", None)
    with SessionLocal() as db:
        if deal_id:
            from ..models import Deal
            if db.get(Deal, str(deal_id)) is None:   # ID из пути может быть мусором — колонка без FK, но храним только реальные
                deal_id = None
        sev.record(db, request=Request(scope), deal_id=deal_id, **fields)
        db.commit()


def reset_cache() -> None:   # для тестов: маршруты, добавленные после старта
    global _file_routes
    _file_routes = None


__all__ = ["DownloadAuditMiddleware", "EXPORT_ROUTES", "file_routes", "note", "reset_cache", "walk_routes"]
