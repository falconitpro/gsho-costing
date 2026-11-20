"""D-122: рублёвые расходы логиста «с НДС / без НДС» — в себестоимость без НДС, входящий НДС — в денежном потоке к вычету."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401


def test_rub_items_with_vat_go_to_cost_net(client):
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    fac = _ok(client.get("/api/refs/factories", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "НДС логиста"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "VAT-L", "machine_type": "ПДМ", "qty": 1,
                                                                             "unit_price": 1500000, "currency": "CNY"}), 201)
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    items = {"door_to_door": {"amount": 100000, "currency": "CNY"}, "broker_rub": 122000, "vat_included": {"broker_rub": True, "intl_freight": True},
             "comments": {"door_to_door": "x", "broker_rub": "x"}}
    b = _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}), 201)
    assert b["items"]["vat_included"] == {"broker_rub": True}                      # только рублёвые статьи
    assert b["items"]["checks"]["broker_rub"]["value"] == 100000                     # история и проверка — без НДС
    lg = _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={
        "hs_code": "8429519000", "util_applies": False, "certification_rub": 61000, "vat_included": {"certification_rub": True},
        "comments": {"certification_rub": "x"}}))["logistics"]
    assert lg["vat_included"] == {"certification_rub": True}
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 20}))
    base = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))["result"]["variants"][var["id"]]["base"]
    assert abs(base["items_rub"]["broker_util"] - 100000) < 0.01 and abs(base["items_rub"]["certification"] - 50000) < 0.01
    v = base["vat"]
    assert abs(v["input_rub"] - 33000) < 0.01                                       # 22 000 + 11 000 — к вычету
    imp = sum(x["amount_rub"] for x in v["import"])
    assert abs(v["from_client_rub"] - imp - v["input_rub"] - sum(q["amount_rub"] for q in v["budget"])) < 0.05
