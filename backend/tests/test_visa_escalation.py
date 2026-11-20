"""D-112: виза ФД без ответа N рабочих дней → ЗГД; заместитель ФД визирует за него."""
from datetime import date, timedelta

from tests.test_api_flow import client, _ok, H, CD, FD, GD, MGR  # noqa: F401
from tests.test_negotiation import _approved  # noqa: F401


def _to_fd_visa(client):
    """Сделка на визе ФД: маржа ниже порога (2 %)."""
    from tests.test_negotiation import _approved as _a   # тот же путь, но без последнего submit
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Виза"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "NG-7",
              "machine_type": "ПДМ", "qty": 1, "unit_price": 1500000, "currency": "CNY"}), 201)
    var = _ok(client.get(f"/api/deals/{did}", headers=H(MGR)))["offer"]
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    from tests.test_api_flow import LOG
    items = {"door_to_door": {"amount": 200000, "currency": "CNY"}, "broker_rub": 200000, "comments": {"door_to_door": "x", "broker_rub": "x"}}
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(LOG), json={"items": items, "position_ids": [pos["id"]]}), 201)
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG)))
    _ok(client.post(f"/api/deals/{did}/price", headers=H(CD), json={"variant_id": var["id"], "mode": "margin", "value": 2}))
    schemes = _ok(client.get("/api/refs/payment-schemes", headers=H(CD)))
    calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
    _ok(client.post(f"/api/deals/{did}/payment-terms", headers=H(CD), json={"client_payments": schemes[0]["lines"],
                                                                            "factory_payments": {pos["id"]: calc["payment_terms"]["default_factory"]}}))
    assert _ok(client.post(f"/api/deals/{did}/submit", headers=H(CD)))["status"] == "fd_visa"
    return did


def test_fd_silence_escalates_to_gd(client):
    from app.db import SessionLocal
    from app.models import Deal, Task
    from app.services.visa_watch import escalate_fd
    did = _to_fd_visa(client)
    with SessionLocal() as db:
        assert escalate_fd(db, date.today()) == 0                      # срок не вышел
        assert escalate_fd(db, date.today() + timedelta(days=10)) >= 1
        d = db.get(Deal, did)
        assert d.status.value == "gd_visa"
        open_ = {(t.role, t.block) for t in db.query(Task).filter(Task.deal_id == did, Task.done_at.is_(None))}
        assert ("gd", "visa") in open_ and ("fd", "visa") not in open_
    assert _ok(client.post(f"/api/deals/{did}/visa", headers=H(GD), json={"approved": True}))["status"] == "approved"


def test_deputy_signs_for_fd(client):
    from app.db import SessionLocal
    from app.models import User
    did = _to_fd_visa(client)
    assert client.post(f"/api/deals/{did}/visa", headers=H(MGR), json={"approved": True}).status_code == 403
    with SessionLocal() as db:
        fd = db.query(User).filter(User.email == FD).one()
        mgr = db.query(User).filter(User.email == MGR).one()
        fd.deputy_id, fd.deputy_from, fd.deputy_to = mgr.id, date.today() - timedelta(days=1), date.today() + timedelta(days=1)
        db.commit()
    try:
        assert "fd" in _ok(client.get("/api/auth/me", headers=H(MGR)))["acting_roles"]
        st = _ok(client.post(f"/api/deals/{did}/visa", headers=H(MGR), json={"approved": True, "comment": "в отпуске ФД"}))["status"]
        assert st in ("gd_visa", "approved")
    finally:
        with SessionLocal() as db:
            fd = db.query(User).filter(User.email == FD).one()
            fd.deputy_id = fd.deputy_from = fd.deputy_to = None
            db.commit()
