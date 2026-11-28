"""D-208 (L2 упрощённо): приказ по личному составу в Канцелярии из кадрового события; подпись на бумаге, скан — в Канцелярии."""
import uuid

from sqlalchemy import select

from tests.test_api_flow import client, _ok, H  # noqa: F401
from tests.test_hr_core_l1 import GD, D, _employers, _org

PAYROLL, CLERK = "payroll@example.com", "clerk@example.com"


def test_hr_order_from_event(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import NotificationLog
    t = uuid.uuid4().hex[:5]
    ru, kg = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    dep2 = _ok(client.post("/api/hr/departments", headers=H(GD), json={"employer_id": ru, "name": f"Склад {t}", "valid_from": D(-400)}))
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Приказов{t}", "first_name": "Иван", "middle_name": "Петрович",
                                                                   "hire": {"employer_id": ru, "start_date": D(-5), "department_id": dep["id"], "position_id": pos["id"]}}))
    m = e["employments"][0]["id"]
    _ok(client.post(f"/api/hr/employments/{m}/pay", headers=H(PAYROLL), json={"kind": "salary", "amount": 154321, "effective_from": D(-5)}))
    _ok(client.put(f"/api/hr/employees/{e['id']}/personal", headers=H(GD), json={"passport_number": "556677"}))
    hire = e["employments"][0]["events"][0]
    # кадровик без права Канцелярии формирует черновик; видит его как автор
    o = _ok(client.post(f"/api/hr/events/{hire['id']}/order", headers=H(PAYROLL)), 201)
    assert o["status"] == "draft" and o["url"].endswith(o["office_doc_id"])
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(PAYROLL)).status_code == 409
    doc = _ok(client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(PAYROLL)))
    assert doc["kind"] == "order" and doc["category"] == "hr" and f"Приказов{t}" in doc["subject"]
    body = doc.get("body", "")
    assert f"Приказов{t} Иван Петрович" in body and dep["name"] in body and "154321" not in body and "556677" not in body
    assert any(f["kind"] == "draft" and f["name"].endswith(".docx") for f in doc["files"])
    # делопроизводитель получил уведомление и видит приказ
    with SessionLocal() as db:
        clerk = db.scalar(select(NotificationLog).where(NotificationLog.event == "office_hr_order", NotificationLog.link == o["url"]))
        assert clerk is not None
    assert _ok(client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(CLERK)))["id"] == o["office_doc_id"]
    # список приказов в карточке
    assert _ok(client.get(f"/api/hr/employees/{e['id']}/orders", headers=H(GD)))[hire["id"]]["office_doc_id"] == o["office_doc_id"]
    # перевод — текст «с … на …»; отменённое событие — без приказа
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "transfer", "department_id": dep2["id"], "effective_from": D(0)}))
    tr = next(x for x in r["employments"][0]["events"] if x["kind"] == "transfer")
    o2 = _ok(client.post(f"/api/hr/events/{tr['id']}/order", headers=H(GD)), 201)
    b2 = _ok(client.get(f"/api/office/docs/{o2['office_doc_id']}", headers=H(GD)))["body"]
    assert f"подразделения «{dep['name']}»" in b2 and f"подразделения «{dep2['name']}»" in b2
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "rate", "rate": 0.5, "effective_from": D(1)}))
    rt = next(x for x in r["employments"][0]["events"] if x["kind"] == "rate")
    _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": rt["id"], "comment": "ошибка"}))
    assert client.post(f"/api/hr/events/{rt['id']}/order", headers=H(GD)).status_code == 409
    # чужое юрлицо — 404; без права «Персонала» — 403
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    pid = next(u["id"] for u in (users if isinstance(users, list) else users["items"]) if u["email"] == PAYROLL)
    _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": [kg]}))
    try:
        assert client.post(f"/api/hr/events/{tr['id']}/order", headers=H(PAYROLL)).status_code == 404
        assert client.get(f"/api/hr/employees/{e['id']}/orders", headers=H(PAYROLL)).status_code == 404
    finally:
        _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": []}))
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H("manager@example.com")).status_code == 403
