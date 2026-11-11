"""Точка входа FastAPI. Запуск: uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import acceptance, admin, finance, auth, catalog, model_docs, notifications, operations, security, summary, legal_profiles, legal_templates, legal_packages, legal_poa, legal_contracts, legal_claims, workplace, deal_trash, deals, documents, execution, images_options, logistics, parts_catalog, parts_fleet, parts_po, parts_requests, parts_rfq, parts_inventory, parts_setup, parts_reports, parts_stock, parts_warranty, pricing, references, service, tasks_files_planfact, team, tech_params
from .core.config import get_settings
from .db import init_db

log = logging.getLogger("gsho")
settings = get_settings()


async def _daily_jobs() -> None:
    """Фоновые задания: курс ЦБ ежедневно, проверка просрочек раз в час.

    В проде — отдельный воркер очереди (arq/Celery) и cron; здесь — простой цикл в процессе API.
    """
    from sqlalchemy import select
    from . import adapters
    from .db import SessionLocal
    from .models import FxRate, IntegrationLog
    while True:
        try:
            with SessionLocal() as db:
                today = date.today()
                have_today = set(db.scalars(select(FxRate.currency).where(FxRate.on == today, FxRate.source == "cbr")).all())
                from .adapters.fx_source import SUPPORTED as FX_CODES
                if not set(FX_CODES) <= have_today:
                    try:
                        rates = await asyncio.to_thread(adapters.fx_source().daily, today)
                        for cur, rate in rates.items():
                            if cur != "RUB" and cur not in have_today:
                                db.add(FxRate(on=today, currency=cur, rate=rate, source="cbr"))
                        db.add(IntegrationLog(system="cbr", operation="daily", ok=True, details={"on": str(today)}))
                    except Exception as e:
                        db.add(IntegrationLog(system="cbr", operation="daily", ok=False, details={"error": str(e)[:300]}))
                    db.commit()
                # история курсов ЦБ для тренда и сезонности прогноза: если её почти нет — догружаем за 5 лет (один раз)
                import sys
                from datetime import timedelta
                for cur in (() if "pytest" in sys.modules else FX_CODES):   # в тестах — без сети
                    n = len(db.scalars(select(FxRate.id).where(FxRate.currency == cur, FxRate.source == "cbr",
                                                              FxRate.on >= today - timedelta(days=365 * 5))).all())
                    if n >= 200:
                        continue
                    try:
                        pts = await asyncio.to_thread(adapters.fx_source().history, cur, today - timedelta(days=365 * 5), today)
                        have = {r for r in db.scalars(select(FxRate.on).where(FxRate.currency == cur, FxRate.source == "cbr")).all()}
                        for d, r in pts:
                            if d not in have:
                                db.add(FxRate(on=d, currency=cur, rate=r, source="cbr"))
                        db.add(IntegrationLog(system="cbr", operation="history", ok=True, details={"currency": cur, "points": len(pts)}))
                    except Exception as e:
                        db.add(IntegrationLog(system="cbr", operation="history", ok=False, details={"currency": cur, "error": str(e)[:300]}))
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("daily job failed: %s", e)
        try:   # запчасти: нормативные сроки (КП клиенту, ответ и подтверждение поставщика, просроченная поставка)
            from .services.parts_sla import check as check_parts_sla
            with SessionLocal() as db:
                if check_parts_sla(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("parts SLA check failed: %s", e)
        try:   # запчасти: напоминания менеджерам о ТО машин клиентов
            from .api.parts_fleet import check_reminders
            with SessionLocal() as db:
                if check_reminders(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("parts TO reminders failed: %s", e)
        try:   # просроченные задачи — уведомления исполнителю и КД (раз в час, по одной на задачу)
            from .services.deal_service import check_overdue_tasks
            with SessionLocal() as db:
                if check_overdue_tasks(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("overdue check failed: %s", e)
        try:   # доверенности компаний группы: напоминания за 30 и 7 дней до истечения, перевод в «срок истёк»
            from .services.legal_poa import check_expiring
            with SessionLocal() as db:
                if check_expiring(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("poa expiry check failed: %s", e)
        try:   # договоры: напоминания за 30 и 7 дней до окончания, автопролонгация, истечение срока
            from .services.legal_contracts import check_expiring as check_contracts
            with SessionLocal() as db:
                if check_contracts(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("contracts expiry check failed: %s", e)
        try:   # претензии: напоминание за 3 дня, уведомление в 1-й день просрочки, черновик претензии на 5-й день, истёк срок ответа
            from .services.legal_claims import ladder
            with SessionLocal() as db:
                if ladder(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("claims ladder failed: %s", e)
        try:   # пересмотр прав: напоминания в системе за 14/7/1 день и о просрочке (docs/operations/04 §6)
            from .services.access_review import remind as remind_reviews
            with SessionLocal() as db:
                remind_reviews(db)
        except Exception as e:   # pragma: no cover
            log.warning("access review reminders failed: %s", e)
        try:   # резервные копии: перенос статуса с хоста, «нет копии дольше 26 ч», уведомления в системе (docs/operations/03 §5)
            from .services.backup_status import refresh as refresh_backups
            with SessionLocal() as db:
                refresh_backups(db)
        except Exception as e:   # pragma: no cover
            log.warning("backup status refresh failed: %s", e)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_dev:
        init_db()
    task = asyncio.create_task(_daily_jobs())
    yield
    task.cancel()


app = FastAPI(title="Модуль расчёта себестоимости и маржинальности поставок ГШО", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.is_dev else None, openapi_url="/api/openapi.json" if settings.is_dev else None)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


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
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/api/openapi"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

for r in (auth.router, deals.router, logistics.router, service.router, pricing.router, references.router, admin.router, tasks_files_planfact.router,
          execution.router, tech_params.router, catalog.router, images_options.router, team.router, documents.router, deal_trash.router, parts_setup.router, parts_catalog.router, parts_requests.router, parts_rfq.router, parts_po.router, parts_stock.router, parts_inventory.router, parts_fleet.router, parts_warranty.router, parts_reports.router, model_docs.router, acceptance.router, notifications.router, summary.router, legal_profiles.router, legal_templates.router, legal_packages.router, legal_poa.router, legal_contracts.router, legal_claims.router, workplace.router, finance.router, finance.deal_router, operations.router, security.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):   # pragma: no cover
    log.exception("unhandled: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/api/health")
def health():
    return {"status": "ok", "env": settings.env, "auth": settings.auth_provider, "storage": settings.storage_provider}
