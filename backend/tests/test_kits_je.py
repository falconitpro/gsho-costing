"""J-e (D-201): комплекты ТО — работы из справочника сервиса, узлы строк, диапазон заводских номеров; комплект в наряде."""
import uuid

from tests.test_api_flow import client, _ok, H, SVC, MGR  # noqa: F401
from tests.test_service_core import ENG, _assign_with_permits, _ev, _refs  # noqa: F401

CD = "cd@example.com"


def test_kit_works_serial_and_order(client):  # noqa: F811
    t = uuid.uuid4().hex[:5].upper()
    model = f"LH-{t}"
    part = _ok(client.post("/api/parts/catalog", headers=H(CD), json={"article": f"KT-{t}", "name_ru": "Фильтр масляный", "purchase_price": 1,
                                                                      "purchase_currency": "RUB", "sale_price": 1200, "sale_currency": "RUB"}), 201)
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    m_in = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Sandvik", "other_model": model, "serial": "SN-150"}), 201)
    m_out = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": cl["id"], "other_manufacturer": "Sandvik", "other_model": model, "serial": "SN-900"}), 201)
    refs = _ok(client.get("/api/service/refs", headers=H(SVC)))
    to250 = next(x for x in refs["work_types"] if x["code"] == "TO-250")
    asm = next(a for a in _ok(client.get("/api/parts/assemblies", headers=H(CD)))["items"] if a["name_ru"] == "Двигатель")
    body = {"other_model_id": m_in["other_model_id"], "name": "ТО-250", "interval_hours": 250,
            "lines": [{"article": f"KT-{t}", "qty": 2, "assembly_id": asm["id"]}, {"article": f"NO-{t}", "qty": 1}],
            "works": [{"work_type_id": to250["id"]}], "serial_from": "SN-100", "serial_to": "SN-500"}
    assert client.post("/api/parts/fleet/kits", headers=H(CD), json={**body, "works": [{"work_type_id": "nope"}]}).status_code == 422
    k = _ok(client.post("/api/parts/fleet/kits", headers=H(CD), json=body), 201)
    assert k["works"][0]["code"] == "TO-250" and k["works"][0]["norm_hours"] == to250["norm_hours"]
    assert k["lines"][0]["assembly"] == "Двигатель" and k["serial_from"] == "SN-100"
    # прежний вид (без works / serial) — не стирает их
    k2 = _ok(client.put(f"/api/parts/fleet/kits/{k['id']}", headers=H(CD), json={"name": "ТО-250", "interval_hours": 250, "lines": [{"article": f"KT-{t}", "qty": 3}]}))
    assert k2["works"] and k2["serial_to"] == "SN-500" and k2["lines"][0]["qty"] == 3
    # карточка машины: комплект только у машины в диапазоне номеров
    assert [x["id"] for x in _ok(client.get(f"/api/parts/fleet/machines/{m_in['id']}", headers=H(MGR)))["kits"]] == [k["id"]]
    assert _ok(client.get(f"/api/parts/fleet/machines/{m_out['id']}", headers=H(MGR)))["kits"] == []
    # наряд по машине: комплект виден инженеру; запчасти — в смету руководителем
    req = _ok(client.post("/api/service/requests", headers=H(SVC), json={"client_id": cl["id"], "machine_id": m_in["id"], "description": "ТО", "kind": "maintenance"}), 201)
    wo = _ok(client.post("/api/service/orders", headers=H(SVC), json={"request_id": req["id"]}), 201)
    wo = _assign_with_permits(client, wo)
    kits = _ok(client.get(f"/api/service/orders/{wo['id']}/kits", headers=H(ENG)))["items"]
    assert [x["name"] for x in kits] == ["ТО-250"] and kits[0]["works"][0]["code"] == "TO-250"
    wo = _ok(_ev(client, wo, "work_add", payload={"work_type_id": to250["id"]}))["order"]
    est = _ok(client.post(f"/api/service/orders/{wo['id']}/estimates", headers=H(SVC), json={}), 201)
    assert client.post(f"/api/service/estimates/{est['id']}/add-kit", headers=H(ENG), json={"kit_id": k["id"]}).status_code == 403
    e = _ok(client.post(f"/api/service/estimates/{est['id']}/add-kit", headers=H(SVC), json={"kit_id": k["id"]}))
    parts = [x for x in e["lines"] if x["kind"] in ("part", "other") and "комплект" in x["note"]]
    assert parts[0]["price"] == 1200 and parts[0]["qty"] == 3
    other = _ok(client.post("/api/service/orders", headers=H(SVC), json={"client_id": cl["id"], "machine_id": m_out["id"]}), 201)
    assert _ok(client.get(f"/api/service/orders/{other['id']}/kits", headers=H(SVC)))["items"] == []
