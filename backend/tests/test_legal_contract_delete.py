"""Удаление ошибочно заведённых проектов договоров: только ЗГД, только до подписания, без доп. соглашений и претензий; снимок — в журнале."""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, Contract  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, LAW = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "lawyer@example.com"


def test_gd_deletes_unsigned_drafts_only():
    seed()
    with TestClient(app) as c:
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        new = lambda: c.post("/api/legal/contracts", headers=H(CD), json={"kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «Ошибка»",  # noqa: E731
                                                                           "subject": "заведён по ошибке", "amount": 1000, "currency": "rub"}).json()
        k = new()
        assert c.get(f"/api/legal/contracts/{k['id']}", headers=H(GD)).json()["can_delete"] is True
        assert c.get(f"/api/legal/contracts/{k['id']}", headers=H(CD)).json()["can_delete"] is False
        assert c.delete(f"/api/legal/contracts/{k['id']}", headers=H(CD)).status_code == 403
        assert c.delete(f"/api/legal/contracts/{k['id']}", headers=H(MGR)).status_code in (403, 404)
        # с доп. соглашением — нельзя, пока оно есть
        ds = c.post("/api/legal/contracts", headers=H(CD), json={"parent_id": k["id"], "subject": "изменение", "amount": 10}).json()
        r = c.delete(f"/api/legal/contracts/{k['id']}", headers=H(GD))
        assert r.status_code == 409 and ds["number"] in r.json()["detail"]
        assert c.delete(f"/api/legal/contracts/{ds['id']}", headers=H(GD)).status_code == 200
        r = c.delete(f"/api/legal/contracts/{k['id']}", headers=H(GD))
        assert r.status_code == 200 and r.json()["deleted"] == k["number"]
        assert c.get(f"/api/legal/contracts/{k['id']}", headers=H(GD)).status_code == 404
        with SessionLocal() as db:
            a = db.scalar(select(AuditLog).where(AuditLog.entity == "contract", AuditLog.entity_id == k["id"], AuditLog.action == "delete"))
            assert a is not None and a.before["number"] == k["number"]
        # подписанный — нельзя
        s = new()
        with SessionLocal() as db:
            x = db.get(Contract, s["id"])
            x.status, x.signed_on = "active", date.today()
            db.commit()
        assert c.get(f"/api/legal/contracts/{s['id']}", headers=H(GD)).json()["can_delete"] is False
        assert c.delete(f"/api/legal/contracts/{s['id']}", headers=H(GD)).status_code == 409
