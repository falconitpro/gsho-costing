"""D-157: исправления по аудиту версии 8a146bd (AUD-01…06), закрепление сотрудника за направлением, замещение и несколько ролей."""
from datetime import date, timedelta

from tests.test_api_flow import client, _ok, H, MGR, CD  # noqa: F401
from tests.test_negotiation import _approved
from tests.test_workplace import FD, GD

B = "/api/management"


def _id(c, email):
    return c.get("/api/auth/me", headers=H(email)).json()["id"]


def test_aud01_assignees_only_gd_and_heads(client):
    cd, fd = _id(client, CD), _id(client, FD)
    _ok(client.put(f"{B}/directions", headers=H(GD), json={"heads": {"sales": cd, "finance": None}}))
    full = _ok(client.get(f"{B}/assignees", headers=H(GD)))
    assert full["users"] and "position" in full["users"][0] and full["heads"]["sales"] == cd
    head = _ok(client.get(f"{B}/assignees", headers=H(CD)))                       # руководитель — только свои, без должностей и оргструктуры
    assert head["heads"] == {} and [d["key"] for d in head["directions"]] == ["sales"]
    assert all(set(u) == {"id", "name"} for u in head["users"]) and cd not in {u["id"] for u in head["users"]}
    assert _id(client, MGR) in {u["id"] for u in head["users"]} and fd not in {u["id"] for u in head["users"]}
    for who in (MGR, FD, "lawyer@example.com", "logist@example.com"):
        assert client.get(f"{B}/assignees", headers=H(who)).status_code == 403


def test_aud02_direction_validated_and_pinning(client):
    cd, mgr, fd = _id(client, CD), _id(client, MGR), _id(client, FD)
    _ok(client.put(f"{B}/directions", headers=H(GD), json={"heads": {"sales": cd, "finance": fd}}))
    due = (date.today() + timedelta(days=3)).isoformat()
    base = {"title": "x", "expected_result": "y", "mode": "direct", "assignee_id": mgr, "due_on": due}
    r = client.post(f"{B}/assignments", headers=H(GD), json={**base, "direction_key": "finance"})
    assert r.status_code == 422 and "не относится" in r.json()["detail"]          # менеджер — не финансы: руководитель финансов не получит видимость
    a = _ok(client.post(f"{B}/assignments", headers=H(GD), json=base))
    assert a["direction_key"] == "sales" and a["head_id"] == cd                    # направление — с сервера
    # закрепление: менеджер фактически работает в финансах → руководитель финансов, а не продаж
    _ok(client.put(f"{B}/directions", headers=H(GD), json={"members": {mgr: "finance"}}))
    b = _ok(client.post(f"{B}/assignments", headers=H(GD), json=base))
    assert b["direction_key"] == "finance" and b["head_id"] == fd
    assert client.post(f"{B}/assignments", headers=H(GD), json={**base, "direction_key": "sales"}).status_code == 422
    assert client.put(f"{B}/directions", headers=H(CD), json={"members": {mgr: "sales"}}).status_code == 403
    _ok(client.put(f"{B}/directions", headers=H(GD), json={"members": {mgr: None}}))
    # передать исполнителю можно только сотруднику своего подразделения
    h = _ok(client.post(f"{B}/assignments", headers=H(GD), json={**base, "mode": "head", "direction_key": "sales", "assignee_id": cd}))
    assert client.post(f"{B}/assignments/{h['id']}/action", headers=H(CD), json={"action": "delegate", "executor_id": fd}).status_code == 422
    assert _ok(client.post(f"{B}/assignments/{h['id']}/action", headers=H(CD), json={"action": "delegate", "executor_id": mgr}))["executor_id"] == mgr


def test_aud05_expected_result_required(client):
    mgr = _id(client, MGR)
    due = (date.today() + timedelta(days=2)).isoformat()
    r = client.post(f"{B}/assignments", headers=H(GD), json={"title": "x", "assignee_id": mgr, "due_on": due})
    assert r.status_code == 422 and "ожидаемый результат" in r.json()["detail"]
    assert client.post(f"{B}/assignments", headers=H(GD), json={"title": "x", "assignee_id": mgr, "due_on": due, "expected_result": "   "}).status_code == 422
    e = _ok(client.post(f"{B}/assignments", headers=H(GD), json={"title": "Пояснить", "assignee_id": mgr, "due_on": due, "kind": "explain"}))
    assert e["expected_result"].startswith("Письменно")                           # у пояснения критерий по умолчанию


