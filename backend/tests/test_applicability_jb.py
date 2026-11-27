"""J-b (D-196): применимость — статусы, основание, диапазон серийных номеров, сохранение проверки, подбор к машине."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from app.services.applicability import serial_in_range


def test_serial_range():
    assert serial_in_range("BH-1000", "BH-0990", "BH-1100") is True
    assert serial_in_range("BH-099", "BH-100", "") is False           # 99 < 100, хотя строкой «099» > «100» нет
    assert serial_in_range("BH-1000", "BH-099", "") is True             # 1000 > 99, хотя строкой меньше
    assert serial_in_range("BH-1201", "", "BH-1200") is False
    assert serial_in_range("", "BH-1", "BH-9") is None                  # номер машины не известен — «проверьте»
    assert serial_in_range("XX-5", "BH-1", "BH-9") is None              # другой префикс — сравнить нельзя
    assert serial_in_range("anything", "", "") is True
    assert serial_in_range(" bh 1050 ", "BH1000", "BH1100") is True     # регистр и пробелы не важны


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
MGR, CD, LOG, SVC, ENG = "manager@example.com", "cd@example.com", "logist@example.com", "service@example.com", "engineer@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def _part(c, t, suffix, model, **app):
    return _ok(c.post("/api/parts/catalog", headers=H(CD), json={
        "article": f"JB-{t}-{suffix}", "name_ru": f"Деталь {suffix}", "purchase_price": 100, "purchase_currency": "RUB",
        "applicability": [{"other_manufacturer": "Sandvik", "other_model": model, "assembly": "Гидросистема", **app}]}), 201)


def test_status_basis_and_save_keeps_verification(client):
    t = uuid.uuid4().hex[:5].upper()
    p = _part(client, t, "A", f"LH-{t}")
    a = p["applicability"][0]
    assert a["status"] == "unverified" and a["status_title"] == "Не подтверждено"       # решение 7: по умолчанию
    # «подтверждено» без основания — нельзя
    assert client.post(f"/api/parts/applicability/{a['id']}/verify", headers=H(CD), json={"status": "confirmed"}).status_code == 422
    assert client.post(f"/api/parts/applicability/{a['id']}/verify", headers=H(LOG), json={"status": "probable"}).status_code == 403
    v = _ok(client.post(f"/api/parts/applicability/{a['id']}/verify", headers=H(CD),
                        json={"status": "confirmed", "basis_kind": "manual", "basis": "Каталог Sandvik, стр. 112", "serial_from": "SN-100"}))
    assert v["status"] == "confirmed" and v["verified_by_name"] and v["verified_at"] and v["serial_from"] == "SN-100"
    # прежний вид сохраняет карточку без полей проверки — проверка не теряется
    card = _ok(client.get(f"/api/parts/catalog/{p['id']}", headers=H(CD)))
    body = {k: card[k] for k in ("article", "name_ru", "purchase_price", "purchase_currency")}
    body["applicability"] = [{"other_manufacturer": "Sandvik", "other_model": f"LH-{t}", "assembly": "Гидросистема"},
                             {"other_manufacturer": "Sandvik", "other_model": f"LH-{t}-B", "assembly": ""}]
    card = _ok(client.put(f"/api/parts/catalog/{p['id']}", headers=H(CD), json=body))
    by = {x["other_model"]: x for x in card["applicability"]}
    assert by[f"LH-{t}"]["status"] == "confirmed" and by[f"LH-{t}"]["basis"] == "Каталог Sandvik, стр. 112" and by[f"LH-{t}"]["id"] == a["id"]
    assert by[f"LH-{t}-B"]["status"] == "unverified"
    # новый вид меняет статус в карточке — с основанием, иначе 422
    body["applicability"][1].update({"id": by[f"LH-{t}-B"]["id"], "status": "not_applicable"})
    assert client.put(f"/api/parts/catalog/{p['id']}", headers=H(CD), json=body).status_code == 422
    body["applicability"][1].update({"basis_kind": "service", "basis": "не встал по посадке"})
    card = _ok(client.put(f"/api/parts/catalog/{p['id']}", headers=H(CD), json=body))
    nb = next(x for x in card["applicability"] if x["other_model"] == f"LH-{t}-B")
    assert nb["status"] == "not_applicable" and nb["verified_by_name"]
    q = _ok(client.get("/api/parts/applicability", headers=H(MGR), params={"status": "", "q": f"JB-{t}"}))
    assert q["counts"]["confirmed"] == 1 and q["counts"]["not_applicable"] == 1 and len(q["items"]) == 2 and q["can_edit"] is True
    assert _ok(client.get("/api/parts/applicability", headers=H(LOG), params={"q": f"JB-{t}"}))["can_edit"] is False


def test_machine_parts_status_and_serial_range(client):
    t = uuid.uuid4().hex[:5].upper()
    model = f"R17-{t}"
    ok = _part(client, t, "OK", model)
    _part(client, t, "UNV", model)
    no = _part(client, t, "NO", model)
    rng = _part(client, t, "RNG", model)
    _ok(client.post(f"/api/parts/applicability/{ok['applicability'][0]['id']}/verify", headers=H(CD),
                    json={"status": "confirmed", "basis_kind": "supplier", "basis": "письмо"}))
    _ok(client.post(f"/api/parts/applicability/{no['applicability'][0]['id']}/verify", headers=H(CD),
                    json={"status": "not_applicable", "basis_kind": "service", "basis": "не подошла"}))
    _ok(client.post(f"/api/parts/applicability/{rng['applicability'][0]['id']}/verify", headers=H(CD),
                    json={"status": "probable", "basis_kind": "analog", "basis": "по аналогии", "serial_from": "SN-2000"}))
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    m = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Sandvik",
                                                                           "other_model": model, "serial": "SN-1500"}), 201)
    parts = _ok(client.get(f"/api/parts/fleet/machines/{m['id']}/parts", headers=H(MGR)))
    arts = [x["article"] for x in parts]
    assert arts == [f"JB-{t}-OK", f"JB-{t}-UNV"]                       # подтверждённые первыми; «не подходит» и вне диапазона — нет
    assert parts[1]["status_title"] == "Не подтверждено"
    # машина без серийного номера: деталь с диапазоном — с пометкой «проверьте номер»
    m2 = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Sandvik",
                                                                            "other_model": model}), 201)
    p2 = {x["article"]: x for x in _ok(client.get(f"/api/parts/fleet/machines/{m2['id']}/parts", headers=H(MGR)))}
    assert p2[f"JB-{t}-RNG"]["serial_check"] is True and p2[f"JB-{t}-RNG"]["serial_range"] == "SN-2000"

    # сервис: запчасти для машины наряда — инженеру, без цен
    from app.db import SessionLocal
    from app.services.service_refs import load_drafts
    with SessionLocal() as db:
        load_drafts(db)
        db.commit()
    req = _ok(client.post("/api/service/requests", headers=H(SVC), json={"client_id": cl["id"], "machine_id": m["id"], "description": "течь"}), 201)
    wo = _ok(client.post("/api/service/orders", headers=H(SVC), json={"request_id": req["id"]}), 201)
    eng = _ok(client.get("/api/auth/me", headers=H(ENG)))["id"]
    _ok(client.post(f"/api/service/orders/{wo['id']}/events", headers=H(SVC),
                    json={"id": str(uuid.uuid4()), "kind": "assign", "base_version": wo["version"], "payload": {"engineer_id": eng}}))
    op = _ok(client.get(f"/api/service/orders/{wo['id']}/parts", headers=H(ENG)))
    assert [x["article"] for x in op["items"]] == arts and "sale_price" not in op["items"][0]
