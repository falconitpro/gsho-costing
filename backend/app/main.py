"""Точка входа FastAPI. Запуск: uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import admin, auth, catalog, deal_trash, deals, documents, execution, images_options, logistics, parts_catalog, parts_po, parts_requests, parts_rfq, parts_inventory, parts_setup, parts_stock, pricing, references, service, tasks_files_planfact, team, tech_params
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
        try:   # просроченные задачи — уведомления исполнителю и КД (раз в час, по одной на задачу)
            from .services.deal_service import check_overdue_tasks
            with SessionLocal() as db:
                if check_overdue_tasks(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("overdue check failed: %s", e)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_dev:
        init_db()
    task = asyncio.create_task(_daily_jobs())
    yield
    task.cancel()


app = FastAPI(title="Модуль расчёта себестоимости и маржинальности поставок ГШО", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, deals.router, logistics.router, service.router, pricing.router, references.router, admin.router, tasks_files_planfact.router,
          execution.router, tech_params.router, catalog.router, images_options.router, team.router, documents.router, deal_trash.router, parts_setup.router, parts_catalog.router, parts_requests.router, parts_rfq.router, parts_po.router, parts_stock.router, parts_inventory.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):   # pragma: no cover
    log.exception("unhandled: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


@app.get("/api/health")
def health():
    return {"status": "ok", "env": settings.env, "auth": settings.auth_provider, "storage": settings.storage_provider}
