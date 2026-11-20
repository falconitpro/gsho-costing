"""«Действующее предложение» (D-103): цена, которую видит клиент, и сверка мест хранения цены. Только чтение."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401
from tests.test_negotiation import _approved


def _offer(client, did, who=CD):
    return _ok(client.get(f"/api/deals/{did}/current-offer", headers=H(who)))


def test_after_approval_price_from_version(client):
    did, vid, _ = _approved(client, currency="CNY")
    assert client.get(f"/api/deals/{did}/current-offer", headers=H(LOG)).status_code == 403
    o = _offer(client, did)
    assert o["approved"] and o["issues_count"] == 0
    v = o["variants"][0]
    assert v["id"] == vid and v["source"]["kind"] == "version" and v["currency"] == "CNY" and v["rate"]
    assert v["price"]["net_rub"] == v["first"]["net_rub"] and v["change_pct"] == 0
    assert abs(v["price"]["net"] * v["rate"] - v["price"]["net_rub"]) < 1
    assert abs(v["price"]["unit_net"] * v["units"] - v["price"]["net"]) < 0.05
    assert v["price"]["gross_rub"] > v["price"]["net_rub"]
    keys = {p["key"]: p["price_rub"] for p in v["places"]}
    assert set(keys) == {"variant", "version"} and abs(keys["variant"] - keys["version"]) < 1


def test_after_step_and_manual_revision(client):
    did, vid, _ = _approved(client)
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 5}}, "reason": "client"}), 201)
    v = _offer(client, did)["variants"][0]
    assert v["source"]["kind"] == "revision" and v["negotiated"]
    assert abs(v["price"]["net_rub"] - s["price_rub"]) < 1 and abs(v["change_pct"] + 5) < 0.01
    keys = {p["key"]: p["price_rub"] for p in v["places"]}
    assert abs(keys["step"] - keys["revision"]) < 1 and not [i for i in v["issues"] if i["level"] != "info"]

    _ok(client.post(f"/api/deals/{did}/kp/revisions", headers=H(CD), json={"changes": {vid: {"extras": [{"name": "Обучение", "price_rub": 100000}]}},
                                                                         "description": "добавлено обучение"}), 201)
    v = _offer(client, did)["variants"][0]
    keys = {p["key"]: p["price_rub"] for p in v["places"]}
    assert abs(keys["revision"] - (s["price_rub"] + 100000)) < 1 and abs(keys["calc"] - s["price_rub"]) < 1
    texts = " | ".join(i["text"] for i in v["issues"])
    assert "правили вручную" in texts and "услуги" in texts


def test_price_in_offer_differs_from_approved_version(client):
    """После изменения условий контракта версия несёт цену торгов, а цена КД в предложении — первую: действует цена версии."""
    from app.db import SessionLocal
    from app.models import Variant
    did, vid, _ = _approved(client)
    first = _offer(client, did)["variants"][0]["price"]["net_rub"]
    with SessionLocal() as db:
        v = db.get(Variant, vid)
        v.price_rub = round(first * 1.05, 2)
        if v.price_amount is not None:
            v.price_amount = round(v.price_amount * 1.05, 2)
        db.commit()
    o = _offer(client, did)
    v = o["variants"][0]
    assert abs(v["price"]["net_rub"] - first) < 1 and v["source"]["kind"] == "version"
    assert o["issues_count"] == 1 and v["issues"][0]["level"] == "error"


def test_contract_change_keeps_negotiated_price(client):
    """D-104: при изменении условий контракта цена после торгов переходит в предложение и в новую версию."""
    did, vid, _ = _approved(client)
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 5}}, "reason": "client"}), 201)
    _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["a@b.example"]}))
    assert _ok(client.post(f"/api/deals/{did}/outcome", headers=H(CD), json={"outcome": "won", "reason": "цена"}))["status"] == "contract"
    assert _ok(client.post(f"/api/deals/{did}/execution/terms-changed", headers=H(CD), json={"reason": "аванс 40 %"}))["status"] == "cd_approval"
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    assert abs(calc["result"]["variants"][vid]["base"]["price_rub"] - s["price_rub"]) < 1      # повторное утверждение — от цены торгов
    v = _offer(client, did)["variants"][0]
    assert abs(v["price"]["net_rub"] - s["price_rub"]) < 1 and v["source"]["kind"] == "version"
    assert _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))["status"] == "contract"
    o = _offer(client, did)
    v = o["variants"][0]
    assert abs(v["price"]["net_rub"] - s["price_rub"]) < 1 and o["issues_count"] == 0
    assert {p["key"] for p in v["places"]} == {"variant", "version"}
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    assert abs(calc["result"]["variants"][vid]["base"]["price_rub"] - s["price_rub"]) < 1


def test_repair_existing_contract_change(client):
    """Разовое исправление: версия по изменению условий утверждена, в предложении первая цена → цена версии."""
    from app.db import SessionLocal
    from app.models import Deal, DealVersion, Variant
    from app.services.contract_price import repair_existing
    did, vid, _ = _approved(client)
    with SessionLocal() as db:
        deal = db.get(Deal, did)
        db.get(DealVersion, deal.approved_version_id).reason = "contract_change"
        v = db.get(Variant, vid)
        good = v.price_rub
        v.price_rub = round(good * 1.05, 2)
        db.commit()
        assert repair_existing(db) >= 1
        db.refresh(v)
        assert abs(v.price_rub - good) < 0.01
        assert repair_existing(db) == 0 or abs(db.get(Variant, vid).price_rub - good) < 0.01
