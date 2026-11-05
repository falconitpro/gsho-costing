"""Запчасти, шаг 7 — парк машин на живой PostgreSQL: машина, наработка, ТО-комплекты, напоминание, заявка на комплект.

Запускается, когда задан DATABASE_URL (в CI — сервис postgres). Без БД — пропускается.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, timedelta

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
MGR, CD, LOG, SRV = "manager@example.com", "cd@example.com", "logist@example.com", "service@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_fleet_flow(client):
    t = uuid.uuid4().hex[:5].upper()
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    oil = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"FL-{t}-OIL", "name_ru": "Фильтр масляный", "purchase_price": 900,
                                                                     "purchase_currency": "RUB", "applicability": [{"other_manufacturer": "Caterpillar", "other_model": f"R1700-{t}"}]}), 201)
    air = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"FL-{t}-AIR", "name_ru": "Фильтр воздушный", "purchase_price": 2500,
                                                                     "purchase_currency": "RUB"}), 201)
    assert client.post("/api/parts/fleet/machines", headers=H(LOG), json={"client_id": cl["id"], "other_manufacturer": "Caterpillar", "other_model": "X"}).status_code == 403
    m = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Caterpillar",
                                                                           "other_model": f"R1700-{t}", "serial": f"SN-{t}", "site": "рудник Северный"}), 201)
    assert (m["manufacturer"], m["model"], m["other_model_id"] is not None) == ("Caterpillar", f"R1700-{t}", True)
    today = date.today()
    _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/hours", headers=H(SRV), json={"on": (today - timedelta(days=30)).isoformat(), "hours": 1000}), 201)
    assert client.post(f"/api/parts/fleet/machines/{m['id']}/hours", headers=H(MGR), json={"on": (today - timedelta(days=10)).isoformat(), "hours": 900}).status_code == 422
    m = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/hours", headers=H(MGR), json={"on": (today - timedelta(days=10)).isoformat(), "hours": 1200}), 201)
    assert m["forecast"]["per_day"] == 10 and m["forecast"]["hours_now"] == 1300 and m["forecast"]["next"] is None   # комплектов ещё нет

    k250 = _ok(client.post("/api/parts/fleet/kits", headers=H(SRV), json={"other_model_id": m["other_model_id"], "name": "ТО-250", "interval_hours": 250,
                                                                          "lines": [{"article": f"FL-{t}-OIL", "qty": 2}]}), 201)
    k500 = _ok(client.post("/api/parts/fleet/kits", headers=H(SRV), json={"other_model_id": m["other_model_id"], "name": "ТО-500", "interval_hours": 500,
                                                                          "lines": [{"article": f"FL-{t}-AIR", "qty": 1}, {"article": f"FL-{t}-OIL", "qty": 1},
                                                                                    {"article": "НЕТ-В-КАТАЛОГЕ", "qty": 1}]}), 201)
    assert k500["lines"][2]["part_id"] is None
    m = _ok(client.get(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR)))
    nx = m["forecast"]["next"]
    assert nx["to_hours"] == 1500 and [k["name"] for k in nx["kits"]] == ["ТО-500", "ТО-250"] and nx["due_on"] == (today + timedelta(days=20)).isoformat()
    assert f"FL-{t}-OIL" in {p["article"] for p in _ok(client.get(f"/api/parts/fleet/machines/{m['id']}/parts", headers=H(MGR)))}

    # напоминание: ТО через 20 дней, порог 30 — одно на машину и ТО
    _ok(client.post("/api/parts/fleet/reminders/run", headers=H(MGR)))
    _ok(client.post("/api/parts/fleet/reminders/run", headers=H(MGR)))
    rem = [r for r in _ok(client.get("/api/parts/fleet/reminders", headers=H(CD))) if r["machine_id"] == m["id"]]
    assert len(rem) == 1 and rem[0]["to_hours"] == 1500
    assert m["id"] in {x["id"] for x in _ok(client.get("/api/parts/fleet/machines?due_days=30", headers=H(MGR)))}

    # заявка на комплект в один клик: строки сложены, подобраны по каталогу
    r = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/kit-request", headers=H(MGR), json={"reminder_id": rem[0]["id"]}), 201)
    L = {l["raw_article"]: l for l in r["lines"]}
    assert L[f"FL-{t}-OIL"]["qty"] == 3 and L[f"FL-{t}-OIL"]["part"]["id"] == oil["id"] and L[f"FL-{t}-AIR"]["part"]["id"] == air["id"]
    assert r["machine_id"] == m["id"] and "R1700" in r["machine"] and "ТО-500" in r["comment"]
    assert not [x for x in _ok(client.get("/api/parts/fleet/reminders", headers=H(CD))) if x["machine_id"] == m["id"]]
    card = _ok(client.get(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR)))
    assert card["requests"][0]["id"] == r["id"]
    # заявка вручную с машиной из парка: машина должна быть этого клиента
    other = next((c for c in _ok(client.get("/api/refs/clients", headers=H(MGR))) if c["id"] != cl["id"]), None)
    if other:
        assert client.post("/api/parts/requests", headers=H(MGR), json={"client_id": other["id"], "machine_id": m["id"], "lines_text": "X\t\t1"}).status_code == 422
    _ok(client.post("/api/parts/fleet/sync-deals", headers=H(CD)))
