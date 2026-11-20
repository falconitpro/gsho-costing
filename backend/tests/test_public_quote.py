"""D-107: ответ логистической компании по ссылке без входа в CRM."""
from tests.test_api_flow import client, _ok, H, LOG  # noqa: F401
from tests.test_logistics_workspace import _deal


def _links(client, did, emails):
    r = _ok(client.post(f"/api/deals/{did}/logistics/request/send", headers=H(LOG), json={"emails": emails, "manual": True}))
    assert [x["email"] for x in r["links"]] == emails
    return r


def test_answer_by_link(client):
    did, _, _ = _deal(client)
    r = _links(client, did, ["a@carrier.example", "b@carrier.example"])
    tok = r["links"][0]["url"].rsplit("/q/", 1)[1]
    assert tok not in str(_ok(client.get(f"/api/deals/{did}/feed", headers=H(LOG))))           # ссылка не хранится в открытом виде
    v = _ok(client.get(f"/api/public/quote/{tok}"))                                              # без входа
    assert v["no"] == r["no"] and v["services"] and v["answer"] is None
    assert "position_id" not in str(v) and "factory_id" not in str(v) and "unit_price" not in str(v)
    body = {"carrier": "ТК Восток", "contact": "Иван, +7 900", "valid_until": "2027-01-31", "comment": "без погрузки",
            "lines": [{"scope": "intl_freight", "amount": 12000, "currency": "USD", "transit_days": 25},
                      {"scope": "rf_delivery", "amount": 300000, "currency": "RUB"}]}
    assert _ok(client.post(f"/api/public/quote/{tok}", json=body))["replaced"] is False
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    mine = [q for q in ws["quotes"] if q.get("source") == "link"]
    assert {q["scope"] for q in mine} == {"intl_freight", "rf_delivery"} and all(q["carrier"] == "ТК Восток" for q in mine)
    assert "b@carrier.example" not in str(mine)
    notes = _ok(client.get("/api/notifications?kind=all", headers=H(LOG)))
    assert any("ТК Восток" in (n.get("text") or "") for n in (notes.get("items") if isinstance(notes, dict) else notes))
    # повторная отправка заменяет прежний ответ; форма показывает введённое
    body["lines"] = [{"scope": "intl_freight", "amount": 11000, "currency": "USD", "transit_days": 25}]
    assert _ok(client.post(f"/api/public/quote/{tok}", json=body))["replaced"] is True
    ws = _ok(client.get(f"/api/deals/{did}/logistics/workspace", headers=H(LOG)))
    mine = [q for q in ws["quotes"] if q.get("source") == "link"]
    assert len(mine) == 1 and mine[0]["amount"] == 11000
    assert _ok(client.get(f"/api/public/quote/{tok}"))["answer"]["lines"][0]["amount"] == 11000
    # ошибки ввода и чужие ссылки
    assert client.post(f"/api/public/quote/{tok}", json={**body, "lines": []}).status_code == 422
    assert client.post(f"/api/public/quote/{tok}", json={**body, "lines": [{"scope": "x", "amount": 1}]}).status_code == 422
    assert client.get("/api/public/quote/nonexistent-token").status_code == 404


def test_link_closed_after_logistics_done(client):
    from app.db import SessionLocal
    from app.models import Deal
    did, _, _ = _deal(client)
    tok = _links(client, did, ["c@carrier.example"])["links"][0]["url"].rsplit("/q/", 1)[1]
    with SessionLocal() as db:
        db.get(Deal, did).logistics_done = True
        db.commit()
    assert client.get(f"/api/public/quote/{tok}").status_code == 410
