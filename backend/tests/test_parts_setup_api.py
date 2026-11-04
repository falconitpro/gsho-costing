"""Запчасти, шаг 1 — API на живой PostgreSQL: компании группы, маршруты, политика наценок с визами ЗГД и ФД.

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
MGR, CD, FD, GD, LOG = "manager@example.com", "cd@example.com", "fd@example.com", "gd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_entities_and_routes(client):
    ents = _ok(client.get("/api/parts/entities", headers=H(MGR)))
    codes = {e["code"]: e for e in ents}
    assert {"ru", "kg", "cn"} <= set(codes)
    assert codes["ru"]["sells_to_clients"] and codes["kg"]["warehouse"] == "virtual" and codes["cn"]["warehouse"] == "none"
    # компании группы правят КД, ЗГД и администратор
    body = {"code": "xx", "name": "Тест", "currency": "RUB", "warehouse": "none"}
    assert client.post("/api/parts/entities", headers=H(MGR), json=body).status_code == 403
    assert client.post("/api/parts/entities", headers=H(CD), json={**body, "code": "ru"}).status_code == 409
    assert client.post("/api/parts/entities", headers=H(CD), json={**body, "warehouse": "big"}).status_code == 422

    routes = _ok(client.get("/api/parts/routes", headers=H(MGR)))
    names = {r["name"]: r for r in routes}
    via_kg = names["Поставщик КНР → компания в Китае → Киргизия → РФ"]
    assert via_kg["chain"] == "Поставщик → Компания в Китае → Компания в Киргизии → Компания в РФ"
    assert [s["incoterms"] for s in via_kg["steps"]] == ["EXW", "FCA", "DAP"] and via_kg["steps"][1]["customs_country"] == "KG"

    # маршрут с разрывом цепочки не сохраняется; логист может вести маршруты
    name = f"Тестовый маршрут {uuid.uuid4().hex[:6]}"
    bad = {"name": name, "steps": [{"buyer_entity_id": codes["kg"]["id"], "currency": "EUR"},
                                   {"seller_entity_id": codes["cn"]["id"], "buyer_entity_id": codes["ru"]["id"], "currency": "EUR"}]}
    r = client.post("/api/parts/routes", headers=H(LOG), json=bad)
    assert r.status_code == 422 and "продавец должен быть покупателем" in r.text
    good = {**bad, "steps": [bad["steps"][0], {**bad["steps"][1], "seller_entity_id": codes["kg"]["id"], "incoterms": "DAP"}]}
    created = _ok(client.post("/api/parts/routes", headers=H(LOG), json=good), 201)
    assert len(created["steps"]) == 2 and created["steps"][0]["seller"] == "Поставщик"
    upd = _ok(client.put(f"/api/parts/routes/{created['id']}", headers=H(CD), json={**good, "is_active": False}))
    assert upd["is_active"] is False
    assert created["id"] not in {r["id"] for r in _ok(client.get("/api/parts/routes", headers=H(CD)))}
    assert client.post("/api/parts/routes", headers=H(MGR), json=good).status_code == 403


def test_markup_policy_two_visas_and_simulator(client):
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    route = next(r for r in _ok(client.get("/api/parts/routes", headers=H(CD))) if r["name"] == "Поставщик КНР → компания в Китае → Киргизия → РФ")
    assert client.get("/api/parts/markup-policies", headers=H(MGR)).status_code == 403   # наценки — КД, ФД, ЗГД

    rules = {"default": {ents["cn"]: 5, ents["kg"]: 3}, "min_margin_pct": 1}
    # симулятор по черновику прямо из формы
    sim = _ok(client.post("/api/parts/markup-policies/simulate", headers=H(CD),
                          json={"route_id": route["id"], "purchase_price": 1000, "rules": rules, "client_price_rub": 30000, "fx": {"CNY": 12}}))
    assert [s["price"] for s in sim["steps"]] == [1000, 1050, 1081.5]
    assert [c["name"] for c in sim["companies"]] == ["Компания в Китае", "Компания в Киргизии"]
    assert sim["group_margin_rub"] == 30000 - 12000 and sim["warnings"] == []

    p = _ok(client.post("/api/parts/markup-policies", headers=H(CD), json={"rules": rules, "reason": "стартовые ставки"}), 201)
    assert p["status"] == "draft" and p["missing_approvals"] == ["ЗГД", "ФД"]
    assert client.post(f"/api/parts/markup-policies/{p['id']}/approve", headers=H(FD), json={}).status_code == 409   # сначала — на утверждение
    _ok(client.post(f"/api/parts/markup-policies/{p['id']}/submit", headers=H(CD)))
    assert client.post(f"/api/parts/markup-policies/{p['id']}/approve", headers=H(CD), json={}).status_code == 403   # КД визу не ставит
    p = _ok(client.post(f"/api/parts/markup-policies/{p['id']}/approve", headers=H(FD), json={"comment": "согласен"}))
    assert p["status"] == "on_approval" and p["missing_approvals"] == ["ЗГД"]
    assert client.post(f"/api/parts/markup-policies/{p['id']}/approve", headers=H(FD), json={}).status_code == 409   # второй раз — нельзя
    assert client.post(f"/api/parts/markup-policies/{p['id']}/reject", headers=H(GD), json={"comment": " "}).status_code == 422
    p = _ok(client.post(f"/api/parts/markup-policies/{p['id']}/approve", headers=H(GD), json={}))
    assert p["status"] == "approved" and p["valid_from"] and p["missing_approvals"] == []
    lst = _ok(client.get("/api/parts/markup-policies", headers=H(FD)))
    assert lst["current_id"] == p["id"]
    assert client.put(f"/api/parts/markup-policies/{p['id']}", headers=H(CD), json={"rules": rules}).status_code == 409

    # симулятор по действующей версии
    sim = _ok(client.post("/api/parts/markup-policies/simulate", headers=H(GD), json={"route_id": route["id"], "purchase_price": 1000, "fx": {"CNY": 12}}))
    assert sim["policy_version"] == p["version_no"] and sim["steps"][-1]["price"] == 1081.5

    # новая версия: отказ ФД с причиной возвращает черновик автору; правка снимает визы
    assert client.post("/api/parts/markup-policies", headers=H(GD), json={"rules": {"default": {ents["kg"]: 400}}}).status_code == 422
    p2 = _ok(client.post("/api/parts/markup-policies", headers=H(GD), json={"rules": {"default": {ents["kg"]: 4}}}), 201)
    _ok(client.post(f"/api/parts/markup-policies/{p2['id']}/submit", headers=H(GD)))
    p2 = _ok(client.post(f"/api/parts/markup-policies/{p2['id']}/approve", headers=H(GD), json={}))
    p2 = _ok(client.put(f"/api/parts/markup-policies/{p2['id']}", headers=H(GD), json={"rules": {"default": {ents["kg"]: 4.5}}}))
    assert p2["status"] == "draft" and p2["approvals"] == {}
    _ok(client.post(f"/api/parts/markup-policies/{p2['id']}/submit", headers=H(GD)))
    p2 = _ok(client.post(f"/api/parts/markup-policies/{p2['id']}/reject", headers=H(FD), json={"comment": "слишком высоко"}))
    assert p2["status"] == "rejected" and p2["rejection"]["comment"] == "слишком высоко"
    assert _ok(client.get("/api/parts/markup-policies", headers=H(CD)))["current_id"] == p["id"]


def test_group_company_as_supplier(client):
    ents = {e["code"]: e["id"] for e in _ok(client.get("/api/parts/entities", headers=H(CD)))}
    base = {"name": f"Компания группы КНР {uuid.uuid4().hex[:6]}", "country": "CN", "supplier_status": "group_company", "language": "ru"}
    assert client.post("/api/refs/factories", headers=H(CD), json=base).status_code == 422          # не выбрана компания группы
    f = _ok(client.post("/api/refs/factories", headers=H(CD), json={**base, "legal_entity_id": ents["cn"]}), 201)
    assert f["supplier_status"] == "group_company" and f["legal_entity_id"] == ents["cn"] and f["language"] == "ru"
    trader = _ok(client.post("/api/refs/factories", headers=H(CD), json={**base, "name": base["name"] + " трейдер", "supplier_status": "trader",
                                                                          "legal_entity_id": ents["cn"]}), 201)
    assert trader["legal_entity_id"] is None                                                         # привязка — только у компании группы
