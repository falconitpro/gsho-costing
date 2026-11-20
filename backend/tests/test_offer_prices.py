"""D-109: цены предложения в одном месте — запись при каждом назначении цены и редакции КП, чтение одной функцией."""
from tests.test_api_flow import client, _ok, H, CD  # noqa: F401
from tests.test_negotiation import _approved


def _rows(did):
    from app.db import SessionLocal
    from app.models import OfferPrice
    with SessionLocal() as db:
        return [(r.kind, round(r.total_rub, 2), r.extras_rub, r.revision_no, r.currency, r.units, r.unit_amount, r.fx_rate)
                for r in db.query(OfferPrice).filter(OfferPrice.deal_id == did).order_by(OfferPrice.created_at, OfferPrice.seq)]


def test_records_and_current(client):
    did, vid, _ = _approved(client, currency="CNY")
    rows = _rows(did)
    assert rows and rows[-1][0] == "kd" and rows[-1][4] == "CNY"
    kd = rows[-1]
    assert abs(kd[6] * kd[7] * kd[5] - kd[1]) < 1                          # за 1 машину в валюте × курс × машин = итог в рублях
    s = _ok(client.post(f"/api/deals/{did}/negotiation/steps", headers=H(CD), json={"prices": {vid: {"mode": "pct", "value": 5}}, "reason": "client"}), 201)
    rows = _rows(did)
    assert rows[-1][0] == "revision" and abs(rows[-1][1] - s["price_rub"]) < 1 and rows[-1][3] == 1
    _ok(client.post(f"/api/deals/{did}/kp/revisions", headers=H(CD), json={"changes": {vid: {"extras": [{"name": "Обучение", "price_rub": 100000}]}},
                                                                         "description": "обучение"}), 201)
    rows = _rows(did)
    assert rows[-1][0] == "revision" and rows[-1][2] == 100000 and rows[-1][3] == 2
    from app.db import SessionLocal
    from app.models import Deal
    from app.services import offer_prices
    from app.services.deal_calc import actual_offer, negotiated
    with SessionLocal() as db:
        d = db.get(Deal, did)
        v = next(x for x in d.variants if x.id == vid)
        cur = offer_prices.current(db, d, v)
        assert cur["from_table"] and cur["revision"].revision_no == 2
        assert abs(actual_offer(d, v, db)["price_rub"] - (s["price_rub"] + 100000)) < 1
        assert abs(negotiated(db, d)[vid]["price_rub"] - s["price_rub"]) < 1        # в расчёт — без услуг из КП
    assert len([r for r in _rows(did) if r[0] == "kd"]) == len([r for r in rows if r[0] == "kd"])  # чтение ничего не пишет
