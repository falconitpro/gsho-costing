"""Запчасти, шаг 5б — заказы поставщикам на живой PostgreSQL: заказ по строкам, внутригрупповые заказы, подтверждение, статусы.

Запускается, когда задан DATABASE_URL (в CI — сервис postgres). Без БД — пропускается.
"""
from __future__ import annotations

import io
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
from openpyxl import load_workbook  # noqa: E402

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


def test_po_flow(client):
    t = uuid.uuid4().hex[:5].upper()
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    route = _ok(client.post("/api/parts/routes", headers=H(CD), json={"name": f"PO route {t}", "steps": [
        {"buyer_entity_id": ents["cn"], "incoterms": "EXW", "currency": "CNY", "transit_days": 5},
        {"seller_entity_id": ents["cn"], "buyer_entity_id": ents["kg"], "incoterms": "FCA", "currency": "CNY", "transit_days": 20},
        {"seller_entity_id": ents["kg"], "buyer_entity_id": ents["ru"], "incoterms": "DAP", "currency": "CNY", "transit_days": 10}]}), 201)
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"PO CN {t}", "country": "CN", "default_currency": "CNY",
                                                                      "default_route_id": route["id"]}), 201)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    a = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"PO-{t}-1", "name_ru": "Фильтр", "names": {"cn": "滤芯"},
                                                                   "supplier_id": sup["id"], "purchase_price": 100, "purchase_currency": "CNY", "lead_days": 15}), 201)
    b = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"PO-{t}-2", "name_ru": "Палец", "supplier_id": sup["id"],
                                                                   "purchase_price": 40, "purchase_currency": "CNY", "lead_days": 30}), 201)
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"PO-{t}-1\t\t2\nPO-{t}-2\t\t5"}), 201)
    L = {l["no"]: l for l in r["lines"]}
    assert L[1]["supplier_id"] == sup["id"] and L[1]["status"] == "quoted"          # поставщик из карточки

    cands = _ok(client.get("/api/parts/po/candidates", headers=H(MGR)))
    g = next(x for x in cands if x["supplier_id"] == sup["id"])
    assert {x["id"] for x in g["lines"]} >= {L[1]["id"], L[2]["id"]} and all(x["priced"] for x in g["lines"])
    assert client.post("/api/parts/po", headers=H(LOG), json={"supplier_id": sup["id"], "request_line_ids": [L[1]["id"]]}).status_code == 403

    pos = _ok(client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "request_line_ids": [L[1]["id"], L[2]["id"]],
                                                                 "stock_lines": [{"part_id": b["id"], "qty": 10}]}), 201)
    assert len(pos) == 1
    po = pos[0]
    assert po["number"].startswith("ЗК-") and po["status"] == "draft" and po["buyer_name"] and po["currency"] == "CNY" and len(po["lines"]) == 3
    assert po["total"] == 2 * 100 + 5 * 40 + 10 * 40
    assert po["lines"][0]["eta"] == (date.today() + timedelta(days=15 + 35)).isoformat()
    assert [c["number"] for c in po["intragroup"]] == [f"{po['number']}/2", f"{po['number']}/3"]
    assert "price" not in po["intragroup"][0]["lines"][0]                         # наценки группы менеджеру не видны
    cd = _ok(client.get(f"/api/parts/po/{po['id']}", headers=H(CD)))
    assert cd["intragroup"][0]["lines"][0]["price"] >= 100
    # повторно ту же строку не заказать
    assert client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "request_line_ids": [L[1]["id"]]}).status_code == 409
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert {l["status"] for l in rq["lines"]} == {"po"} and rq["lines"][0]["eta"]

    # файл заказа на двух языках
    x = client.get(f"/api/parts/po/{po['id']}/xlsx", headers=H(MGR))
    rows = list(load_workbook(io.BytesIO(x.content)).active.iter_rows(values_only=True))
    assert "订单" in rows[0][0]
    assert client.get(f"/api/parts/po/{po['intragroup'][0]['id']}/xlsx", headers=H(MGR)).status_code == 403

    # статус до отправки не двигается; без контакта — «не отправлено», вручную — «отправлен»
    ids = [l["id"] for l in po["lines"]]
    assert client.post(f"/api/parts/po/{po['id']}/lines/status", headers=H(LOG), json={"line_ids": ids[:1], "status": "shipped"}).status_code == 409
    s = _ok(client.post(f"/api/parts/po/{po['id']}/send", headers=H(MGR), json={}))
    assert all(not x["ok"] for x in s["send_results"]) and s["status"] == "draft"
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(MGR)))

    # подтверждение: дата отгрузки сдвигает срок, менеджер получает уведомление
    ship = date.today() + timedelta(days=40)
    c = _ok(client.post(f"/api/parts/po/{po['id']}/confirm", headers=H(CD), json={"supplier_ref": "PI-77", "planned_ship_date": ship.isoformat(),
                                                                                 "payments": [{"due": date.today().isoformat(), "amount": 240}]}))
    assert c["status"] == "confirmed" and all(l["status"] == "confirmed" for l in c["lines"])
    assert c["lines"][0]["eta"] == (ship + timedelta(days=35)).isoformat()
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert rq["lines"][0]["eta"] == c["lines"][0]["eta"]

    # статусы поставки: логист ведёт, назад нельзя, заявка видит «в пути» и «на складе»
    _ok(client.post(f"/api/parts/po/{po['id']}/lines/status", headers=H(LOG), json={"line_ids": ids, "status": "transit"}))
    assert client.post(f"/api/parts/po/{po['id']}/lines/status", headers=H(LOG), json={"line_ids": ids[:1], "status": "confirmed"}).status_code == 409
    assert client.post(f"/api/parts/po/{po['id']}/cancel", headers=H(MGR)).status_code == 409
    assert client.post(f"/api/parts/po/{po['id']}/lines/status", headers=H(LOG), json={"line_ids": ids[:1], "status": "received"}).status_code == 422
    wh = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Склад PO {t}", "legal_entity_id": ents["ru"]}), 201)
    _ok(client.post("/api/parts/stock/receipts", headers=H(MGR), json={"po_id": po["id"], "warehouse_id": wh["id"],
                                                                       "lines": [{"po_line_id": ids[0], "qty": 2, "cell": "A-1"}]}), 201)
    d = _ok(client.get(f"/api/parts/po/{po['id']}", headers=H(CD)))
    assert d["status"] == "in_progress" and d["intragroup"][0]["lines"][0]["status"] == "received"
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    st = {l["no"]: l["status"] for l in rq["lines"]}
    assert st == {1: "in_stock", 2: "transit"}
    _ok(client.post("/api/parts/stock/receipts", headers=H(MGR), json={"po_id": po["id"], "warehouse_id": wh["id"],
                                                                       "lines": [{"po_line_id": ids[1], "qty": 5}, {"po_line_id": ids[2], "qty": 10}]}), 201)
    d = _ok(client.get(f"/api/parts/po/{po['id']}", headers=H(CD)))
    assert d["status"] == "closed"
    assert po["id"] not in {p["id"] for p in _ok(client.get("/api/parts/po", headers=H(FD)))}
    assert po["id"] in {p["id"] for p in _ok(client.get(f"/api/parts/po?open_only=false&request_id={r['id']}", headers=H(FD)))}


def test_po_cancel_returns_lines(client):
    t = uuid.uuid4().hex[:5].upper()
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"PO RU {t}", "country": "RU", "default_currency": "RUB"}), 201)
    p = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"POC-{t}", "name_ru": "Втулка", "supplier_id": sup["id"],
                                                                   "purchase_price": 500, "purchase_currency": "RUB"}), 201)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"POC-{t}\t\t1"}), 201)
    lid = r["lines"][0]["id"]
    po = _ok(client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "request_line_ids": [lid]}), 201)[0]
    assert po["intragroup"] == [] and po["buyer_name"]                               # без маршрута — покупает компания в РФ
    c = _ok(client.post(f"/api/parts/po/{po['id']}/cancel", headers=H(MGR)))
    assert c["status"] == "cancelled"
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert rq["lines"][0]["status"] == "quoted" and rq["lines"][0]["eta"] is None
    assert lid in {x["id"] for g in _ok(client.get("/api/parts/po/candidates", headers=H(MGR))) for x in g["lines"]}
