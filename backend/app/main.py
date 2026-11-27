"""Точка входа FastAPI. Запуск: uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import changes, service_desk, client_contacts, client_sites, acceptance, admin, assistant, briefing, competitors, finance, logistics_workspace, negotiation, auth, catalog, model_docs, notifications, operations, security, summary, support, legal_profiles, legal_templates, legal_packages, legal_poa, legal_contracts, legal_claims, workplace, deal_trash, deals, documents, execution, images_options, logistics, parts_catalog, parts_fleet, parts_po, parts_requests, parts_rfq, parts_inventory, parts_setup, parts_reports, parts_stock, parts_warranty, pricing, references, service, tasks_files_planfact, team, tech_params, public_quote, deal_feed, office, management, hr
from .core.config import get_settings
from .db import init_db

log = logging.getLogger("gsho")
settings = get_settings()


def _bg_error(job: str, exc: BaseException) -> None:
    """D-117: сбой фонового задания — в «Ошибки системы» (администратор и ЗГД получают уведомление)."""
    from .services.app_errors import record_background
    record_background(job, exc)


async def _jobs_loop() -> None:
    """D-143: фоновые задания выполняет отдельный процесс (app/worker.py). Пока он не отмечается (старый сервер,
    worker не запущен) — цикл выполняет процесс API; блокировка в базе не даёт выполнить цикл дважды."""
    from . import jobs
    while True:
        try:
            if not await asyncio.to_thread(jobs.worker_alive):
                await asyncio.to_thread(jobs.run_cycle, "api")
        except Exception as e:   # pragma: no cover
            log.warning("jobs loop failed: %s", e)
            _bg_error("jobs loop", e)
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_dev:
        init_db()
    # класс данных и опасные сочетания настроек (04 §10): при нарушении backend не стартует
    from .core import environment
    from .db import SessionLocal
    with SessionLocal() as db:
        environment.check(db, settings)
    # recovery-копия для drill: никаких фоновых заданий (напоминания, уведомления, импорт курсов)
    # в тестах фоновый цикл не запускается: его шаги проверяются напрямую, а запуск при каждом TestClient(app)
    # гонялся с тестами (напоминания о доверенностях успевали уйти до проверки)
    import sys
    task = asyncio.create_task(_jobs_loop()) if settings.env != "recovery" and "pytest" not in sys.modules else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Модуль расчёта себестоимости и маржинальности поставок ГШО", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.is_dev else None, openapi_url="/api/openapi.json" if settings.is_dev else None)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# Проверка загрузок (04 §8): добавлена раньше лимита размера — значит, работает внутри него (413 до чтения тела)
from .core.upload_gate import UploadGateMiddleware  # noqa: E402
app.add_middleware(UploadGateMiddleware)


@app.middleware("http")
async def security_headers_and_upload_limit(request: Request, call_next):
    """Лимит размера загрузок по Content-Length (фото и видео приёмки — крупнее, остальное — документы)
    и заголовки безопасности на каждый ответ API."""
    from .core.files import LIMIT_DOCUMENT, LIMIT_MEDIA, MB
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        path = request.url.path
        limit = LIMIT_MEDIA if ("/acceptance/" in path and "/files" in path) or "/photos" in path or "/photo" in path else LIMIT_DOCUMENT
        if length > limit:
            return JSONResponse(status_code=413, content={"detail": f"Файл больше допустимого размера ({limit // MB} МБ)"})
    from .services.security_events import request_id
    rid = request_id(request)       # один ID на запрос: в журнале безопасности и в ответе (для «Сообщить в поддержку»)
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", rid)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/api/openapi"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# Журнал скачиваний и выгрузок (04 §7): снаружи всех обработчиков, ловит любой ответ с файлом; без записи в журнал файл не выдаётся
from .core.download_audit import DownloadAuditMiddleware  # noqa: E402
app.add_middleware(DownloadAuditMiddleware)

for r in (changes.router, service_desk.router, client_contacts.router, client_sites.router, auth.router, deals.router, logistics.router, service.router, pricing.router, references.router, admin.router, tasks_files_planfact.router,
          execution.router, tech_params.router, catalog.router, images_options.router, team.router, documents.router, deal_trash.router, parts_setup.router, parts_catalog.router, parts_requests.router, parts_rfq.router, parts_po.router, parts_stock.router, parts_inventory.router, parts_fleet.router, parts_warranty.router, parts_reports.router, model_docs.router, acceptance.router, notifications.router, summary.router, legal_profiles.router, legal_templates.router, legal_packages.router, legal_poa.router, legal_contracts.router, legal_claims.router, workplace.router, finance.router, finance.deal_router, operations.router, security.router, support.router, briefing.router, assistant.router, competitors.router, logistics_workspace.router, logistics_workspace.refs, negotiation.router, public_quote.router, deal_feed.router, office.router, management.router, hr.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled: %s", exc)
    # D-117: ошибка попадает в «Ошибки системы» и приходит администратору / ЗГД; номер запроса — в ответе (для поддержки)
    from .services import app_errors
    from .services.security_events import request_id
    rid = request_id(request)
    await asyncio.to_thread(app_errors.record_exception, exc, method=request.method, path=request.url.path,
                            user_id=getattr(request.state, "audit_user_id", None), request_id=rid)
    return JSONResponse(status_code=500, content={"detail": f"Внутренняя ошибка сервера. Номер ошибки: {rid[:8]} — администратор уже получил уведомление."},
                        headers={"X-Request-ID": rid})


@app.get("/api/health")
def health():
    out = {"status": "ok", "env": settings.env, "auth": settings.auth_provider, "storage": settings.storage_provider, "file_scan": settings.file_scan}
    try:   # D-143: фоновые задания — кто выполняет и не застряли ли (без подробностей: адрес открыт без входа)
        from . import jobs
        st = jobs.status()
        out["jobs"] = {"runner": "worker" if st["worker_alive"] else "api", "stale": st["stale"], "failing": len(st["failing"])}
    except Exception:   # noqa: BLE001 — таблицы ещё нет (до миграции 044)
        out["jobs"] = {"runner": "unknown"}
    return out