def test_aud03_each_overdue_payment_is_own_signal_and_aud06_partial(client):
    from app.db import SessionLocal
    from app.models import ExecutionStage
    did, _vid, _ = _approved(client)
    _ok(client.post(f"/api/deals/{did}/kp-sent", headers=H(MGR), json={"recipients": ["a@example.com"]}))
    _ok(client.post(f"/api/deals/{did}/outcome", headers=H(CD), json={"outcome": "won", "reason": "цена"}))
    _ok(client.get(f"/api/deals/{did}/execution/stages", headers=H(CD)))
    with SessionLocal() as db:
        pays = db.query(ExecutionStage).filter(ExecutionStage.deal_id == did, ExecutionStage.code.in_(("client_advance", "client_final"))).all()
        assert len(pays) == 2
        for i, s in enumerate(pays):
            s.planned_on, s.refined_on, s.actual_on = date.today() - timedelta(days=40 + i), None, None
        db.commit()
    ex = _ok(client.get("/api/workplace/executive", headers=H(GD)))["executive"]
    mine = [e for e in ex["escalations"] if e["group"] == f"deal:{did}" and (":pay:" in e["key"] or ":obl:" in e["key"])]
    assert len(mine) >= 2 and len({e["key"] for e in mine}) == len(mine)          # оба платежа видны, не схлопнулись
    assert all(e["priority"] == "critical" for e in mine)
    # AUD-06: если у части сделок нет цены — «частичные данные» и «сумма по X из Y», иначе — подтверждено
    for k in (x for x in ex["kpis"] if x["key"] in ("active", "risk", "won")):
        if k["data_quality"] == "partial":
            assert "сумма по" in k["quality_note"] and "нет цены" in k["quality_note"]
        else:
            assert k["data_quality"] == "confirmed"


def test_deputy_and_multirole_head_rights(client):
    """Замещение: заместитель руководителя видит и действует как руководитель; у сотрудника с двумя ролями — оба подразделения."""
    from app.db import SessionLocal
    from app.models import User
    cd, mgr, fd = _id(client, CD), _id(client, MGR), _id(client, FD)
    _ok(client.put(f"{B}/directions", headers=H(GD), json={"heads": {"sales": cd}, "members": {mgr: None}}))
    with SessionLocal() as db:
        u = db.get(User, cd)
        u.deputy_id, u.deputy_from, u.deputy_to = fd, date.today() - timedelta(days=1), date.today() + timedelta(days=3)
        db.commit()
    due = (date.today() + timedelta(days=4)).isoformat()
    h = _ok(client.post(f"{B}/assignments", headers=H(GD), json={"title": "Отчёт по продажам", "expected_result": "отчёт", "mode": "head",
                                                                 "direction_key": "sales", "assignee_id": cd, "due_on": due}))
    view = _ok(client.get(f"{B}/assignments/{h['id']}", headers=H(FD)))            # замещающий видит поручение руководителя
    assert "assignee" in view["my_role"] and "accept" in view["actions"]
    assert _ok(client.post(f"{B}/assignments/{h['id']}/action", headers=H(FD), json={"action": "accept"}))["status"] == "accepted"
    assert {u["id"] for u in _ok(client.get(f"{B}/assignees", headers=H(FD)))["users"]} >= {mgr}   # и может передать исполнителю
    d = _ok(client.get(f"{B}/directions", headers=H(GD)))
    sales = next(x for x in d["items"] if x["key"] == "sales")
    assert sales["deputy"] and sales["deputy"]["id"] == fd and sales["deputy"]["temporary"]
    with SessionLocal() as db:
        u = db.get(User, cd)
        u.deputy_id = u.deputy_from = u.deputy_to = None
        m = db.get(User, mgr)
        roles = list(m.roles)
        m.roles = roles + ["logistician"]
        db.commit()
    try:
        users = {u["id"]: u for u in _ok(client.get(f"{B}/assignees", headers=H(GD)))["users"]}
        assert users[mgr]["directions"] == ["sales", "logistics"]
        a = _ok(client.post(f"{B}/assignments", headers=H(GD), json={"title": "x", "expected_result": "y", "assignee_id": mgr, "direction_key": "logistics", "due_on": due}))
        assert a["direction_key"] == "logistics"
    finally:
        with SessionLocal() as db:
            m = db.get(User, mgr)
            m.roles = roles
            db.commit()
