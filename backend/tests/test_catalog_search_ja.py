"""J-a (D-197): единый поиск Каталога — нормализованные номера, причина совпадения, порядок, серийные номера по правам."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from app.services.catalog_search import norm


def test_norm():
    assert norm("bh-0001") == norm("BH 0001") == norm("BH.0001") == "BH0001"
    assert norm("ФК-12/3") == "ФК123"


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
MGR, CD, ENG, SVC = "manager@example.com", "cd@example.com", "engineer@example.com", "service@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_search_normalized_reason_rank_and_rights(client):
    t = uuid.uuid4().hex[:6].upper()
    exact = _ok(client.post("/api/parts/catalog", headers=H(CD), json={
        "article": f"QS-{t}", "name_ru": "Фильтр поиска", "oem_number": f"OEM.{t}.77", "purchase_price": 1, "purchase_currency": "RUB",
        "cross_refs": [{"kind": "supplier", "number": f"X {t} 99"}]}), 201)
    longer = _ok(client.post("/api/parts/catalog", headers=H(CD), json={
        "article": f"QS-{t}-A", "name_ru": "Фильтр поиска длинный", "purchase_price": 1, "purchase_currency": "RUB"}), 201)
    r = _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": f"qs {t}"}))
    ids = [p["id"] for p in r["parts"]]
    assert ids[:2] == [exact["id"], longer["id"]] and r["parts"][0]["exact"] is True and r["parts"][0]["match"] == f"артикул: QS-{t}"
    oem = _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": f"oem{t}77"}))
    assert oem["parts"][0]["id"] == exact["id"] and oem["parts"][0]["match"].startswith("OEM")
    cross = _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": f"x-{t}-99"}))
    assert cross["parts"][0]["id"] == exact["id"] and cross["parts"][0]["match"] == f"кросс-номер: X {t} 99"
    name = _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": "фильтр поиска длин"}))
    assert any(p["id"] == longer["id"] and p["match"] == "название" for p in name["parts"])
    # серийный номер машины — только с доступом к парку
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    m = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Sandvik",
                                                                           "other_model": f"LH-{t}", "serial": f"SN {t}/01"}), 201)
    found = _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": f"sn-{t}-01"}))
    assert found["machines"][0]["id"] == m["id"] and found["machines"][0]["exact"] and found["machines"][0]["client_name"]
    assert _ok(client.get("/api/catalog/search", headers=H(ENG), params={"q": f"sn-{t}-01"}))["machines"] == []
    assert _ok(client.get("/api/catalog/search", headers=H(MGR), params={"q": "q"}))["parts"] == []   # короче 2 символов


def test_service_head_verifies_applicability(client):
    t = uuid.uuid4().hex[:5].upper()
    p = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"SH-{t}", "name_ru": "Деталь", "purchase_price": 1, "purchase_currency": "RUB",
                                                                   "applicability": [{"other_manufacturer": "Sandvik", "other_model": f"LH-{t}"}]}), 201)
    aid = p["applicability"][0]["id"]
    assert _ok(client.get("/api/parts/applicability", headers=H(SVC), params={"q": f"SH-{t}"}))["can_edit"] is True
    v = _ok(client.post(f"/api/parts/applicability/{aid}/verify", headers=H(SVC), json={"status": "confirmed", "basis_kind": "service", "basis": "ставили на LH"}))
    assert v["status"] == "confirmed"
    # карточку детали руководитель сервиса по-прежнему не правит
    assert client.put(f"/api/parts/catalog/{p['id']}", headers=H(SVC), json={"article": f"SH-{t}", "name_ru": "x"}).status_code == 403
