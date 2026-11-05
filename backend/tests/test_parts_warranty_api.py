"""Гарантийные претензии, сборные партии и отчёты запчастей на живой PostgreSQL.

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
MGR, CD, LOG, FD = "manager@example.com", "cd@example.com", "logist@example.com", "fd@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def _setup(client, t):
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    wh = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Склад W {t}", "legal_entity_id": ents["ru"]}), 201)
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"WAR {t}", "country": "CN", "default_currency": "CNY", "supplies": ["gsho", "parts"]}), 201)
    p = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"WAR-{t}", "name_ru": "Насос", "supplier_id": sup["id"], "purchase_price": 1000,
                                                                   "purchase_currency": "CNY", "weight_kg": 12}), 201)
    q = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"WAR-{t}-2", "name_ru": "Шланг", "supplier_id": sup["id"], "purchase_price": 100,
                                                                   "purchase_currency": "CNY", "weight_kg": 3}), 201)
    return ents, wh, sup, p, q


def test_warranty_claim(client):
    t = uuid.uuid4().hex[:5].upper()
    ents, wh, sup, p, q = _setup(client, t)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    assert client.post("/api/parts/claims", headers=H(LOG), json={"supplier_id": sup["id"], "lines": [{"article": p["article"]}]}).status_code == 403
    c = _ok(client.post("/api/parts/claims", headers=H(MGR), json={"supplier_id": sup["id"], "client_id": cl["id"], "machine_hours": 1450,
                                                                   "description": "течь по корпусу", "lines": [{"article": p["article"], "qty": 1, "cost_rub": 15000}]}), 201)
    assert c["number"].startswith("ГП-") and c["lines"][0]["part_id"] == p["id"] and c["cost_rub"] == 15000
    assert client.get(f"/api/parts/claims/{c['id']}/xlsx", headers=H(MGR)).status_code == 200
    _ok(client.post(f"/api/parts/claims/{c['id']}/photos", headers=H(MGR), files={"file": ("f.jpg", b"\xff\xd8 fake")}))
    assert client.post(f"/api/parts/claims/{c['id']}/answer", headers=H(MGR), json={"status": "accepted"}).status_code == 409
    s = _ok(client.post(f"/api/parts/claims/{c['id']}/sent", headers=H(MGR), json={"answer_days": 10}))
    assert s["status"] == "sent" and s["answer_due"] == (date.today() + timedelta(days=10)).isoformat()
    _ok(client.post(f"/api/parts/claims/{c['id']}/answer", headers=H(MGR), json={"status": "partial", "answer": "заменим насос, остальное — нет"}))
    # деталью — заказ заводу с гарантийной строкой по нулевой цене; деньгами — сумма к получению
    x = _ok(client.post(f"/api/parts/claims/{c['id']}/compensations", headers=H(MGR), json={"kind": "part"}))
    comp_part = x["compensations"][0]
    po = _ok(client.get(f"/api/parts/po/{comp_part['po_ids'][0]}", headers=H(CD)))
    assert po["lines"][0]["price"] == 0 and po["lines"][0]["warranty_claim_id"] == c["id"] and po["total"] == 0
    x = _ok(client.post(f"/api/parts/claims/{c['id']}/compensations", headers=H(MGR), json={"kind": "money", "amount": 500, "currency": "RUB"}))
    money = x["compensations"][1]
    _ok(client.post(f"/api/parts/claims/{c['id']}/compensations/{money['id']}/received", headers=H(MGR), json={}))
    assert _ok(client.get(f"/api/parts/claims/{c['id']}", headers=H(MGR)))["status"] == "partial"
    # приёмка гарантийной детали закрывает компенсацию деталью → «компенсация получена»
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(CD)))
    _ok(client.post("/api/parts/stock/receipts", headers=H(CD), json={"po_id": po["id"], "warehouse_id": wh["id"], "lines": [{"po_line_id": po["lines"][0]["id"], "qty": 1}]}), 201)
    d = _ok(client.get(f"/api/parts/claims/{c['id']}", headers=H(CD)))
    assert d["status"] == "compensated" and d["compensated_rub"] == 15500
    rep = next(r for r in _ok(client.get("/api/parts/reports/warranty", headers=H(CD)))["rows"] if r["supplier_id"] == sup["id"])
    assert rep["claims"] == 1 and rep["accepted_pct"] == 100 and rep["avg_days_to_compensation"] == 0
    _ok(client.post(f"/api/parts/claims/{c['id']}/close", headers=H(MGR), json={"reason": "компенсировано"}))


def test_batch_and_reports(client):
    t = uuid.uuid4().hex[:5].upper()
    ents, wh, sup, p, q = _setup(client, t)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"WAR-{t}\t\t2\nWAR-{t}-2\t\t4\nNO-{t}\tНет такой\t1"}), 201)
    ids = [l["id"] for l in r["lines"] if l["part"]]
    po = _ok(client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "request_line_ids": ids}), 201)[0]
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(MGR)))
    pl = [l["id"] for l in po["lines"]]
    assert client.post("/api/parts/batches", headers=H(LOG), json={"po_line_ids": pl}).status_code == 422          # ещё не подтверждены
    _ok(client.post(f"/api/parts/po/{po['id']}/confirm", headers=H(CD), json={"supplier_ref": "PI-1"}))
    assert set(pl) <= {x["id"] for x in _ok(client.get("/api/parts/batches/candidates", headers=H(LOG)))}
    b = _ok(client.post("/api/parts/batches", headers=H(LOG), json={"forwarder": "ТрансАзия", "po_line_ids": pl}), 201)
    assert b["number"].startswith("ПТ-") and b["total_weight_kg"] == 36 and {l["status"] for l in b["lines"]} == {"consolidation"}
    eta = (date.today() + timedelta(days=40)).isoformat()
    b = _ok(client.put(f"/api/parts/batches/{b['id']}", headers=H(LOG), json={"status": "transit", "eta": eta, "costs": [{"item": "фрахт", "amount": 3600, "currency": "RUB"}]}))
    shares = {l["number"]: l["extra_cost_rub"] for l in b["lines"]}
    assert sum(shares.values()) == 3600 and shares[f"WAR-{t}"] == 2400                     # по весу: 24 кг из 36
    assert {l["status"] for l in b["lines"]} == {"transit"} and {l["eta"] for l in b["lines"]} == {eta}
    assert "costs" not in _ok(client.get(f"/api/parts/batches/{b['id']}", headers=H(MGR)))
    assert client.put(f"/api/parts/batches/{b['id']}", headers=H(LOG), json={"status": "forming"}).status_code == 409
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert {l["status"] for l in rq["lines"] if l["part"]} == {"transit"}

    # приёмка и отгрузка → отчёты
    _ok(client.post("/api/parts/stock/receipts", headers=H(MGR), json={"po_id": po["id"], "warehouse_id": wh["id"], "lines": [
        {"po_line_id": pl[0], "qty": 2}, {"po_line_id": pl[1], "qty": 4}]}), 201)
    _ok(client.post(f"/api/parts/stock/requests/{r['id']}/client-order", headers=H(MGR), json={"line_ids": ids}))
    _ok(client.post(f"/api/parts/stock/requests/{r['id']}/shipments", headers=H(MGR), json={"warehouse_id": wh["id"], "lines": [
        {"line_id": ids[0], "qty": 2}, {"line_id": ids[1], "qty": 4}]}), 201)
    s = _ok(client.get("/api/parts/reports/sales?by=supplier", headers=H(CD)))
    row = next(x for x in s["rows"] if x["key"] == sup["id"])
    assert row["lines"] == 2 and row["revenue_rub"] > 0 and "margin_pct" in row
    assert "margin_pct" not in next(x for x in _ok(client.get("/api/parts/reports/sales?by=supplier", headers=H(MGR)))["rows"] if x["key"] == sup["id"])
    assert any(x["supplier_id"] == sup["id"] and x["lines"] == 2 for x in _ok(client.get("/api/parts/reports/lead-accuracy", headers=H(CD)))["rows"])
    assert any(x["article"] == f"NO-{t}" and not x["in_catalog"] for x in _ok(client.get("/api/parts/reports/missing", headers=H(MGR)))["rows"])
    sv = _ok(client.get("/api/parts/reports/service", headers=H(MGR)))
    assert sv["requests"] >= 1 and sv["lines"] >= 3
    assert any(x["request_number"] == r["number"] for x in _ok(client.get("/api/parts/reports/shipments", headers=H(MGR)))["rows"])
    st = _ok(client.get("/api/parts/reports/stock?idle_days=0", headers=H(CD)))
    assert st["positions"] >= 0
