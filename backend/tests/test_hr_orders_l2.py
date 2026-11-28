"""D-208 (L2 упрощённо): приказ по личному составу в Канцелярии из кадрового события; подпись на бумаге, скан — в Канцелярии."""
import uuid

from sqlalchemy import select

from tests.test_api_flow import client, _ok, H  # noqa: F401
from tests.test_hr_core_l1 import GD, D, _employers, _org

PAYROLL, CLERK = "payroll@example.com", "office@example.com"


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
    _ok(client.post(f"/api/hr/employments/{m}/pay", headers=H(PAYROLL), json={"kind": "allowance", "name": "Районный коэффициент", "percent": 80, "effective_from": D(-5)}))
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(PAYROLL), json={}).status_code == 422   # нет договора
    tpl = _ok(client.get(f"/api/hr/events/{hire['id']}/order-template", headers=H(PAYROLL)))
    assert tpl["kind"] == "hire" and "77-1-3" in tpl["tk_bases"]
    det = {"contract_number": f"ТД-{t}", "contract_date": D(-6), "probation_months": 3, "conditions": "постоянно, вахтовым методом"}
    o = _ok(client.post(f"/api/hr/events/{hire['id']}/order", headers=H(PAYROLL), json=det), 201)
    assert o["status"] == "draft" and o["url"].endswith(o["office_doc_id"])
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(PAYROLL), json=det).status_code == 409
    doc = _ok(client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(PAYROLL)))
    assert doc["kind"] == "order" and doc["category"] == "hr" and f"Приказов{t}" in doc["subject"]
    body = doc.get("body", "")
    assert f"Приказов{t} Иван Петрович" in body and dep["name"] in body and "556677" not in body   # паспорт в приказ не входит
    assert "154 321,00 руб. в месяц" in body and "Районный коэффициент — 80 %" in body and f"№ ТД-{t}" in body and "Испытание на срок 3 мес." in body
    assert "____" not in body   # от руки — только подписи
    assert any(f["kind"] == "draft" and f["name"].endswith(".docx") for f in doc["files"])
    # делопроизводитель получил уведомление и видит приказ
    with SessionLocal() as db:
        rows = db.scalars(select(NotificationLog).where(NotificationLog.event == "office_hr_order", NotificationLog.link == o["url"])).all()
        from app.models import User
        assert rows and all("payroll" in (db.get(User, r.user_id).roles or []) or {"gd", "fd"} & set(db.get(User, r.user_id).roles or []) for r in rows)
    # приказ с оплатой труда: делопроизводитель без доступа к оплате труда его не видит (решение владельца 28.11)
    assert doc["pay_restricted"] is True
    assert client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(CLERK)).status_code == 404
    assert all(x["id"] != o["office_doc_id"] for x in _ok(client.get("/api/office/docs", headers=H(CLERK)))["items"])
    # список приказов в карточке
    assert _ok(client.get(f"/api/hr/employees/{e['id']}/orders", headers=H(GD)))[hire["id"]]["office_doc_id"] == o["office_doc_id"]
    # перевод — текст «с … на …»; отменённое событие — без приказа
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "transfer", "department_id": dep2["id"], "effective_from": D(0)}))
    tr = next(x for x in r["employments"][0]["events"] if x["kind"] == "transfer")
    assert client.post(f"/api/hr/events/{tr['id']}/order", headers=H(GD), json={}).status_code == 422   # нет допсоглашения
    tpl2 = _ok(client.get(f"/api/hr/events/{tr['id']}/order-template", headers=H(GD)))
    assert tpl2["prefill"]["contract_number"] == f"ТД-{t}"   # договор подставляется из приказа о приёме
    o2 = _ok(client.post(f"/api/hr/events/{tr['id']}/order", headers=H(GD),
                         json={**tpl2["prefill"], "addendum_number": "1", "addendum_date": D(0)}), 201)
    b2 = _ok(client.get(f"/api/office/docs/{o2['office_doc_id']}", headers=H(GD)))["body"]
    assert f"подразделения «{dep['name']}»" in b2 and f"подразделения «{dep2['name']}»" in b2
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "rate", "rate": 0.5, "effective_from": D(1)}))
    rt = next(x for x in r["employments"][0]["events"] if x["kind"] == "rate")
    _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "cancel", "cancels_id": rt["id"], "comment": "ошибка"}))
    assert client.post(f"/api/hr/events/{rt['id']}/order", headers=H(GD), json={}).status_code == 409
    # номер до подписи — делопроизводитель; печатная форма с номером; при подписании номер не меняется
    assert client.post(f"/api/hr/orders/{o['office_doc_id']}/number", headers=H(CLERK)).status_code == 404   # не видит
    d = _ok(client.post(f"/api/hr/orders/{o['office_doc_id']}/number", headers=H(PAYROLL)))
    assert d["number"] and d["status"] == "draft"
    assert [f for f in d["files"] if f["name"].endswith(".docx")][0]["name"].startswith(f"Приказ {d['number']}")
    assert len([f for f in d["files"] if f["name"].endswith(".docx")]) == 1
    # чужое юрлицо — 404; без права «Персонала» — 403
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    pid = next(u["id"] for u in (users if isinstance(users, list) else users["items"]) if u["email"] == PAYROLL)
    _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": [kg]}))
    try:
        assert client.post(f"/api/hr/events/{tr['id']}/order", headers=H(PAYROLL), json={}).status_code == 404
        assert client.get(f"/api/hr/employees/{e['id']}/orders", headers=H(PAYROLL)).status_code == 404
    finally:
        _ok(client.put(f"/api/hr/scopes/{pid}", headers=H(GD), json={"employer_ids": []}))
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H("manager@example.com"), json={}).status_code == 403
    # увольнение: статья ТК из списка, документ-основание, договор из приказа о приёме
    r = _ok(client.post(f"/api/hr/employments/{m}/events", headers=H(GD), json={"kind": "terminate", "effective_from": D(3), "reason": "по собственному"}))
    te = next(x for x in r["employments"][0]["events"] if x["kind"] == "terminate")
    pre = _ok(client.get(f"/api/hr/events/{te['id']}/order-template", headers=H(GD)))["prefill"]
    assert client.post(f"/api/hr/events/{te['id']}/order", headers=H(GD), json=pre).status_code == 422
    o3 = _ok(client.post(f"/api/hr/events/{te['id']}/order", headers=H(GD), json={**pre, "tk_basis": "77-1-3", "basis_doc": f"заявление работника от {D(-1)}"}), 201)
    b3 = _ok(client.get(f"/api/office/docs/{o3['office_doc_id']}", headers=H(GD)))["body"]
    assert "пункт 3 части первой статьи 77" in b3 and f"№ ТД-{t}" in b3 and "____" not in b3
    # увольнение — без оплаты: видят делопроизводители и выбранные сотрудники
    assert _ok(client.get(f"/api/office/docs/{o3['office_doc_id']}", headers=H(CLERK)))["pay_restricted"] is False


