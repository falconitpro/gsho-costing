"""D-105: перевод старого режима «на всю позицию / партию» на «на 1 машину» (migrations/031) не меняет расчёт."""
import json
from pathlib import Path

from tests.test_api_flow import client, _ok, H, MGR, LOG  # noqa: F401

SQL = Path(__file__).resolve().parents[1] / "migrations" / "031_per_unit_everywhere.sql"


def _calc(did):
    from app.db import SessionLocal
    from app.models import Deal
    from app.services.deal_calc import run_calc
    with SessionLocal() as db:
        _, res = run_calc(db, db.get(Deal, did))
        return json.loads(res.model_dump_json())


def _apply_sql():
    from app.db import engine
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(SQL.read_text())
        raw.commit()
    finally:
        raw.close()


def _same(a, b, path=""):
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, (int, float)):
        assert abs(a - b) <= max(1e-6, abs(a) * 1e-9), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_legacy_records_converted_without_changing_calc(client):
    from app.db import SessionLocal
    from app.models import Batch, LocalOption, Position
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Перевод"}), 201)["id"]
    p1 = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "PU-3",
             "machine_type": "ПДМ", "qty": 3, "unit_price": 1000000, "currency": "CNY"}), 201)
    p2 = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "PU-2",
             "machine_type": "Самосвал", "qty": 2, "unit_price": 800000, "currency": "CNY"}), 201)
    with SessionLocal() as db:
        b = Batch(deal_id=did, route="Китай — РФ", transport_units=2,
                  items={"intl_freight": {"amount": 90000, "currency": "CNY"}, "insurance_pct": 0.3, "broker_rub": "150 000",
                         "rf_delivery_rub": 250000, "comments": {"intl_freight": "x"}})
        db.add(b)
        db.flush()
        for pid, extra in ((p1["id"], {"util_fee_rub": 900000}), (p2["id"], {"util_fee_rub": 50000, "util_coefficient": 2.5})):
            p = db.get(Position, pid)
            p.batch_id = b.id
            p.logistics = {"domestic_logistics": {"amount": 30000, "currency": "CNY"}, "export_formalities": {"amount": "12,5", "currency": "CNY"},
                           "certification_rub": 60000, "hs_code": "8429519000", "duty_pct": 5, "vat_pct": 22, **extra}
        db.add(LocalOption(deal_id=did, position_id=p1["id"], name="Камера", qty=6, cost=1000, currency="RUB", per_unit=False, install_cost_rub=3000))
        db.add(LocalOption(deal_id=did, position_id=p1["id"], name="Аптечка", qty=4, cost=500, currency="RUB", per_unit=False))
        db.commit()
    before = _calc(did)
    _apply_sql()
    with SessionLocal() as db:
        b = db.query(Batch).filter(Batch.deal_id == did).one()
        assert b.items["per_unit"] and abs(b.items["intl_freight"]["amount"] - 18000) < 1e-9 and abs(b.items["broker_rub"] - 30000) < 1e-9
        assert b.items["insurance_pct"] == 0.3 and b.items["comments"] == {"intl_freight": "x"}
        q1, q2 = db.get(Position, p1["id"]), db.get(Position, p2["id"])
        assert q1.logistics["per_unit"] and abs(q1.logistics["domestic_logistics"]["amount"] - 10000) < 1e-9
        assert abs(q1.logistics["util_fee_rub"] - 300000) < 1e-9 and q2.logistics["util_fee_rub"] == 50000      # по коэффициенту — не делится
        opts = {o.name: o for o in db.query(LocalOption).filter(LocalOption.deal_id == did)}
        assert opts["Камера"].per_unit and opts["Камера"].qty == 2 and opts["Камера"].install_cost_rub == 1000
        assert not opts["Аптечка"].per_unit and opts["Аптечка"].qty == 4                                        # 4 на 3 машины не делится
    _same(before, _calc(did))
    snap = None
    with SessionLocal() as db:
        snap = json.dumps(db.query(Batch).filter(Batch.deal_id == did).one().items, sort_keys=True)
    _apply_sql()                                                                                               # повторный запуск — без изменений
    with SessionLocal() as db:
        assert json.dumps(db.query(Batch).filter(Batch.deal_id == did).one().items, sort_keys=True) == snap
    _same(before, _calc(did))
