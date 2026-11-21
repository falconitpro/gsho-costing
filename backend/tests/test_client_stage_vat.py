"""D-120: платежи клиента в этапах исполнения — в валюте договора с НДС; рубли без НДС — справочно; факт — в валюте этапа."""
from tests.test_api_flow import client, _ok, H, MGR, CD  # noqa: F401
from tests.test_negotiation import _approved


def test_client_stages_gross_in_contract_currency(client):
    did, vid, _ = _approved(client)
    v = next(x for x in _ok(client.get(f"/api/deals/{did}", headers=H(CD)))["variants"] if x["id"] == vid)
    _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["a@example.com"]}))
    _ok(client.post(f"/api/deals/{did}/outcome", headers=H(CD), json={"outcome": "won", "reason": "цена"}))
    st = _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(CD)))["stages"]
    pay = [s for s in st if s["code"] in ("client_advance", "client_final")]
    assert len(pay) == 2 and all(s["currency"] == v["price_currency"] and s["with_vat"] for s in pay)
    assert abs(sum(s["amount"] for s in pay) - v["price_amount"] * 1.22) < 1
    for s in pay:   # справочно ₽ без НДС — по прогнозу курса на плановую дату оплаты
        assert s["ref_rate"] > 0 and abs(s["amount_rub_net"] - s["amount"] / 1.22 * s["ref_rate"]) < s["amount"] * 1e-4 + 1
    # без права видеть деньги — сумм нет
    assert all("amount_rub_net" not in s for s in _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H("logist@example.com")))["stages"])
    # факт вносится в валюте этапа (с НДС) и после этого этап не пересчитывается
    adv = next(s for s in pay if s["code"] == "client_advance")
    _ok(client.post(f"/api/deals/{did}/execution/stages/{adv['id']}/actual", headers=H("fd@example.com"),
                    json={"actual_on": __import__("datetime").date.today().isoformat(), "actual_amount": adv["amount"], "note": ""}))
    adv2 = next(s for s in _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(CD)))["stages"] if s["code"] == "client_advance")
    assert adv2["actual_amount"] == adv["amount"] and adv2["currency"] == adv["currency"]
