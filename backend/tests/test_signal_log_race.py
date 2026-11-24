"""D-160 (попутно): одновременное открытие «Пульта» не даёт 500 — новый сигнал записывается один раз."""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Base, SignalLog  # noqa: E402
from app.services import signals  # noqa: E402


def test_parallel_sync_same_new_key():
    db0 = SessionLocal()
    Base.metadata.create_all(db0.get_bind(), tables=[SignalLog.__table__])
    db0.close()
    key = f"test:race:{uuid.uuid4()}"
    esc = [{"key": key, "title": "гонка", "priority": "high"}]
    errors: list[BaseException] = []
    first_done = threading.Event()

    def run(delay_commit: float, mark: bool):
        db = SessionLocal()
        try:
            signals.sync(db, esc)
            db.flush()
            if mark:
                first_done.set()
            time.sleep(delay_commit)
            db.commit()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            db.rollback()
        finally:
            db.close()

    t1 = threading.Thread(target=run, args=(0.6, True))
    t1.start()
    first_done.wait(5)
    t2 = threading.Thread(target=run, args=(0, False))
    t2.start()
    t1.join(10)
    t2.join(10)
    assert not errors, errors
    db = SessionLocal()
    try:
        rows = db.scalars(select(SignalLog).where(SignalLog.key == key)).all()
        assert len(rows) == 1 and rows[0].resolved_at is None and rows[0].title == "гонка"
        db.delete(rows[0])
        db.commit()
    finally:
        db.close()
