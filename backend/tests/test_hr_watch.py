"""D-207: «Персонал» — уволен, а вход не отключён; напоминания о сроках допусков. Каждое — один раз."""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from tests.test_api_flow import client, _ok, H  # noqa: F401
from tests.test_hr_core_l1 import GD, D, _employers, _org

PAYROLL = "payroll@example.com"


def _workday_morning():
    from app.calc.calendar import DEFAULT_CALENDAR as CAL
    from app.core.clock import today
    d = today()
    while not CAL.is_workday(d):
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone(timedelta(hours=3))), d


def test_hr_watch_offboarding_and_permits(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import NotificationLog
    from app.services import hr_watch
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    uid = _ok(client.post("/api/admin/users", headers=H(GD), json={"email": f"leaver{t}@example.com", "display_name": f"Уходящий {t}", "roles": ["engineer"]}), 201)["id"]
    now, day = _workday_morning()
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Уходящий{t}", "first_name": "Олег", "user_id": uid,
                                                                   "hire": {"employer_id": ru, "start_date": D(-200), "department_id": dep["id"], "position_id": pos["id"]}}))
    m = e["employments"][0]["id"]
    soon = (day + timedelta(days=10)).isoformat()
    adm = _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": "OT", "issued_on": D(-100), "valid_until": soon}))
    with SessionLocal() as db:
        hr_watch.check(db, now)
        db.commit()
        rows = db.scalars(select(NotificationLog).where(NotificationLog.event == "hr_permit_soon", NotificationLog.link.contains(adm["id"]))).all()
        got = {r.user_id for r in rows if r.channel == "app"}
        assert uid in got and len(got) >= 2   # сам сотрудник и кадровики
        n = len(rows)
        hr_watch.check(db, now)
        db.commit()
        assert len(db.scalars(select(NotificationLog).where(NotificationLog.event == "hr_permit_soon", NotificationLog.link.contains(adm["id"]))).all()) == n
        assert hr_watch.check(db, now.replace(hour=7)) == 0   # до 9:00 — тишина
    # продлили — старые напоминания не повторяются, новый срок дальше 30 дней — новых нет
    _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": "OT", "issued_on": D(0), "valid_until": (day + timedelta(days=200)).isoformat()}))
    # увольнение вчерашним днём — вход блокируется сразу при сохранении (решение владельца 28.11)
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(-1)}))
    assert r["offboard_needed"] is False and r["user_active"] is False
    me = _ok(client.get("/api/auth/me", headers=H(GD)))["id"]
    with SessionLocal() as db:
        from app.models import AuditLog, User
        u = db.get(User, uid)
        assert u.is_active is False and u.offboarded_by == me and u.offboarded_at is not None
        assert db.scalar(select(AuditLog.id).where(AuditLog.entity_id == uid, AuditLog.action == "offboard"))
        assert db.scalars(select(NotificationLog).where(NotificationLog.event == "security", NotificationLog.text.contains("отключён автоматически"))).first()


def test_hr_block_after_last_day_by_job(client):  # noqa: F811
    """Увольнение «сегодня» — работает до конца дня; задание блокирует вход на следующий день."""
    from app.db import SessionLocal
    from app.models import User
    from app.services import hr_watch
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    uid = _ok(client.post("/api/admin/users", headers=H(GD), json={"email": f"last{t}@example.com", "display_name": f"Последний {t}", "roles": ["engineer"]}), 201)["id"]
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Последний{t}", "first_name": "День", "user_id": uid,
                                                                   "hire": {"employer_id": ru, "start_date": D(-20), "department_id": dep["id"], "position_id": pos["id"]}}))
    # второе место работы ещё действует — блокировать нельзя
    ru2 = _employers(client)[1]
    dep2, pos2 = _org(client, GD, ru2, t + "b")
    r = _ok(client.post(f"/api/hr/employees/{e['id']}/employments", headers=H(GD),
                        json={"employer_id": ru2, "kind": "external_part", "start_date": D(-10), "department_id": dep2["id"], "position_id": pos2["id"]}))
    m1 = next(x["id"] for x in r["employments"] if x["employer_id"] == ru)
    m2 = next(x["id"] for x in r["employments"] if x["employer_id"] == ru2)
    _ok(client.post(f"/api/hr/employments/{m1}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(-2)}))
    with SessionLocal() as db:
        assert db.get(User, uid).is_active is True
    r = _ok(client.post(f"/api/hr/employments/{m2}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(0)}))
    assert r["user_active"] is True   # сегодня ещё последний рабочий день
    with SessionLocal() as db:
        hr_watch.block_gone(db, date.fromisoformat(D(0)))
        db.commit()
        assert db.get(User, uid).is_active is True
        assert hr_watch.block_gone(db, date.fromisoformat(D(1))) == 1
        db.commit()
        assert db.get(User, uid).is_active is False

def test_hr_summary(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, kg = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    slot = _ok(client.post("/api/hr/staff-slots", headers=H(GD), json={"employer_id": ru, "department_id": dep["id"], "position_id": pos["id"], "fte": 3, "valid_from": D(-400)}))
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Сводкин{t}", "first_name": "Ян",
                                                                   "hire": {"employer_id": ru, "start_date": D(0), "staff_slot_id": slot["id"]}}))
    _ok(client.put(f"/api/hr/employees/{e['id']}/personal", headers=H(GD), json={"snils": "112-233-445 95"}))
    assert client.post(f"/api/hr/employees/{e['id']}/documents", headers=H(GD), data={"kind": "passport"},
                       files=[("files", ("p.pdf", b"%PDF", "application/pdf"))]).status_code == 201
    s = _ok(client.get(f"/api/hr/summary?employer_id={ru}", headers=H(GD)))
    row = next(x for x in s["employers"] if x["employer_id"] == ru)
    assert row["people"] >= 1 and row["fte_plan"] >= 3 and row["vacant_slots"] >= 1 and row["hired_month"] >= 1
    g = next(x for x in s["gaps"] if x["employee_id"] == e["id"])
    assert "Паспорт" not in g["docs"] and "Трудовой договор" in g["docs"] and "СНИЛС" not in g["personal"] and "ИНН" in g["personal"]
    # только кадры (без личных данных и оплаты) — пробелы только по кадровым документам, личных полей не видно
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    cd = next(u for u in (users if isinstance(users, list) else users["items"]) if u["email"] == "cd@example.com")
    client.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": cd["id"], "group": "hr_core", "level": 1, "reason": "тест", "comment": "тест"})
    _ok(client.put(f"/api/hr/scopes/{cd['id']}", headers=H(GD), json={"employer_ids": [ru]}))
    s2 = _ok(client.get("/api/hr/summary", headers=H("cd@example.com")))
    assert all(x["employer_id"] == ru for x in s2["employers"])
    assert all(not x["personal"] and not x["docs"] for x in s2["gaps"]) and s2["gaps_scope"] == ["core"]
