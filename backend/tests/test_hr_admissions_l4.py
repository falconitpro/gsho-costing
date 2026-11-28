"""L4 (D-206): допуски, обучение, инструктажи и медосмотры в «Персонале» — общий справочник и общая таблица с сервисом."""
import uuid
from datetime import date, timedelta

from tests.test_api_flow import client, _ok, H, SVC  # noqa: F401
from tests.test_hr_core_l1 import GD, D, _employers, _org
from tests.test_service_core import _order

PAYROLL = "payroll@example.com"


def _new_engineer(client, t):
    r = client.post("/api/admin/users", headers=H(GD), json={"email": f"eng{t}@example.com", "display_name": f"Инженер {t}", "roles": ["engineer"]})
    assert r.status_code == 201, r.text
    return r.json()["id"], f"eng{t}@example.com"


def test_hr_admissions_shared_with_service(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    uid, _email = _new_engineer(client, t)
    # допуск, заведённый в сервисе до появления карточки, — переходит в карточку при связи
    _ok(client.put("/api/service/permits", headers=H(SVC), json={"user_id": uid, "permit_code": "OT", "issued_on": D(-10)}))
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Допусков{t}", "first_name": "Иван",
                                                                   "hire": {"employer_id": ru, "start_date": D(-60), "department_id": dep["id"], "position_id": pos["id"]}}))
    _ok(client.patch(f"/api/hr/employees/{e['id']}", headers=H(GD), json={"user_id": uid}))
    adm = _ok(client.get(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD)))
    assert [x["permit_code"] for x in adm["items"]] == ["OT"] and adm["has_login"] and adm["items"][0]["from_service"] is False
    # в «Персонале» завели огневые — сервис при назначении их видит
    _, wo = _order(client, "RPR-WELD")
    gaps = lambda: {g["code"] for g in next(x for x in _ok(client.get("/api/service/engineers", headers=H(SVC), params={"order_id": wo["id"]}))  # noqa: E731
                                            if x["id"] == uid)["gaps"]}
    assert "HOT" in gaps()
    r = _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(PAYROLL), json={"permit_code": "HOT", "issued_on": D(-5)}))
    assert r["valid_until"] and r["state"] == "ok"
    assert "HOT" not in gaps()
    mine = _ok(client.get("/api/service/permits", headers=H(SVC), params={"user_id": uid}))["items"]
    assert {x["permit_code"] for x in mine} == {"OT", "HOT"}
    # продление в сервисе меняет ту же запись, дубля нет
    _ok(client.put("/api/service/permits", headers=H(SVC), json={"user_id": uid, "permit_code": "HOT", "issued_on": D(-1)}))
    assert len([x for x in _ok(client.get(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD)))["items"] if x["permit_code"] == "HOT"]) == 1
    from app.db import SessionLocal
    from app.models import EngineerPermit
    from sqlalchemy import select
    with SessionLocal() as db:
        assert len(db.scalars(select(EngineerPermit).where(EngineerPermit.permit_code == "HOT", EngineerPermit.employee_id == e["id"])).all()) == 1
        assert not db.scalars(select(EngineerPermit).where(EngineerPermit.permit_code == "HOT", EngineerPermit.user_id == uid,
                                                           EngineerPermit.employee_id.is_(None))).all()


def test_hr_admissions_medical_workers_and_overview(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, kg = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Рабочий{t}", "first_name": "Пётр",
                                                                   "hire": {"employer_id": ru, "start_date": D(-60), "department_id": dep["id"], "position_id": pos["id"]}}))
    code = f"MED{t}".upper()
    assert client.post("/api/hr/admission-kinds", headers=H("manager@example.com"), json={"code": code, "name": "x"}).status_code == 403
    _ok(client.post("/api/hr/admission-kinds", headers=H(GD), json={"code": code, "name": "Периодический медосмотр", "category": "medical", "valid_months": 12}), 201)
    assert client.post("/api/hr/admission-kinds", headers=H(GD), json={"code": code, "name": "дубль"}).status_code == 409
    # итог «годен» — только для медосмотра
    assert client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": "OT", "result": "fit"}).status_code == 422
    m = _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": code, "issued_on": D(-10), "result": "unfit"}))
    assert m["state"] == "unfit" and m["result_title"] == "не годен"
    _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": "PB-UG", "issued_on": D(-360)}))   # 12 мес. — истекает через ~5 дней
    old = _ok(client.put(f"/api/hr/employees/{e['id']}/admissions", headers=H(GD), json={"permit_code": "EB3", "issued_on": D(-800)}))
    assert old["state"] == "expired"
    ov = [x for x in _ok(client.get("/api/hr/admissions/overview", headers=H(GD)))["items"] if x["employee_id"] == e["id"]]
    assert [x["state"] for x in ov] == ["unfit", "expired", "expiring"]
    # область по юрлицу: пользователь, которому открыта только Киргизия, этих допусков не видит
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    pid = next(u["id"] for u in (users if isinstance(users, list) else users["items"]) if u["email"] == PAYROLL)
    _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": [kg]}))
    try:
        assert client.get(f"/api/hr/employees/{e['id']}/admissions", headers=H(PAYROLL)).status_code == 404
        assert all(x["employee_id"] != e["id"] for x in _ok(client.get("/api/hr/admissions/overview", headers=H(PAYROLL)))["items"])
        assert client.delete(f"/api/hr/admissions/{old['id']}", headers=H(PAYROLL)).status_code == 404
    finally:
        _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": []}))
    _ok(client.delete(f"/api/hr/admissions/{old['id']}", headers=H(PAYROLL)))
    assert date.fromisoformat(D(0)) - timedelta(days=1) < date.fromisoformat(D(1))
