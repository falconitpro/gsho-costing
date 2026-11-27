"""L1 (D-205): блок «Персонал» — ядро. Изоляция по работодателю (негативные тесты), события с датами действия,
повторный приём, отмена события, штатное расписание, журнал без значений."""
import uuid
from datetime import date, timedelta

import pytest

from tests.test_api_flow import client, _ok, H, FD, MGR  # noqa: F401
from scripts.seed import TEST_LOGIN as GD

D = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731


def _employers(client):
    ents = {e["code"]: e for e in _ok(client.get("/api/hr/employers?all=1", headers=H(GD)))}
    for code in ("ru", "kg"):
        _ok(client.patch(f"/api/hr/employers/{ents[code]['id']}", headers=H(GD), json={"is_employer": True}))
    return ents["ru"]["id"], ents["kg"]["id"]


def _org(client, who, employer_id, t):
    dep = _ok(client.post("/api/hr/departments", headers=H(who), json={"employer_id": employer_id, "name": f"Сервис {t}", "valid_from": D(-400)}))
    pos = _ok(client.post("/api/hr/positions", headers=H(who), json={"employer_id": employer_id, "name": f"Инженер {t}", "category": "specialist"}))
    return dep, pos


def _grant_fd(client, ru):
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    fd = next(u for u in (users if isinstance(users, list) else users["items"]) if u["email"] == FD)
    r = client.post("/api/admin/access/overrides", headers=H(GD), json={"user_id": fd["id"], "group": "hr_core", "level": 2, "reason": "тест HR", "comment": "тест HR"})
    assert r.status_code in (201, 409), r.text
    _ok(client.put(f"/api/hr/scopes/{fd['id']}", headers=H(GD), json={"employer_ids": [ru]}))
    return fd


def test_hr_access_and_isolation(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, kg = _employers(client)
    assert client.get("/api/hr/employees", headers=H(MGR)).status_code == 403        # нет группы
    dep_kg, pos_kg = _org(client, GD, kg, t)
    kg_emp = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": "Асанов", "first_name": f"Бек{t}",
                                                                         "hire": {"employer_id": kg, "start_date": D(-30), "department_id": dep_kg["id"], "position_id": pos_kg["id"]}}))
    _grant_fd(client, ru)
    # чужое юрлицо: не видно в списках, карточка и правки — 404, создание у чужого работодателя — 404
    assert all(e["id"] != kg for e in _ok(client.get("/api/hr/employers", headers=H(FD))))
    assert all(x["id"] != kg_emp["id"] for x in _ok(client.get("/api/hr/employees?status=all", headers=H(FD))))
    assert all(d["employer_id"] != kg for d in _ok(client.get("/api/hr/departments", headers=H(FD))))
    assert client.get(f"/api/hr/employees/{kg_emp['id']}", headers=H(FD)).status_code == 404
    assert client.patch(f"/api/hr/employees/{kg_emp['id']}", headers=H(FD), json={"last_name": "X"}).status_code == 404
    emp_kg_id = kg_emp["employments"][0]["id"]
    assert client.post(f"/api/hr/employments/{emp_kg_id}/events", headers=H(FD), json={"kind": "terminate"}).status_code == 404
    assert client.post("/api/hr/departments", headers=H(FD), json={"employer_id": kg, "name": "Чужой"}).status_code == 404
    assert client.patch(f"/api/hr/departments/{dep_kg['id']}", headers=H(FD), json={"name": "Чужой"}).status_code == 404
    assert client.post(f"/api/hr/employees/{kg_emp['id']}/employments", headers=H(FD),
                       json={"employer_id": ru, "start_date": D(0), "department_id": "x", "position_id": "y"}).status_code == 404
    # своё юрлицо: ссылка на подразделение чужого работодателя — 422
    dep_ru, pos_ru = _org(client, FD, ru, t)
    bad = {"last_name": "Иванов", "first_name": "Иван", "hire": {"employer_id": ru, "start_date": D(0), "department_id": dep_kg["id"], "position_id": pos_ru["id"]}}
    assert client.post("/api/hr/employees", headers=H(FD), json=bad).status_code == 422
    # отметить работодателем может только ЗГД
    assert client.patch(f"/api/hr/employers/{kg}", headers=H(FD), json={"is_employer": False}).status_code == 403
    assert client.get("/api/hr/scopes", headers=H(FD)).status_code == 403


