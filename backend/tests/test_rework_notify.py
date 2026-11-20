"""Возврат на доработку логисту: задача и уведомление — логисту, даже если партию заполнял ЗГД; повтор не плодит дубли."""
from tests.test_api_flow import client, _ok, H, MGR, LOG, CD, GD  # noqa: F401


def _deal_filled_by_gd(client):
    clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
    factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Возврат"}), 201)["id"]
    pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "RW-1",
              "machine_type": "ПДМ", "qty": 1, "unit_price": 1500000, "currency": "CNY"}), 201)
    _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
    items = {"door_to_door": {"amount": 200000, "currency": "CNY"}, "broker_rub": 200000, "comments": {"door_to_door": "x", "broker_rub": "x"}}
    _ok(client.post(f"/api/deals/{did}/logistics/batches", headers=H(GD), json={"items": items, "position_ids": [pos["id"]]}), 201)   # за логиста — ЗГД
    _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(GD), json={"hs_code": "8429519000", "util_applies": False}))
    _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(GD)))
    return did


def _logist_open(did):
    from app.db import SessionLocal
    from app.models import NotificationLog, Task, User
    with SessionLocal() as db:
        logist = db.query(User).filter(User.email == LOG).one()
        tasks = db.query(Task).filter(Task.deal_id == did, Task.block == "logistics", Task.done_at.is_(None)).all()
        notes = db.query(NotificationLog).filter(NotificationLog.deal_id == did, NotificationLog.user_id == logist.id, NotificationLog.channel == "app",
                                                 NotificationLog.read_at.is_(None)).all()
        return logist.id, [(t.assignee_id, t.title) for t in tasks], [n.text for n in notes]


def test_rework_to_logistics_reaches_logist(client):
    did = _deal_filled_by_gd(client)
    _ok(client.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason": "уточнить фрахт"}))
    lid, tasks, notes = _logist_open(did)
    assert tasks == [(lid, "Доработка: уточнить фрахт")]
    assert any("Доработка: уточнить фрахт" in t for t in notes)
    # повторный возврат с той же причиной: одна открытая задача, уведомление о ней — непрочитано
    _ok(client.post(f"/api/deals/{did}/rework", headers=H(GD), json={"block": "logistics", "reason": "уточнить фрахт"}))
    from app.db import SessionLocal
    from app.services.notification_kinds import close_done_task_notices
    with SessionLocal() as db:
        close_done_task_notices(db)
        db.commit()
    lid, tasks, notes = _logist_open(did)
    assert len(tasks) == 1 and tasks[0][0] == lid
    assert any("Доработка: уточнить фрахт" in t for t in notes)
    n = _ok(client.get("/api/notifications/unread-count", headers=H(LOG)))
    assert n["unread"] >= 1


def test_repair_open_task_assigned_to_gd(client):
    from app.db import SessionLocal
    from app.models import Deal, Task, User
    from app.services.deal_service import repair_task_assignees
    did = _deal_filled_by_gd(client)
    _ok(client.post(f"/api/deals/{did}/rework", headers=H(CD), json={"block": "logistics", "reason": "старая задача"}))
    with SessionLocal() as db:       # как было до исправления: задача логиста и исполнитель сделки — ЗГД
        gd = db.query(User).filter(User.email == GD).one()
        t = db.query(Task).filter(Task.deal_id == did, Task.block == "logistics", Task.done_at.is_(None)).one()
        t.assignee_id = gd.id
        db.get(Deal, did).logistician_id = gd.id
        db.commit()
        assert repair_task_assignees(db) >= 1
        lid, tasks, notes = _logist_open(did)
        assert tasks == [(lid, "Доработка: старая задача")] and any("старая задача" in x for x in notes)
        assert db.get(Deal, did).logistician_id is None
        assert repair_task_assignees(db) == 0
