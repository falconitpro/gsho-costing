"""Разовая правка: производитель PAUS, Германия у всех запчастей каталога; прежние значения — в журнале; повторно не выполняется."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import IntegrationLog, Part  # noqa: E402
from scripts.seed import parts_manufacturer_paus, run as seed  # noqa: E402


def test_paus_once():
    seed()
    t = uuid.uuid4().hex[:6]
    db = SessionLocal()
    p = Part(article=f"PAUS-{t}", name_ru="Деталь", manufacturer="Sandvik", origin_country="Швеция")
    db.add(p)
    db.execute(delete(IntegrationLog).where(IntegrationLog.system == "migration", IntegrationLog.operation == "parts_manufacturer_paus"))
    db.commit()
    assert parts_manufacturer_paus(db) >= 1
    db.commit()
    p = db.get(Part, p.id)
    assert (p.manufacturer, p.origin_country) == ("PAUS", "Германия")
    log = db.scalars(select(IntegrationLog).where(IntegrationLog.operation == "parts_manufacturer_paus")).first()
    assert {"id": p.id, "article": p.article, "manufacturer": "Sandvik", "origin_country": "Швеция"} in log.details["backup"]
    q = Part(article=f"PAUS-{t}-2", name_ru="Новая", manufacturer="Caterpillar")
    db.add(q)
    db.commit()
    assert parts_manufacturer_paus(db) == 0                        # повторно не выполняется — новые карточки не трогает
    db.commit()
    assert db.get(Part, q.id).manufacturer == "Caterpillar"
    db.close()