def test_hr_events_rehire_and_slots(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    dep2 = _ok(client.post("/api/hr/departments", headers=H(GD), json={"employer_id": ru, "name": f"Склад {t}", "parent_id": dep["id"], "valid_from": D(-400)}))
    slot = _ok(client.post("/api/hr/staff-slots", headers=H(GD), json={"employer_id": ru, "department_id": dep["id"], "position_id": pos["id"], "fte": 2,
                                                                       "work_location": "Карьер Удачный", "valid_from": D(-400)}))
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Петров{t}", "first_name": "Пётр",
                                                                   "hire": {"employer_id": ru, "start_date": D(-100), "staff_slot_id": slot["id"], "rate": 0.5}}))
    assert e["personnel_no"].startswith("П-") and e["status"] == "active"
    m = e["employments"][0]
    assert m["assignment"]["department_id"] == dep["id"] and m["assignment"]["rate"] == 0.5
    occ = {s["id"]: s["occupied"] for s in _ok(client.get(f"/api/hr/staff-slots?employer_id={ru}", headers=H(GD)))}
    assert occ[slot["id"]] == 0.5
    # временный перевод на 10 дней в прошлом: сегодня — прежнее назначение, на дату внутри периода — новое
    _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "transfer", "department_id": dep2["id"],
                                                                                  "effective_from": D(-50), "effective_to": D(-41)}))
    on = _ok(client.get(f"/api/hr/employees/{e['id']}?on={D(-45)}", headers=H(GD)))
    assert on["employments"][0]["assignment"]["department_id"] == dep2["id"]
    now = _ok(client.get(f"/api/hr/employees/{e['id']}", headers=H(GD)))
    assert now["employments"][0]["assignment"]["department_id"] == dep["id"]
    # перевод в будущем: сегодня ещё старое; ставка; отмена ставки с причиной
    _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "transfer", "department_id": dep2["id"], "effective_from": D(10)}))
    assert _ok(client.get(f"/api/hr/employees/{e['id']}", headers=H(GD)))["employments"][0]["assignment"]["department_id"] == dep["id"]
    r = _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "rate", "rate": 1, "effective_from": D(-5)}))
    rate_ev = next(x for x in r["employments"][0]["events"] if x["kind"] == "rate")
    assert client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": rate_ev["id"]}).status_code == 422
    r = _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": rate_ev["id"], "comment": "ошибка"}))
    assert r["employments"][0]["assignment"]["rate"] == 0.5
    assert client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": rate_ev["id"], "comment": "ещё"}).status_code == 409
    hire_ev = next(x for x in r["employments"][0]["events"] if x["kind"] == "hire")
    assert client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": hire_ev["id"], "comment": "x"}).status_code == 422
    # увольнение: перевод после даты увольнения мешает; после — повторный приём = новые трудовые отношения
    assert client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(-1)}).status_code == 409
    fut = next(x for x in r["employments"][0]["events"] if x["kind"] == "transfer" and x["effective_from"] == D(10))
    _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": fut["id"], "comment": "передумали"}))
    r = _ok(client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(-1), "reason": "по собственному"}))
    assert r["status"] == "terminated"
    assert client.post(f"/api/hr/employments/{m['id']}/events", headers=H(GD), json={"kind": "rate", "rate": 1, "effective_from": D(0)}).status_code == 409
    occ = {s["id"]: s["occupied"] for s in _ok(client.get(f"/api/hr/staff-slots?employer_id={ru}", headers=H(GD)))}
    assert occ[slot["id"]] == 0
    assert client.post(f"/api/hr/employees/{e['id']}/employments", headers=H(GD),
                       json={"employer_id": ru, "start_date": D(-2), "department_id": dep["id"], "position_id": pos["id"]}).status_code == 409
    r = _ok(client.post(f"/api/hr/employees/{e['id']}/employments", headers=H(GD),
                        json={"employer_id": ru, "start_date": D(0), "department_id": dep["id"], "position_id": pos["id"]}))
    assert len(r["employments"]) == 2 and r["status"] == "active" and r["personnel_no"] == e["personnel_no"]
    # штатная единица: новая версия с даты закрывает прежнюю днём раньше
    v2 = _ok(client.post(f"/api/hr/staff-slots/{slot['id']}/change", headers=H(GD), json={"valid_from": D(1), "fte": 3}))
    assert v2["supersedes_id"] == slot["id"] and v2["fte"] == 3
    now_ids = [s["id"] for s in _ok(client.get(f"/api/hr/staff-slots?employer_id={ru}", headers=H(GD)))]
    later_ids = [s["id"] for s in _ok(client.get(f"/api/hr/staff-slots?employer_id={ru}&on={D(2)}", headers=H(GD)))]
    assert slot["id"] in now_ids and v2["id"] not in now_ids and v2["id"] in later_ids and slot["id"] not in later_ids
    # подразделение не может входить само в себя
    assert client.patch(f"/api/hr/departments/{dep['id']}", headers=H(GD), json={"parent_id": dep2["id"]}).status_code == 422


def test_hr_events_append_only_and_audit_without_values(client):  # noqa: F811
    from sqlalchemy import select, text
    from app.db import SessionLocal
    from app.models import AuditLog, HrEmployeeEvent
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    secret = f"Секретов{t}"
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": secret, "first_name": "Сергей",
                                                                   "hire": {"employer_id": ru, "start_date": D(0), "department_id": dep["id"], "position_id": pos["id"]}}))
    _ok(client.patch(f"/api/hr/employees/{e['id']}", headers=H(GD), json={"last_name": secret + "ов"}))
    with SessionLocal() as db:
        ev = db.scalar(select(HrEmployeeEvent).where(HrEmployeeEvent.employee_id == e["id"]))
        with pytest.raises(Exception):
            db.execute(text("UPDATE hr_employee_events SET comment = 'x' WHERE id = :i"), {"i": ev.id})
            db.flush()
        db.rollback()
        with pytest.raises(Exception):
            db.execute(text("DELETE FROM hr_employee_events WHERE id = :i"), {"i": ev.id})
            db.flush()
        db.rollback()
        rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == e["id"])).all()
        assert rows and all(secret not in str(r.before) + str(r.after) + r.comment for r in rows)
