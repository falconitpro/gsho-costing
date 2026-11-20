"""D-111: история логистики — на 1 машину, по сделке без дублей, сравнение только в той же валюте."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401


def _deal(client, qty=4, freight=50000, cur="CNY"):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "История"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "LH-1",
              "machine_type": "ПДМ-история", "qty": qty, "unit_price": 1500000, "currency": "CNY"}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    items = {"intl_freight": {"amount": freight, "currency": cur}, "broker_rub": 100000, "comments": {"intl_freight": "x", "broker_rub": "x"}}
    r = client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"route": "Маршрут-история", "items": items, "position_ids": [pos["id"]]})
    return did, pos, r


def _hist(did):
    from app.db import SessionLocal
    from app.models import LogisticsHistory
    with SessionLocal() as db:
        return sorted((r.item_code, r.amount, r.currency) for r in db.query(LogisticsHistory).filter(LogisticsHistory.deal_id == did))


def test_history_per_unit_and_no_duplicates(client):
    did, pos, r = _deal(client)
    assert r.status_code == 201
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    h = _hist(did)
    assert ("intl_freight", 50000, "CNY") in h and ("broker_rub", 100000, "RUB") in h
    _ok(client.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason": "ещё раз"}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    assert _hist(did) == h                                       # повторное завершение не дублирует записи

    # такая же ставка на 1 машину в другой сделке — без требования комментария, хотя машин в партии другое число
    did2, _, r2 = _deal(client, qty=1, freight=52000)
    assert r2.status_code == 201, r2.text
    # ставка в другой валюте с историей не сравнивается
    did3, _, r3 = _deal(client, qty=1, freight=7000, cur="USD")
    assert r3.status_code == 201, r3.text


def test_rebuild_converts_legacy_totals(client):
    from datetime import date
    from app.db import SessionLocal
    from app.models import Batch, LogisticsHistory, RateSetting
    from app.services import logistics_history as lh
    did, pos, r = _deal(client, qty=4, freight=50000)
    with SessionLocal() as db:
        db.query(RateSetting).filter(RateSetting.key == lh.MARK).delete()
        b = db.query(Batch).filter(Batch.deal_id == did).one()
        # как до D-111: в истории сумма на всю партию (4 машины), записанная дважды
        for _ in range(2):
            db.add(LogisticsHistory(item_code="intl_freight", route=b.route, amount=200000, currency="CNY", on=date(2026, 9, 1), deal_id=did, batch_id=b.id))
        db.commit()
        assert lh.rebuild_once(db) >= 1
        rows = db.query(LogisticsHistory).filter(LogisticsHistory.deal_id == did, LogisticsHistory.item_code == "intl_freight").all()
        assert [(x.amount, str(x.on)) for x in rows] == [(50000, "2026-09-01")]
        assert lh.rebuild_once(db) == 0
