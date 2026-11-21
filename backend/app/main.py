"""Точка входа FastAPI. Запуск: uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import acceptance, admin, assistant, briefing, competitors, finance, logistics_workspace, negotiation, auth, catalog, model_docs, notifications, operations, security, summary, support, legal_profiles, legal_templates, legal_packages, legal_poa, legal_contracts, legal_claims, workplace, deal_trash, deals, documents, execution, images_options, logistics, parts_catalog, parts_fleet, parts_po, parts_requests, parts_rfq, parts_inventory, parts_setup, parts_reports, parts_stock, parts_warranty, pricing, references, service, tasks_files_planfact, team, tech_params, public_quote, deal_feed, office
from .core.config import get_settings
from .db import init_db

log = logging.getLogger("gsho")
settings = get_settings()


SCANNER_START_GRACE_S = 600   # после старта антивирус 1–2 мин загружает базы; раньше проверять — ложная тревога (D-068)


async def _scanner_check(delay: float) -> None:
    """Доступность и свежесть баз антивируса (docs/operations/04 §8.2); сетевой опрос и перепроверка файлов — в отдельном потоке."""
    if delay:
        await asyncio.sleep(delay)

    def run() -> None:
        from .db import SessionLocal
        from .services import scanner_status
        with SessionLocal() as db:
            scanner_status.hourly(db, settings)
    try:
        await asyncio.to_thread(run)
    except Exception as e:   # pragma: no cover
        log.warning("scanner check failed: %s", e)
        _bg_error("scanner check", e)


def _bg_error(job: str, exc: BaseException) -> None:
    """D-117: сбой фонового задания — в «Ошибки системы» (администратор и ЗГД получают уведомление)."""
    from .services.app_errors import record_background
    record_background(job, exc)


async def _daily_jobs() -> None:
    """Фоновые задания: курс ЦБ ежедневно, проверка просрочек раз в час.

    В проде — отдельный воркер очереди (arq/Celery) и cron; здесь — простой цикл в процессе API.
    """
    from sqlalchemy import select
    from . import adapters
    from .db import SessionLocal
    from .models import FxRate, IntegrationLog
    _first_done = False
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
            _bg_error("daily job", e)
        try:   # запчасти: нормативные сроки (КП клиенту, ответ и подтверждение поставщика, просроченная поставка)
            from .services.parts_sla import check as check_parts_sla
            with SessionLocal() as db:
                if check_parts_sla(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("parts SLA check failed: %s", e)
            _bg_error("parts SLA check", e)
        try:   # запчасти: напоминания менеджерам о ТО машин клиентов
            from .api.parts_fleet import check_reminders
            with SessionLocal() as db:
                if check_reminders(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("parts TO reminders failed: %s", e)
            _bg_error("parts TO reminders", e)
        try:   # просроченные задачи — уведомления исполнителю и КД (раз в час, по одной на задачу)
            from .services.deal_service import check_overdue_tasks
            with SessionLocal() as db:
                if check_overdue_tasks(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("overdue check failed: %s", e)
            _bg_error("overdue check", e)
        try:   # D-101: уведомления о выполненных задачах — прочитаны (в том числе накопленные до этого правила)
            from .services.notification_kinds import close_done_task_notices
            with SessionLocal() as db:
                if close_done_task_notices(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("notification cleanup failed: %s", e)
            _bg_error("notification cleanup", e)
        try:   # D-112: виза ФД без ответа N рабочих дней → ЗГД
            from .services.visa_watch import escalate_fd
            with SessionLocal() as db:
                escalate_fd(db)
        except Exception as e:   # pragma: no cover
            log.warning("fd visa escalation failed: %s", e)
            _bg_error("fd visa escalation", e)
        try:   # черновики сделок без движения: напоминание через 3 дня, предупреждение на 30-й, архив через сутки после него
            from .services.draft_watch import check as check_drafts
            with SessionLocal() as db:
                if any(check_drafts(db).values()):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("draft watch failed: %s", e)
            _bg_error("draft watch", e)
        try:   # доверенности компаний группы: напоминания за 30 и 7 дней до истечения, перевод в «срок истёк»
            from .services.legal_poa import check_expiring
            with SessionLocal() as db:
                if check_expiring(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("poa expiry check failed: %s", e)
            _bg_error("poa expiry check", e)
        try:   # договоры: напоминания за 30 и 7 дней до окончания, автопролонгация, истечение срока
            from .services.legal_contracts import check_expiring as check_contracts
            with SessionLocal() as db:
                if check_contracts(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("contracts expiry check failed: %s", e)
            _bg_error("contracts expiry check", e)
        try:   # претензии: напоминание за 3 дня, уведомление в 1-й день просрочки, черновик претензии на 5-й день, истёк срок ответа
            from .services.legal_claims import ladder
            with SessionLocal() as db:
                if ladder(db):
                    db.commit()
        except Exception as e:   # pragma: no cover
            log.warning("claims ladder failed: %s", e)
            _bg_error("claims ladder", e)
        try:   # пересмотр прав: напоминания в системе за 14/7/1 день и о просрочке (docs/operations/04 §6)
            from .services.access_review import remind as remind_reviews
            with SessionLocal() as db:
                remind_reviews(db)
        except Exception as e:   # pragma: no cover
            log.warning("access review reminders failed: %s", e)
            _bg_error("access review reminders", e)
        try:   # помощник CRM: стирание содержимого по сроку хранения и удалённых диалогов (docs/operations/06 §7)
            from .services import assistant as _ai
            with SessionLocal() as db:
                _ai.purge(db, settings)
        except Exception as e:   # pragma: no cover
            log.warning("assistant purge failed: %s", e)
            _bg_error("assistant purge", e)
        if not _first_done:
            try:   # D-104: разово после старта — цена утверждённой версии по изменению условий контракта → в предложение
                from .services.contract_price import repair_existing
                with SessionLocal() as db:
                    fixed = repair_existing(db)
                if fixed:
                    log.info("contract price repair: %s offers", fixed)
                from .services.logistics_history import rebuild_once
                with SessionLocal() as db:
                    rebuild_once(db)
                from .services.deal_service import repair_task_assignees
                with SessionLocal() as db:
                    moved = repair_task_assignees(db)
                if moved:
                    log.info("task assignee repair: %s tasks", moved)
            except Exception as e:   # pragma: no cover
                log.warning("contract price repair failed: %s", e)
                _bg_error("contract price repair", e)
        asyncio.create_task(_scanner_check(0 if _first_done else SCANNER_START_GRACE_S))   # D-068: после старта — через 10 мин
        _first_done = True
        try:   # резервные копии: перенос статуса с хоста, «нет копии дольше 26 ч», уведомления в системе (docs/operations/03 §5)
            from .services.backup_status import refresh as refresh_backups
            with SessionLocal() as db:
                refresh_backups(db)
        except Exception as e:   # pragma: no cover
            log.warning("backup status refresh failed: %s", e)
            _bg_error("backup status refresh", e)
        await asyncio.sleep(3600)


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
    task = asyncio.create_task(_daily_jobs()) if settings.env != "recovery" and "pytest" not in sys.modules else None
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

for r in (auth.router, deals.router, logistics.router, service.router, pricing.router, references.router, admin.router, tasks_files_planfact.router,
          execution.router, tech_params.router, catalog.router, images_options.router, team.router, documents.router, deal_trash.router, parts_setup.router, parts_catalog.router, parts_requests.router, parts_rfq.router, parts_po.router, parts_stock.router, parts_inventory.router, parts_fleet.router, parts_warranty.router, parts_reports.router, model_docs.router, acceptance.router, notifications.router, summary.router, legal_profiles.router, legal_templates.router, legal_packages.router, legal_poa.router, legal_contracts.router, legal_claims.router, workplace.router, finance.router, finance.deal_router, operations.router, security.router, support.router, briefing.router, assistant.router, competitors.router, logistics_workspace.router, logistics_workspace.refs, negotiation.router, public_quote.router, deal_feed.router, office.router):
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
    return {"status": "ok", "env": settings.env, "auth": settings.auth_provider, "storage": settings.storage_provider, "file_scan": settings.file_scan}

