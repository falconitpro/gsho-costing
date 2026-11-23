"""D-143: фоновые задания — один цикл раз в час, в отдельном процессе (worker) или, пока он не запущен, в процессе API.

Защита от двойного запуска: блокировка PostgreSQL (pg_try_advisory_lock) на время цикла и отметка начала цикла —
второй процесс (ещё один worker, API при живом worker) цикл пропускает. Каждое задание отмечает в `job_runs`
последний запуск, успех и ошибку; сбой — ещё и в «Ошибки системы» (D-117).
Запуск worker: `python -m app.worker` (docker compose: сервис worker).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text

from .core.config import get_settings
from .db import SessionLocal, engine

log = logging.getLogger("gsho.jobs")
LOCK_KEY = 774_201_143
CYCLE_EVERY = timedelta(minutes=55)       # цикл — раз в час; запас на дрейф таймера
WORKER_FRESH = timedelta(minutes=15)      # worker «жив», если отмечался не позже 15 мин назад
SCANNER_GRACE_S = 600                     # антивирус после старта 1–2 мин грузит базы (D-068)
STARTED = time.monotonic()
HOST = (os.environ.get("HOSTNAME") or socket.gethostname())[:64]


def _mark(job: str, *, start: bool = False, ok: bool = False, error: str = "", ms: int | None = None) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        c.execute(text("""INSERT INTO job_runs (job, last_start, last_ok, last_error, error_at, runs, duration_ms, host)
                          VALUES (:j, :s, :o, :e, :ea, :r, :ms, :h)
                          ON CONFLICT (job) DO UPDATE SET
                            last_start = COALESCE(:s, job_runs.last_start), last_ok = COALESCE(:o, job_runs.last_ok),
                            last_error = CASE WHEN :e <> '' THEN :e WHEN :o IS NOT NULL THEN '' ELSE job_runs.last_error END,
                            error_at = COALESCE(:ea, job_runs.error_at), runs = job_runs.runs + :r,
                            duration_ms = COALESCE(:ms, job_runs.duration_ms), host = :h"""),
                  {"j": job, "s": now if start else None, "o": now if ok else None, "e": error[:500], "ea": now if error else None,
                   "r": 1 if start else 0, "ms": ms, "h": HOST})


def heartbeat(role: str) -> None:
    """Отметка «процесс жив»: worker — каждые 5 минут."""
    _mark(f"heartbeat:{role}", start=True, ok=True)


def worker_alive() -> bool:
    with engine.connect() as c:
        t = c.execute(text("SELECT last_ok FROM job_runs WHERE job = 'heartbeat:worker'")).scalar()
    return bool(t and datetime.now(timezone.utc) - (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) < WORKER_FRESH)


def _run(job: str, fn: Callable[[], object]) -> None:
    t0 = time.monotonic()
    _mark(job, start=True)
    try:
        fn()
        _mark(job, ok=True, ms=int((time.monotonic() - t0) * 1000))
    except Exception as e:   # noqa: BLE001 — одно задание не останавливает остальные
        log.warning("job %s failed: %s", job, e)
        _mark(job, error=f"{type(e).__name__}: {str(e)[:300]}", ms=int((time.monotonic() - t0) * 1000))
        try:
            from .services.app_errors import record_background
            record_background(job, e)
        except Exception:   # noqa: BLE001
            log.error("cannot record job error: %s", traceback.format_exc()[-500:])


def _commit_if(fn: Callable[..., object]) -> Callable[[], None]:
    def run() -> None:
        with SessionLocal() as db:
            fn(db)
            db.commit()
    return run


def _fx() -> None:
    """Курсы ЦБ на сегодня; история за 5 лет для прогноза — один раз, если её почти нет."""
    from sqlalchemy import select
    from . import adapters
    from .adapters.fx_source import SUPPORTED as FX_CODES
    from .models import FxRate, IntegrationLog
    with SessionLocal() as db:
        today = date.today()
        have_today = set(db.scalars(select(FxRate.currency).where(FxRate.on == today, FxRate.source == "cbr")).all())
        if not set(FX_CODES) <= have_today:
            try:
                rates = adapters.fx_source().daily(today)
                for cur, rate in rates.items():
                    if cur != "RUB" and cur not in have_today:
                        db.add(FxRate(on=today, currency=cur, rate=rate, source="cbr"))
                db.add(IntegrationLog(system="cbr", operation="daily", ok=True, details={"on": str(today)}))
            except Exception as e:   # noqa: BLE001 — сбой ЦБ пишется в журнал интеграций, курс возьмётся последний известный
                db.add(IntegrationLog(system="cbr", operation="daily", ok=False, details={"error": str(e)[:300]}))
            db.commit()
        for cur in (() if "pytest" in sys.modules else FX_CODES):
            n = len(db.scalars(select(FxRate.id).where(FxRate.currency == cur, FxRate.source == "cbr", FxRate.on >= today - timedelta(days=365 * 5))).all())
            if n >= 200:
                continue
            try:
                pts = adapters.fx_source().history(cur, today - timedelta(days=365 * 5), today)
                have = {r for r in db.scalars(select(FxRate.on).where(FxRate.currency == cur, FxRate.source == "cbr")).all()}
                for d, r in pts:
                    if d not in have:
                        db.add(FxRate(on=d, currency=cur, rate=r, source="cbr"))
                db.add(IntegrationLog(system="cbr", operation="history", ok=True, details={"currency": cur, "points": len(pts)}))
            except Exception as e:   # noqa: BLE001
                db.add(IntegrationLog(system="cbr", operation="history", ok=False, details={"currency": cur, "error": str(e)[:300]}))
            db.commit()


def _one_time_repairs() -> None:
    """Разовые исправления данных после старта (D-104, D-110, D-111): повторный запуск ничего не меняет."""
    from .services.contract_price import repair_existing
    from .services.deal_service import repair_task_assignees
    from .services.logistics_history import rebuild_once
    with SessionLocal() as db:
        repair_existing(db)
    with SessionLocal() as db:
        rebuild_once(db)
    with SessionLocal() as db:
        repair_task_assignees(db)
    from .services.legal_contracts import repair_client_vat, repair_unprojected
    with SessionLocal() as db:
        repair_client_vat(db)                  # D-147: сумма договора с клиентом — с НДС
    with SessionLocal() as db:
        repair_unprojected(db)                 # D-146: подписанные в сделке договоры без строк в «Финансах»


def _scanner() -> None:
    from .services import scanner_status
    with SessionLocal() as db:
        scanner_status.hourly(db, get_settings())


def _jobs(first: bool) -> list[tuple[str, Callable[[], None]]]:
    from .api.parts_fleet import check_reminders
    from .services import assistant as _ai
    from .services.access_review import remind as remind_reviews
    from .services.backup_status import refresh as refresh_backups
    from .services.deal_service import check_overdue_tasks
    from .services.draft_watch import check as check_drafts
    from .services.legal_claims import ladder
    from .services.legal_contracts import check_expiring as check_contracts
    from .services.legal_poa import check_expiring
    from .services.notification_kinds import close_done_task_notices
    from .services.assignments import check as check_assignments
    from .services.office_watch import check as check_office
    from .services.signals import refresh as refresh_signals
    from .services.parts_sla import check as check_parts_sla
    from .services.visa_watch import escalate_fd
    jobs: list[tuple[str, Callable[[], None]]] = [
        ("fx rates", _fx),                                           # курсы ЦБ
        ("parts SLA check", _commit_if(check_parts_sla)),            # запчасти: нормативные сроки
        ("parts TO reminders", _commit_if(check_reminders)),         # запчасти: ТО машин клиентов
        ("overdue check", _commit_if(check_overdue_tasks)),          # просроченные задачи
        ("notification cleanup", _commit_if(close_done_task_notices)),
        ("office due watch", _commit_if(check_office)),              # D-133, D-140: сроки писем, ознакомление
        ("assignments due watch", _commit_if(check_assignments)),    # D-155: сроки поручений ЗГД
        ("signals refresh", _commit_if(refresh_signals)),            # D-155: история сигналов «Требует вмешательства»
        ("fd visa escalation", _commit_if(escalate_fd)),             # D-112
        ("draft watch", _commit_if(check_drafts)),                   # черновики без движения
        ("poa expiry check", _commit_if(check_expiring)),            # доверенности
        ("contracts expiry check", _commit_if(check_contracts)),     # договоры
        ("claims ladder", _commit_if(ladder)),                       # претензии
        ("access review reminders", _commit_if(remind_reviews)),     # пересмотр прав
        ("assistant purge", _commit_if(lambda db: _ai.purge(db, get_settings()))),
        ("backup status refresh", _commit_if(refresh_backups)),      # резервные копии
    ]
    if first:
        jobs.append(("one-time data repairs", _one_time_repairs))
    if time.monotonic() - STARTED >= SCANNER_GRACE_S:
        jobs.append(("scanner check", _scanner))                    # D-068: не раньше 10 мин после старта
    return jobs


_first_done = False


def run_cycle(runner: str, force: bool = False) -> str:
    """Один цикл заданий. Возвращает: done | busy (идёт в другом процессе) | early (с прошлого цикла меньше 55 мин)."""
    global _first_done
    with engine.connect() as lock_conn:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock_conn.commit()
        if not got:
            return "busy"
        try:
            with engine.connect() as c:
                last = c.execute(text("SELECT last_start FROM job_runs WHERE job = 'cycle'")).scalar()
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last and not force and datetime.now(timezone.utc) - last < CYCLE_EVERY:
                if not _first_done:   # процесс только что запущен, а цикл недавно прошёл в другом — разовые исправления всё равно один раз
                    _run("one-time data repairs", _one_time_repairs)
                    _first_done = True
                return "early"
            _mark("cycle", start=True)
            t0 = time.monotonic()
            for name, fn in _jobs(first=not _first_done):
                _run(name, fn)
            _first_done = True
            _mark("cycle", ok=True, ms=int((time.monotonic() - t0) * 1000))
            _mark(f"runner:{runner}", start=True, ok=True)
            return "done"
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()


def status() -> dict:
    """Для проверки состояния и экрана «Ошибки системы»: последний цикл, кто его выполнил, задания с ошибкой."""
    with engine.connect() as c:
        rows = c.execute(text("SELECT job, last_start, last_ok, last_error, error_at, runs, duration_ms, host FROM job_runs ORDER BY job")).mappings().all()
    by = {r["job"]: r for r in rows}
    now = datetime.now(timezone.utc)
    cyc = by.get("cycle")
    last_ok = cyc["last_ok"] if cyc else None
    if last_ok is not None and last_ok.tzinfo is None:
        last_ok = last_ok.replace(tzinfo=timezone.utc)
    iso = lambda t: t.isoformat() if t else None   # noqa: E731
    return {
        "worker_alive": worker_alive(),
        "last_cycle_ok": iso(last_ok),
        "stale": last_ok is None or now - last_ok > timedelta(hours=2, minutes=10),
        "jobs": [{"job": r["job"], "last_ok": iso(r["last_ok"]), "last_error": r["last_error"], "error_at": iso(r["error_at"]),
                  "duration_ms": r["duration_ms"], "host": r["host"]} for r in rows if ":" not in r["job"] and r["job"] != "cycle"],
        "failing": [r["job"] for r in rows if r["last_error"] and ":" not in r["job"]],
    }
