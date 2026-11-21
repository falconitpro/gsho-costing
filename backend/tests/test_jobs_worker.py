"""D-143: фоновые задания — цикл с блокировкой, без двойного запуска, отметки заданий, отказ одного задания не мешает остальным."""
import os

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")


def test_cycle_lock_marks_and_failures(monkeypatch):
    from sqlalchemy import text

    from app import jobs
    from app.db import engine, init_db
    from scripts.seed import run as seed
    init_db()
    seed()
    with engine.begin() as c:
        c.execute(text("DELETE FROM job_runs"))
    calls = []
    boom = RuntimeError("тестовый сбой")

    def fake_jobs(first):
        return [("ok job", lambda: calls.append("ok")), ("bad job", lambda: (_ for _ in ()).throw(boom)), ("after", lambda: calls.append("after"))]
    monkeypatch.setattr(jobs, "_jobs", fake_jobs)
    monkeypatch.setattr(jobs, "_first_done", False)
    assert jobs.run_cycle("api") == "done"
    assert calls == ["ok", "after"]                       # сбой одного задания не остановил следующее
    st = jobs.status()
    assert "bad job" in st["failing"] and not st["stale"] and st["last_cycle_ok"]
    assert jobs.run_cycle("worker") == "early"            # второй процесс в течение часа цикл не повторяет
    assert calls == ["ok", "after"]
    # пока цикл идёт в другом процессе (блокировка занята) — пропуск
    with engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": jobs.LOCK_KEY}).scalar()
        assert jobs.run_cycle("api", force=True) == "busy"
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": jobs.LOCK_KEY})
    assert jobs.run_cycle("api", force=True) == "done" and calls == ["ok", "after", "ok", "after"]
    # worker отметился — API задания не выполняет
    assert not jobs.worker_alive()
    jobs.heartbeat("worker")
    assert jobs.worker_alive()
    # ошибка задания попала в «Ошибки системы»
    from app.db import SessionLocal
    from app.models import AppError
    with SessionLocal() as db:
        assert db.query(AppError).filter(AppError.where.like("%bad job%")).count() >= 1


def test_health_reports_jobs():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        j = c.get("/api/health").json()["jobs"]
        assert j["runner"] in ("worker", "api") and "stale" in j
