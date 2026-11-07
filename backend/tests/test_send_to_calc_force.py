"""КД и ЗГД отправляют на расчёт, даже если обязательные данные заполнены не до конца; менеджер — нет."""
from __future__ import annotations

import os
import tempfile

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD = "manager@example.com", "cd@example.com"


def test_force_send():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        empty = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "x"}).json()
        r = c.post(f"/api/deals/{empty['id']}/send-to-calc", headers=H(CD), json={"force": True})
        assert r.status_code == 422 and "нет позиций" in r.text                   # без позиций считать нечего
        fac = next(f for f in c.get("/api/refs/factories", headers=H(MGR)).json() if f["approval_status"] == "approved")
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "y"}).json()
        p = c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "FORCE-1", "machine_type": "Самосвал", "unit_price": 0})
        assert p.status_code == 201, p.text
        r = c.post(f"/api/deals/{d['id']}/send-to-calc", headers=H(MGR), json={"force": True})
        assert r.status_code == 422 and r.json()["detail"]["can_force"] is False
        r = c.post(f"/api/deals/{d['id']}/send-to-calc", headers=H(CD), json={"force": True, "comment": "цену уточняем"})
        assert r.status_code == 200 and r.json()["status"] == "logistics_calc"
        row = next(x for x in c.get("/api/deals", headers=H(CD)).json() if x["id"] == d["id"])   # список сделок: менеджер, оборудование
        assert row["manager_name"] and "FORCE-1" in row["equipment"] and row["machines"] >= 1
