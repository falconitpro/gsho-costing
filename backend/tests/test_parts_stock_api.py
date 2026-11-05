"""Запчасти, шаг 6 — склад на живой PostgreSQL: приёмка с расхождениями, жёсткий резерв, заказ клиента, отгрузка, движения.

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


def test_receipt_reserve_ship(client):
    t = uuid.uuid4().hex[:5].upper()
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    wh = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Склад {t}", "legal_entity_id": ents["ru"]}), 201)
    virt = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Вирт {t}", "legal_entity_id": ents["kg"], "kind": "virtual"}), 201)
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"ST {t}", "country": "RU", "default_currency": "RUB"}), 201)
    a = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"ST-{t}-1", "name_ru": "Фильтр", "supplier_id": sup["id"],
                                                                   "purchase_price": 1000, "purchase_currency": "RUB"}), 201)
    b = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"ST-{t}-2", "name_ru": "Ремень", "supplier_id": sup["id"],
                                                                   "purchase_price": 500, "purchase_currency": "RUB"}), 201)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"ST-{t}-1\t\t4\nST-{t}-2\t\t3"}), 201)
    L = {l["no"]: l for l in r["lines"]}
    # клиент заказал — строки «под заказ»
    o = _ok(client.post(f"/api/parts/stock/requests/{r['id']}/client-order", headers=H(MGR), json={"line_ids": [L[1]["id"], L[2]["id"]]}))
    assert {l["status"] for l in o["lines"]} == {"ordered"}
    po = _ok(client.post("/api/parts/po", headers=H(MGR), json={"supplier_id": sup["id"], "request_line_ids": [L[1]["id"], L[2]["id"]]}), 201)[0]
    P = {l["no"]: l["id"] for l in po["lines"]}
    rc_body = {"po_id": po["id"], "warehouse_id": wh["id"], "lines": [{"po_line_id": P[1], "qty": 3}]}
    assert client.post("/api/parts/stock/receipts", headers=H(MGR), json=rc_body).status_code == 409        # заказ ещё не отправлен
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(MGR)))
    assert {p["id"] for p in _ok(client.get("/api/parts/stock/receivable", headers=H(LOG)))} >= {po["id"]}
    assert client.post("/api/parts/stock/receipts", headers=H(MGR), json={**rc_body, "warehouse_id": virt["id"]}).status_code == 422
    assert client.post("/api/parts/stock/receipts", headers=H(LOG), json=rc_body).status_code == 403

    # первая приёмка: строка 1 — 3 из 4 (недопоставка), строка 2 — 1 годный + 1 брак из 3
    rc = _ok(client.post("/api/parts/stock/receipts", headers=H(MGR), json={**rc_body, "lines": [
        {"po_line_id": P[1], "qty": 3, "cell": "B-2"}, {"po_line_id": P[2], "qty": 1, "qty_bad": 1, "discrepancy": "damaged", "comment": "разорвана упаковка"}]}), 201)
    assert rc["number"].startswith("ПР-")
    kinds = sorted((d["kind"], d["qty"]) for d in rc["deviations"])
    assert kinds == [("damaged", 1), ("short", 1), ("short", 1)]
    cat = _ok(client.get(f"/api/parts/catalog/{a['id']}", headers=H(CD)))
    s = next(x for x in cat["stock"] if x["warehouse_id"] == wh["id"])
    assert (s["qty"], s["reserved"], s["available"]) == (3, 3, 0)                                   # принятое — сразу в резерв под заявку
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert {l["no"]: l["status"] for l in rq["lines"]} == {1: "po", 2: "po"}                          # строки ещё не полностью на складе

    # отгрузка: не больше резерва
    sh = {"warehouse_id": wh["id"], "lines": [{"line_id": L[1]["id"], "qty": 4}]}
    assert client.post(f"/api/parts/stock/requests/{r['id']}/shipments", headers=H(MGR), json=sh).status_code == 409
    x = _ok(client.post(f"/api/parts/stock/requests/{r['id']}/shipments", headers=H(MGR), json={**sh, "lines": [{"line_id": L[1]["id"], "qty": 2}]}), 201)
    assert x["shipment"]["number"].startswith("ОГ-")
    line1 = next(l for l in x["lines"] if l["no"] == 1)
    assert line1["shipped_qty"] == 2 and line1["status"] == "po"
    s = next(x for x in _ok(client.get(f"/api/parts/catalog/{a['id']}", headers=H(CD)))["stock"] if x["warehouse_id"] == wh["id"])
    assert (s["qty"], s["reserved"]) == (1, 1)

    # отклонения: по строке 1 ждём допоставку; по строке 2 брак — претензия, недостающее отменяем
    devs = [d for d in _ok(client.get("/api/parts/stock/deviations", headers=H(MGR))) if d["po_number"] == po["number"]]
    short1 = next(d for d in devs if d["kind"] == "short" and d["line"]["no"] == 1)
    short2 = next(d for d in devs if d["kind"] == "short" and d["line"]["no"] == 2)
    bad = next(d for d in devs if d["kind"] == "damaged")
    _ok(client.put(f"/api/parts/stock/deviations/{short1['id']}", headers=H(MGR), json={"decision": "wait"}))
    assert client.put(f"/api/parts/stock/deviations/{bad['id']}", headers=H(MGR), json={"decision": "cancel"}).status_code == 422
    _ok(client.put(f"/api/parts/stock/deviations/{bad['id']}", headers=H(MGR), json={"decision": "replace", "claim_text": "Замените 1 ремень, брак упаковки"}))
    ph = _ok(client.post(f"/api/parts/stock/deviations/{bad['id']}/photos", headers=H(MGR), files={"file": ("brak.jpg", b"\xff\xd8\xff fake")}))
    assert ph["photos"][0]["name"] == "brak.jpg"
    assert client.get(f"/api/parts/stock/deviations/{bad['id']}/photos/0", headers=H(MGR)).content == b"\xff\xd8\xff fake"
    c = _ok(client.post(f"/api/parts/stock/deviations/{bad['id']}/claim-sent", headers=H(MGR)))
    assert c["status"] == "claim" and c["claim_sent_at"]
    _ok(client.put(f"/api/parts/stock/deviations/{short2['id']}", headers=H(MGR), json={"decision": "cancel"}))
    d = _ok(client.get(f"/api/parts/po/{po['id']}", headers=H(CD)))
    pl2 = next(l for l in d["lines"] if l["no"] == 2)
    assert (pl2["qty"], pl2["status"]) == (1, "received")

    # допоставка строки 1 закрывает «ждать»
    _ok(client.post("/api/parts/stock/receipts", headers=H(MGR), json={**rc_body, "lines": [{"po_line_id": P[1], "qty": 1}]}), 201)
    assert _ok(client.get("/api/parts/stock/deviations?open_only=false", headers=H(MGR))) and \
        next(x for x in _ok(client.get("/api/parts/stock/deviations?open_only=false", headers=H(MGR))) if x["id"] == short1["id"])["status"] == "closed"
    rq = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(MGR)))
    assert next(l for l in rq["lines"] if l["no"] == 1)["status"] == "in_stock"
    sp = {x["no"]: x for x in _ok(client.get(f"/api/parts/stock/requests/{r['id']}/shippable", headers=H(MGR)))}
    assert sp[1]["reserved"][0]["qty"] == 2 and sp[2]["reserved"][0]["qty"] == 1
    x = _ok(client.post(f"/api/parts/stock/requests/{r['id']}/shipments", headers=H(MGR), json={"warehouse_id": wh["id"], "lines": [
        {"line_id": L[1]["id"], "qty": 2}, {"line_id": L[2]["id"], "qty": 1}]}), 201)
    st = {l["no"]: (l["status"], l["shipped_qty"]) for l in x["lines"]}
    assert st[1] == ("shipped", 4) and st[2] == ("in_stock", 1)                                       # строке 2 не хватает двух — дозаказ
    mv = _ok(client.get(f"/api/parts/stock/moves?part_id={a['id']}", headers=H(LOG)))
    assert sorted((m["kind"], m["qty"]) for m in mv) == [("receipt", 1), ("receipt", 3), ("shipment", -2), ("shipment", -2)]


def test_stock_client_order_hard_reserve(client):
    t = uuid.uuid4().hex[:5].upper()
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    wh = _ok(client.post("/api/parts/warehouses", headers=H(CD), json={"name": f"Склад К {t}", "legal_entity_id": ents["ru"]}), 201)
    sup = _ok(client.post("/api/refs/factories", headers=H(CD), json={"name": f"STK {t}", "country": "RU", "default_currency": "RUB"}), 201)
    p = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"STK-{t}", "name_ru": "Втулка", "supplier_id": sup["id"],
                                                                   "purchase_price": 300, "purchase_currency": "RUB"}), 201)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    # остаток на складе — через приёмку заказа на пополнение
    po = _ok(client.post("/api/parts/po", headers=H(CD), json={"supplier_id": sup["id"], "stock_lines": [{"part_id": p["id"], "qty": 5}]}), 201)[0]
    _ok(client.post(f"/api/parts/po/{po['id']}/mark-sent", headers=H(CD)))
    rc = _ok(client.post("/api/parts/stock/receipts", headers=H(CD), json={"po_id": po["id"], "warehouse_id": wh["id"],
                                                                           "lines": [{"po_line_id": po["lines"][0]["id"], "qty": 5}]}), 201)
    assert rc["deviations"] == []
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "lines_text": f"STK-{t}\t\t2"}), 201)
    line = r["lines"][0]
    assert line["source"] == "stock"
    kp = _ok(client.post(f"/api/parts/requests/{r['id']}/kp", headers=H(MGR), json={"line_ids": [line["id"]], "currency": "RUB"}), 201)
    assert kp
    o = _ok(client.post(f"/api/parts/stock/requests/{r['id']}/client-order", headers=H(MGR), json={"line_ids": [line["id"]]}))
    assert o["reserve_short"] == [] and o["lines"][0]["status"] == "ordered"
    sp = _ok(client.get(f"/api/parts/stock/requests/{r['id']}/shippable", headers=H(MGR)))
    assert sp[0]["reserved"] == [{"warehouse_id": wh["id"], "warehouse": wh["name"], "qty": 2}]
    x = _ok(client.post(f"/api/parts/stock/requests/{r['id']}/shipments", headers=H(MGR), json={"warehouse_id": wh["id"], "lines": [
        {"line_id": line["id"], "qty": 2}]}), 201)
    assert x["lines"][0]["status"] == "shipped"
    s = next(x for x in _ok(client.get(f"/api/parts/catalog/{p['id']}", headers=H(CD)))["stock"] if x["warehouse_id"] == wh["id"])
    assert (s["qty"], s["reserved"]) == (3, 0)
