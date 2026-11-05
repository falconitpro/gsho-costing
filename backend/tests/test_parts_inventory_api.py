"""Запчасти: инвентаризация и пополнение по минимальному остатку на живой PostgreSQL.

Запускается, когда задан DATABASE_URL (в CI — сервис postgres). Без БД — пропускается.
"""
from __future__ import annotations

import os
import tempfile
import uuid

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
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def _stocked(client, t):
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    wh = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Склад И {t}", "legal_entity_id": ents["ru"]}), 201)
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"INV {t}", "country": "RU", "default_currency": "RUB"}), 201)
    a = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"INV-{t}-1", "name_ru": "Фильтр", "supplier_id": sup["id"],
                                                                   "purchase_price": 100, "purchase_currency": "RUB", "moq": 5}), 201)
    b = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"INV-{t}-2", "name_ru": "Ремень", "supplier_id": sup["id"],
                                                                   "purchase_price": 50, "purchase_currency": "RUB"}), 201)
    c = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"INV-{t}-3", "name_ru": "Болт"}), 201)
    po = _ok(client.post("/api/parts/po", headers=H(CD), json={"supplier_id": sup["id"], "stock_lines": [{"part_id": a["id"], "qty": 10}, {"part_id": b["id"], "qty": 4}]}), 201)[0]
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(CD)))
    _ok(client.post("/api/parts/stock/receipts", headers=H(CD), json={"po_id": po["id"], "warehouse_id": wh["id"], "lines": [
        {"po_line_id": po["lines"][0]["id"], "qty": 10, "cell": "A-01"}, {"po_line_id": po["lines"][1]["id"], "qty": 4, "cell": "B-02"}]}), 201)
    return wh, sup, a, b, c


def _stock(client, pid, wid):
    return next(x for x in _ok(client.get(f"/api/parts/catalog/{pid}", headers=H(CD)))["stock"] if x["warehouse_id"] == wid)


def test_inventory(client):
    t = uuid.uuid4().hex[:5].upper()
    wh, sup, a, b, c = _stocked(client, t)
    assert client.post("/api/parts/stock/inventories", headers=H(LOG), json={"warehouse_id": wh["id"]}).status_code == 403
    inv = _ok(client.post("/api/parts/stock/inventories", headers=H(MGR), json={"warehouse_id": wh["id"]}), 201)
    assert inv["number"].startswith("ИН-") and inv["summary"]["lines"] == 2
    assert client.post("/api/parts/stock/inventories", headers=H(MGR), json={"warehouse_id": wh["id"]}).status_code == 409
    L = {l["article"]: l for l in inv["lines"]}
    # подсчёт частями: фильтр 9 (недостача 1), болт найден на полке (в учёте нет), ремень не посчитан
    _ok(client.put(f"/api/parts/stock/inventories/{inv['id']}/counts", headers=H(MGR), json={"lines": [{"line_id": L[f"INV-{t}-1"]["id"], "counted": 9}]}))
    assert client.put(f"/api/parts/stock/inventories/{inv['id']}/counts", headers=H(MGR), json={"lines": [{"article": "NO-SUCH", "counted": 1}]}).status_code == 422
    x = _ok(client.put(f"/api/parts/stock/inventories/{inv['id']}/counts", headers=H(MGR), json={"lines": [{"article": f"INV-{t}-3", "counted": 7, "cell": "C-1"}]}))
    assert x["summary"] == {"lines": 3, "counted": 2, "diff_lines": 2, "surplus": 7, "shortage": 1}
    s = _ok(client.post(f"/api/parts/stock/inventories/{inv['id']}/submit", headers=H(MGR)))
    assert s["status"] == "submitted" and s["summary"]["shortage"] == 5                      # непосчитанный ремень — 0 (недостача 4)
    assert client.put(f"/api/parts/stock/inventories/{inv['id']}/counts", headers=H(MGR), json={"lines": [{"line_id": L[f"INV-{t}-2"]["id"], "counted": 4}]}).status_code == 409
    assert client.post(f"/api/parts/stock/inventories/{inv['id']}/approve", headers=H(MGR), json={}).status_code == 403
    # КД возвращает на пересчёт, ремень нашёлся
    _ok(client.post(f"/api/parts/stock/inventories/{inv['id']}/approve", headers=H(CD), json={"approve": False, "comment": "пересчитать ремни"}))
    _ok(client.put(f"/api/parts/stock/inventories/{inv['id']}/counts", headers=H(MGR), json={"lines": [{"line_id": L[f"INV-{t}-2"]["id"], "counted": 4}]}))
    _ok(client.post(f"/api/parts/stock/inventories/{inv['id']}/submit", headers=H(MGR)))
    ap = _ok(client.post(f"/api/parts/stock/inventories/{inv['id']}/approve", headers=H(CD), json={}))
    assert ap["status"] == "approved" and ap["approved_by_name"]
    assert (_stock(client, a["id"], wh["id"])["qty"], _stock(client, b["id"], wh["id"])["qty"], _stock(client, c["id"], wh["id"])["qty"]) == (9, 4, 7)
    assert _stock(client, c["id"], wh["id"])["cell"] == "C-1"
    mv = _ok(client.get(f"/api/parts/stock/moves?warehouse_id={wh['id']}", headers=H(CD)))
    assert sorted((m["article"], m["qty"]) for m in mv if m["kind"] == "adjust") == [(f"INV-{t}-1", -1), (f"INV-{t}-3", 7)]


def test_replenishment(client):
    t = uuid.uuid4().hex[:5].upper()
    wh, sup, a, b, c = _stocked(client, t)
    assert client.put("/api/parts/stock/min", headers=H(LOG), json={"part_id": a["id"], "warehouse_id": wh["id"], "min_qty": 12}).status_code == 403
    _ok(client.put("/api/parts/stock/min", headers=H(MGR), json={"part_id": a["id"], "warehouse_id": wh["id"], "min_qty": 12}))
    _ok(client.put("/api/parts/stock/min", headers=H(MGR), json={"part_id": b["id"], "warehouse_id": wh["id"], "min_qty": 3}))
    g = next(x for x in _ok(client.get("/api/parts/stock/replenishment", headers=H(MGR))) if x["supplier_id"] == sup["id"])
    assert [(l["article"], l["suggest"]) for l in g["lines"]] == [(f"INV-{t}-1", 5)]          # до 12 не хватает 2, минимальная партия 5
    po = _ok(client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "stock_lines": [{"part_id": a["id"], "qty": 5}]}), 201)[0]
    assert po["lines"][0]["request_line_id"] is None
    assert sup["id"] not in {x["supplier_id"] for x in _ok(client.get("/api/parts/stock/replenishment", headers=H(MGR)))}   # ожидаемое закрывает минимум
