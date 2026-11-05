"""Запчасти, шаг 3 — заявки клиентов на живой PostgreSQL: подбор, цена, маржа и визы, разделение строки.

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
from app.services.parts_requests import norm_article, parse_lines_text  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, FD, GD, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "gd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_helpers():
    assert norm_article("a-12.3 4/5") == "A12345"
    assert parse_lines_text("RQ-1\tФильтр\t2\nOEM-7; 3\n\nX1") == [
        {"raw_article": "RQ-1", "raw_name": "Фильтр", "qty": 2.0}, {"raw_article": "OEM-7", "raw_name": "", "qty": 3.0},
        {"raw_article": "X1", "raw_name": "", "qty": 1.0}]


def test_request_flow(client):
    t = uuid.uuid4().hex[:5].upper()
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    route = next(r for r in _ok(client.get("/api/parts/routes", headers=H(CD))) if r["name"] == "Поставщик КНР → компания в Китае → Киргизия → РФ")
    a = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"RQ-{t}-1", "name_ru": "Фильтр топливный", "purchase_price": 100,
                                                                   "purchase_currency": "CNY", "route_id": route["id"]}), 201)
    b = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"RQ-{t}-2", "name_ru": "Ремень", "oem_number": f"OEM-{t}",
                                                                   "purchase_price": 4000, "purchase_currency": "RUB", "sale_price": 50, "sale_currency": "EUR"}), 201)
    c = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"RQ-{t}-3", "name_ru": "Датчик",
                                                                   "cross_refs": [{"kind": "supplier", "number": f"SUP-{t}"}],
                                                                   "client_prices": [{"client_id": cl["id"], "price": 77, "currency": "EUR"}]}), 201)
    text = f"RQ {t}-1\tФильтр\t2\nOEM-{t}\t\t1\nsup-{t}\t\t3\nNO-SUCH-{t}\tНеизвестная деталь\t1"
    assert client.post("/api/parts/requests", headers=H(LOG), json={"client_id": cl["id"], "lines_text": text}).status_code == 403
    r = _ok(client.post("/api/parts/requests", headers=H(MGR), json={"client_id": cl["id"], "machine": "Sandvik LH410", "urgent": True,
                                                                    "lines_text": text}), 201)
    assert r["number"].startswith("ЗЧ-") and len(r["lines"]) == 4
    L = {l["no"]: l for l in r["lines"]}
    assert (L[1]["part"]["id"], L[1]["match_kind"], L[1]["qty"]) == (a["id"], "article", 2)       # «RQ 7A-1» = «RQ-7A-1»
    assert (L[2]["part"]["id"], L[2]["match_kind"]) == (b["id"], "oem")
    assert (L[3]["part"]["id"], L[3]["match_kind"]) == (c["id"], "supplier")
    assert L[4]["part"] is None and L[4]["status"] == "new"
    # цены: расчёт от себестоимости по маршруту / прайс / договорная цена клиента; под заказ — склада нет
    assert L[1]["price_source"] == "calc" and L[1]["currency"] == "RUB" and L[1]["source"] == "order"
    assert L[2]["price_source"] == "pricelist" and L[2]["price"] == 50 and L[2]["currency"] == "EUR"
    assert L[3]["price_source"] == "contract" and L[3]["price"] == 77
    assert "cost" not in L[1] and "margin_pct" not in L[1] and "profit_rub" not in r["summary"]     # менеджер не видит себестоимость и маржу
    rc = _ok(client.get(f"/api/parts/requests/{r['id']}", headers=H(CD)))
    L1 = next(l for l in rc["lines"] if l["no"] == 1)
    assert L1["cost"] and L1["margin_pct"] == L1["threshold_pct"] == 40                              # 2 × себестоимость < 10 000 ₽ → 40 %
    assert rc["summary"]["handling_fee_rub"] >= 0 and rc["status"]["text"] == "КП: 0 из 4 строк"

    # ручная цена ниже порога → виза; КД не может утвердить то, что требует ФД
    l2 = next(l for l in rc["lines"] if l["no"] == 2)
    low = round(l2["cost"] * 1.05 / 100, 2)                                                          # ≈ 4,8 % маржи при пороге 40 %, в EUR (курс 98)
    r = _ok(client.patch(f"/api/parts/requests/{r['id']}/lines/{l2['id']}", headers=H(MGR), json={"price": low, "currency": "EUR"}))
    assert r["summary"]["approval_needed"] == "fd"
    assert client.post(f"/api/parts/requests/{r['id']}/approve", headers=H(CD), json={}).status_code == 403
    assert client.post(f"/api/parts/requests/{r['id']}/approve", headers=H(MGR), json={}).status_code == 403
    r = _ok(client.post(f"/api/parts/requests/{r['id']}/approve", headers=H(FD), json={"comment": "постоянный клиент"}))
    assert r["summary"]["approval_needed"] is None and r["approvals"][0]["level"] == "fd"
    assert client.post(f"/api/parts/requests/{r['id']}/approve", headers=H(FD), json={}).status_code == 409

    # ручной подбор строки без совпадения, разделение строки, отмена
    l4 = next(l for l in r["lines"] if l["no"] == 4)
    r = _ok(client.patch(f"/api/parts/requests/{r['id']}/lines/{l4['id']}", headers=H(MGR), json={"part_id": b["id"]}))
    l4 = next(l for l in r["lines"] if l["no"] == 4)
    assert l4["match_kind"] == "manual" and l4["price_source"] == "pricelist"
    l3 = next(l for l in r["lines"] if l["no"] == 3)
    assert client.post(f"/api/parts/requests/{r['id']}/lines/{l3['id']}/split", headers=H(MGR), json={"qty": 3}).status_code == 422
    r = _ok(client.post(f"/api/parts/requests/{r['id']}/lines/{l3['id']}/split", headers=H(MGR), json={"qty": 1}))
    parts3 = [l for l in r["lines"] if l["id"] == l3["id"] or l["parent_line_id"] == l3["id"]]
    assert sorted(l["qty"] for l in parts3) == [1, 2] and len(r["lines"]) == 5
    r = _ok(client.patch(f"/api/parts/requests/{r['id']}/lines/{l4['id']}", headers=H(MGR), json={"status": "cancelled"}))
    assert r["summary"]["lines"] == 4

    # чужой менеджер заявку не видит; закрытие с причиной
    assert any(x["id"] == r["id"] for x in _ok(client.get("/api/parts/requests", headers=H(MGR))))
    assert client.post(f"/api/parts/requests/{r['id']}/close", headers=H(MGR), json={"closed": "lost"}).status_code == 422
    r = _ok(client.post(f"/api/parts/requests/{r['id']}/close", headers=H(MGR), json={"closed": "lost", "reason": "дорого"}))
    assert r["status_text"] == "проиграна"
    assert r["id"] not in {x["id"] for x in _ok(client.get("/api/parts/requests", headers=H(CD)))}
    assert r["id"] in {x["id"] for x in _ok(client.get("/api/parts/requests?open_only=false", headers=H(CD)))}
