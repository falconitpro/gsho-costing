"""Нормативные сроки запчастей на живой PostgreSQL: КП после заявки, подтверждение заказа, просроченная поставка — по одному напоминанию."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PartRequest, PoLine, PurchaseOrder  # noqa: E402
from app.services import parts_sla  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731


def test_sla():
    seed()
    t = uuid.uuid4().hex[:5].upper()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H("manager@example.com")).json()[0]
        sup = c.post("/api/refs/factories", headers=H("cd@example.com"), json={"name": f"SLA {t}", "country": "RU", "supplies": ["parts"]}).json()
        p = c.post("/api/parts/catalog", headers=H("cd@example.com"), json={"article": f"SLA-{t}", "name_ru": "Деталь", "supplier_id": sup["id"],
                                                                          "purchase_price": 100, "purchase_currency": "RUB", "lead_days": 5}).json()
        r = c.post("/api/parts/requests", headers=H("manager@example.com"), json={"client_id": cl["id"], "urgent": True, "lines_text": f"SLA-{t}\t\t1"}).json()
        po = c.post("/api/parts/po", headers=H("cd@example.com"), json={"supplier_id": sup["id"], "request_line_ids": [r["lines"][0]["id"]]}).json()[0]
        c.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H("cd@example.com"))
    future = date.today() + timedelta(days=30)
    db = SessionLocal()
    n = parts_sla.check(db, future)
    db.commit()
    assert n >= 3
    assert "kp" in db.get(PartRequest, r["id"]).sla_notified
    assert "confirm" in db.get(PurchaseOrder, po["id"]).sla_notified
    pl = db.get(PoLine, po["lines"][0]["id"])
    assert pl.overdue_eta == pl.eta
    before = parts_sla.check(db, future)
    db.commit()
    db.close()
    db = SessionLocal()
    assert db.get(PartRequest, r["id"]).sla_notified.count("kp") == 1
    db.close()
    assert before >= 0