def test_hr_order_visibility_approval_acks(client):  # noqa: F811
    t = uuid.uuid4().hex[:5]
    ru, _ = _employers(client)
    dep, pos = _org(client, GD, ru, t)
    users = _ok(client.get("/api/admin/users", headers=H(GD)))
    ids = {u["email"]: u["id"] for u in (users if isinstance(users, list) else users["items"])}
    emp_uid = _ok(client.post("/api/admin/users", headers=H(GD), json={"email": f"ord{t}@example.com", "display_name": f"Работник {t}", "roles": ["engineer"]}), 201)["id"]
    e = _ok(client.post("/api/hr/employees", headers=H(GD), json={"last_name": f"Видимов{t}", "first_name": "Ян", "user_id": emp_uid,
                                                                   "hire": {"employer_id": ru, "start_date": D(-5), "department_id": dep["id"], "position_id": pos["id"]}}))
    m = e["employments"][0]["id"]
    _ok(client.post(f"/api/hr/employments/{m}/pay", headers=H(PAYROLL), json={"kind": "salary", "amount": 100000, "effective_from": D(-5)}))
    hire = e["employments"][0]["events"][0]
    det = {"contract_number": "1", "contract_date": D(-6)}
    # приказ с оплатой: смотреть, согласовывать, знакомиться — только ЗГД, ФД, бухгалтер; работник — может ознакомиться со своим
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(GD), json={**det, "viewer_ids": [ids[CLERK]]}).status_code == 422
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(GD), json={**det, "approver_ids": [ids[CLERK]]}).status_code == 422
    assert client.post(f"/api/hr/events/{hire['id']}/order", headers=H(GD), json={**det, "ack_ids": [ids[CLERK]]}).status_code == 422
    o = _ok(client.post(f"/api/hr/events/{hire['id']}/order", headers=H(GD),
                        json={**det, "viewer_ids": [ids["fd@example.com"]], "approver_ids": [ids["fd@example.com"], ids[PAYROLL]], "ack_ids": [emp_uid]}), 201)
    d = _ok(client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(GD)))
    assert d["status"] == "approval" and [s["user_id"] for s in d["approval"]["steps"]] == [ids["fd@example.com"], ids[PAYROLL]]
    assert d["planned_acks"] == [emp_uid]
    # согласовали по очереди → подписали → работнику назначено ознакомление, он видит свой приказ
    _ok(client.post(f"/api/office/docs/{o['office_doc_id']}/approval/decide", headers=H("fd@example.com"), json={"decision": "approve"}))
    _ok(client.post(f"/api/office/docs/{o['office_doc_id']}/approval/decide", headers=H(PAYROLL), json={"decision": "approve"}))
    meta = _ok(client.post("/api/office/signers", headers=H(GD), json={"entity_id": ru, "full_name": f"Директор {t}", "position": "Генеральный директор",
                                                                     "methods": ["manual"]}), 201)
    sid = next(x["id"] for x in meta["signers"] if x["full_name"] == f"Директор {t}")
    d = _ok(client.post(f"/api/office/docs/{o['office_doc_id']}/sign", headers=H(PAYROLL), json={"signer_id": sid, "method": "manual"}))
    assert d["status"] == "signed" and [a["user_id"] for a in d["acks"]] == [emp_uid]
    assert client.get(f"/api/office/docs/{o['office_doc_id']}", headers=H(CLERK)).status_code == 404
