"""Логистика в себестоимости: расходы партии не теряются молча (партия без позиций, отправка без стоимости доставки)."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401

HS = {"hs_code": "8429519000", "util_applies": False}
D2D = {"door_to_door": {"amount": 300000, "currency": "CNY"}, "broker_rub": 200000, "comments": {"door_to_door": "x", "broker_rub": "x", "intl_freight": "x"}}


def _deal(client):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Логистика"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "LG-1",
              "machine_type": "ПДМ", "qty": 1, "unit_price": 1000000, "currency": "CNY"}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json=HS))
    return did, pos


def _items(client, did):
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    return next(iter(calc["result"]["variants"].values()))["base"]["items_rub"], calc["warnings"]


def test_batch_without_positions_blocks_complete_and_can_be_fixed(client):
    did, pos = _deal(client)
    b1 = _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": {}, "position_ids": [pos["id"]]}), 201)
    b2 = _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"name": "Партия 2", "items": D2D, "position_ids": []}), 201)
    r = client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG))
    assert r.status_code == 422 and "позиций в партии нет" in r.json()["detail"]["message"]
    # исправление: лишнюю пустую партию удаляем, позицию переносим во вторую
    assert client.delete(f"/api/deals/{did}/logistics/batches/{b1['id']}", headers=H(LOG)).status_code == 204
    _ok(client.patch(f"/api/deals/{did}/logistics/batches/{b2['id']}", headers=H(LOG), json={"name": "Партия 2", "items": D2D, "position_ids": [pos["id"]]}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    items, warnings = _items(client, did)
    assert items["intl_freight"] > 0 and items["rf_delivery"] > 0 and items["broker_util"] == 200000
    assert not any("Партия" in w for w in warnings)


def test_complete_without_delivery_cost_needs_confirmation(client):
    did, _ = _deal(client)
    r = client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG))
    assert r.status_code == 422 and r.json()["detail"]["confirm"] == "no_delivery_cost"
    d = _ok(client.get(f"/api/deals/{did}", headers=H(LOG)))
    assert d["status"] == "logistics_calc" and d["batches"] == []           # ничего не создано молча
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG), json={"no_delivery_cost": True}))
    items, warnings = _items(client, did)
    assert items["intl_freight"] == 0 and any("стоимость доставки не введена" in w for w in warnings)


def test_single_batch_goes_into_cost(client):
    did, pos = _deal(client)
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": D2D, "position_ids": [pos["id"]]}), 201)
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    items, _ = _items(client, did)
    assert items["intl_freight"] > 0 and items["rf_delivery"] > items["intl_freight"] and items["broker_util"] == 200000
