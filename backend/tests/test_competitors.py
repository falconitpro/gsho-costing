"""Цены конкурентов и цена предложения с НДС (D-066)."""
import os

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан", allow_module_level=True)

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD, FD  # noqa: F401,E402

GD = "evgeniy@dobrov.pro"


def _deal(client, site):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": site}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "CMP-1", "machine_type": "ПДМ",
              "qty": 1, "unit_price": 1000000, "currency": "CNY"}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG), json={"no_delivery_cost": True}))
    vid = _ok(client.get(f"/api/deals/{did}", headers=H(CD)))["variants"][0]["id"]
    return did, vid


def test_price_with_vat(client):
    did, vid = _deal(client, "НДС")
    r = _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": vid, "mode": "amount", "value": 24_400_000, "currency": "RUB", "vat_included": True}))
    v = r["variant"]
    assert v["price_rub"] == 20_000_000 and v["price_vat_included"] and v["price_input_amount"] == 24_400_000 and v["price_vat_pct"] == 22.0
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    assert calc["sales_vat_pct"] == 22.0 and calc["contract_currency"]["code"] == calc["deal_currency"]["code"] == v["price_currency"]   # D-080: валюта сделки
    assert v["price_input_currency"] == "RUB" and v["price_input_amount"] == 24_400_000
    # логист цену и признак НДС не видит (новые поля — в той же группе прав, что и цена)
    mv = _ok(client.get(f"/api/deals/{did}", headers=H(LOG)))["variants"][0]
    assert "price_rub" not in mv and "price_vat_included" not in mv and "price_input_amount" not in mv


def test_competitor_offers_history_and_compare(client):
    did, vid = _deal(client, "Конкуренты 1")
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": vid, "mode": "amount", "value": 20_000_000, "currency": "RUB"}))
    url = f"/api/deals/{did}/competitors"
    # только КД и ЗГД
    for who in (MGR, FD, LOG):
        assert client.get(url, headers=H(who)).status_code == 403
        assert client.post(url, headers=H(who), json={"amount": 1}).status_code == 403
    o1 = _ok(client.post(url, headers=H(CD), json={"competitor": "«Альфа Машинери»", "on": "2026-11-10", "equipment": "XCMG 80т", "amount": 26_840_000,
                                                   "currency": "RUB", "vat_included": True, "variant_id": vid, "source": "тендер"}), 201)["id"]
    _ok(client.post(url, headers=H(GD), json={"competitor": "", "on": "2026-11-12", "amount": 18_000_000, "currency": "RUB", "vat_included": False}), 201)
    d = _ok(client.get(url, headers=H(CD)))
    first = next(o for o in d["offers"] if o["id"] == o1)
    assert first["competitor"] == "«Альфа Машинери»" and first["net"] == 22_000_000 and first["rub_net"] == 22_000_000
    assert first["diff_pct"] == round((20 - 22) / 22 * 100, 1)                  # мы дешевле
    assert [o["on"] for o in d["offers"]] == ["2026-11-12", "2026-11-10"]      # хронология
    assert any(o["competitor"] == "" for o in d["offers"]) and d["competitors"] == ["«Альфа Машинери»"]
    # правка и удаление — в истории; удалённая строка остаётся
    _ok(client.patch(f"{url}/{o1}", headers=H(CD), json={"competitor": "альфа  машинери", "on": "2026-11-10", "equipment": "XCMG 80т", "amount": 25_000_000,
                                                          "currency": "RUB", "vat_included": True, "variant_id": vid}))
    assert len(_ok(client.get("/api/competitors", headers=H(CD)))) == 1          # то же название — тот же конкурент
    assert client.delete(f"{url}/{o1}", headers=H(CD)).status_code == 204
    hist = _ok(client.get(f"{url}/{o1}/history", headers=H(GD)))
    assert [h["action"] for h in hist] == ["delete", "update", "create"] and hist[1]["before"]["amount"] == 26_840_000
    assert next(o for o in _ok(client.get(url, headers=H(CD)))["offers"] if o["id"] == o1)["deleted"]
    assert client.patch(f"{url}/{o1}", headers=H(CD), json={"amount": 1}).status_code == 409


def test_competitor_history_across_deals_and_currency(client):
    d1, v1 = _deal(client, "Конкуренты 2")
    d2, v2 = _deal(client, "Конкуренты 3")
    _ok(client.post(f"/api/deals/{d2}/price", headers=H(CD), json={"variant_id": v2, "mode": "amount", "value": 3_000_000, "currency": "CNY"}))
    _ok(client.post(f"/api/deals/{d1}/competitors", headers=H(CD), json={"competitor": "Бета Трак", "on": "2026-10-01", "amount": 30_000_000, "currency": "RUB"}), 201)
    _ok(client.post(f"/api/deals/{d2}/competitors", headers=H(CD), json={"competitor": "бета трак", "on": "2026-11-01", "amount": 400_000, "currency": "USD",
                                                                       "vat_included": False}), 201)
    d = _ok(client.get(f"/api/deals/{d2}/competitors", headers=H(CD)))
    assert d["contract_currency"] == "CNY"                                     # валюта нашего предложения
    o = d["offers"][0]
    assert o["fx_rate"] > 1 and o["rub_net"] == round(400_000 * o["fx_rate"], 2) and o["contract_net"] > 400_000   # USD → CNY справочно
    comp = next(c for c in _ok(client.get("/api/competitors?q=бета", headers=H(CD))) if c["name"] == "Бета Трак")
    assert comp["offers"] == 2
    h = _ok(client.get(f"/api/competitors/{comp['id']}/offers", headers=H(GD)))["offers"]
    assert [x["on"] for x in h] == ["2026-11-01", "2026-10-01"] and h[0]["deal_number"] != h[1]["deal_number"]
    assert client.get(f"/api/competitors/{comp['id']}/offers", headers=H(MGR)).status_code == 403
