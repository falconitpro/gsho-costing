"""D-191 (I-0): основа сервиса — справочники, обращения, наряды, события, конфликты без «последней записи», синхронизация."""
import uuid

import pytest
from sqlalchemy import text

from tests.test_api_flow import client, _ok, H, MGR, SVC, FD  # noqa: F401
from tests.test_client_sites import _clients, _site

GD, ENG, LOGI = "evgeniy@dobrov.pro", "engineer@example.com", "logist@example.com"


def _me(c, who):
    return _ok(c.get("/api/auth/me", headers=H(who)))["id"]


def _ev(c, order, kind, who=ENG, base=None, **kw):
    body = {"id": kw.pop("id", str(uuid.uuid4())), "kind": kind, "base_version": order["version"] if base is None else base,
            "payload": kw.pop("payload", {}), **kw}
    return c.post(f"/api/service/orders/{order['id']}/events", headers=H(who), json=body)


def _order(c, wt_code="DIAG-GEN"):
    cl = _clients(c, 1)[0]
    site = _ok(_site(c, cl["id"], f"Карьер {uuid.uuid4().hex[:5]}"), 201)
    refs = _ok(c.get("/api/service/refs", headers=H(SVC)))
    wt = next(x for x in refs["work_types"] if x["code"] == wt_code)
    req = _ok(c.post("/api/service/requests", headers=H(SVC), json={
        "client_id": cl["id"], "site_id": site["id"], "description": "Не запускается двигатель", "machine_down": True, "sla_code": "CRIT"}), 201)
    wo = _ok(c.post("/api/service/orders", headers=H(SVC), json={"request_id": req["id"], "work_type_id": wt["id"]}), 201)
    return req, wo


def _assign(c, wo, who=ENG):
    r = _ok(_ev(c, wo, "assign", who=SVC, payload={"engineer_id": _me(c, who)}))
    return r["order"]


@pytest.fixture(scope="module", autouse=True)
def _refs(client):  # noqa: F811 — после init_db
    from app.db import SessionLocal
    from app.services.service_refs import load_drafts
    with SessionLocal() as db:
        load_drafts(db)
        db.commit()


def test_refs_draft_and_rights(client):  # noqa: F811
    refs = _ok(client.get("/api/service/refs", headers=H(SVC)))
    codes = {x["code"] for x in refs["work_types"]}
    assert {"DIAG-GEN", "TO-250", "TO-1000"} <= codes and refs["drafts"] > 0
    to250 = next(x for x in refs["work_types"] if x["code"] == "TO-250")
    assert to250["is_draft"] and to250["norm_hours"] == 6 and "PB-GR" in to250["permit_codes"] and to250["checklist"]
    assert {x["code"] for x in refs["sla"]} >= {"CRIT", "PLAN"}
    nr = next(x for x in refs["sla"] if x["code"] == "NORM")
    assert nr["restore_hours"] is None                                  # не задано — пусто, а не 0
    assert client.get("/api/service/refs", headers=H(LOGI)).status_code == 403
    _ok(client.get("/api/service/refs", headers=H(ENG)))                  # инженер читает справочники
    body = {**{k: to250[k] for k in ("code", "name", "category", "machine_kind", "norm_hours", "crew_size", "permit_codes", "checklist")},
            "is_draft": False}
    assert client.put(f"/api/service/refs/work_types/{to250['id']}", headers=H(ENG), json=body).status_code == 403
    assert client.put(f"/api/service/refs/work_types/{to250['id']}", headers=H(MGR), json=body).status_code == 403   # READ
    appr = _ok(client.put(f"/api/service/refs/work_types/{to250['id']}", headers=H(SVC), json=body))
    assert appr["is_draft"] is False
    assert client.post("/api/service/refs/work_types", headers=H(SVC), json={**body, "code": f"X-{uuid.uuid4().hex[:5]}",
                                                                            "permit_codes": ["NOPE"]}).status_code == 422
    assert client.post("/api/service/refs/work_types", headers=H(SVC), json=body).status_code == 409


def test_request_order_flow_close_with_signature(client):  # noqa: F811
    req, wo = _order(client)
    assert req["number"].startswith("СО-") and wo["number"].startswith("НР-")
    assert wo["status"] == "draft" and wo["version"] == 1 and len(wo["checklist"]) == 5 and wo["site_name"]
    assert _ok(client.get("/api/service/requests", headers=H(SVC), params={"q": req["number"]}))["items"][0]["status"] == "accepted"
    # инженер не видит неназначенный наряд и не назначает сам
    assert client.get(f"/api/service/orders/{wo['id']}", headers=H(ENG)).status_code == 404
    assert client.get("/api/service/requests", headers=H(ENG)).status_code == 403
    assert _ev(client, wo, "start", who=SVC).json()["event"]["result"] == "rejected"     # инженер не назначен
    wo = _ok(client.get(f"/api/service/orders/{wo['id']}", headers=H(SVC)))
    bad = _ev(client, wo, "assign", who=SVC, payload={"engineer_id": _me(client, MGR)}).json()["event"]
    assert bad["result"] == "rejected" and "инженера" in bad["reason"]
    wo = _assign(client, wo)
    assert wo["status"] == "assigned" and wo["engineer_name"]
    assert [x["id"] for x in _ok(client.get("/api/service/orders", headers=H(ENG)))["items"]].count(wo["id"]) == 1
    assert _ev(client, wo, "assign", who=ENG, payload={"engineer_id": _me(client, ENG)}).status_code == 403

    wo = _ok(_ev(client, wo, "start"))["order"]
    assert wo["status"] == "in_progress"
    r = _ok(_ev(client, wo, "ready"))
    assert r["event"]["result"] == "rejected" and "чек-листа" in r["event"]["reason"]
    wo = r["order"]
    for item in wo["checklist"]:
        wo = _ok(_ev(client, wo, "checklist", payload={"item_id": item["id"], "done": True}))["order"]
    assert all(i["done"] and i["done_by_name"] for i in wo["checklist"])
    r = _ok(_ev(client, wo, "close"))
    assert r["event"]["result"] == "rejected" and "подпись" in r["event"]["reason"]
    wo = _ok(_ev(client, r["order"], "close", payload={"signed_by_name": "Главный механик Петров"}))["order"]
    assert wo["status"] == "closed" and wo["signed_by_name"] and wo["closed_at"]
    assert _ok(client.get("/api/service/requests", headers=H(SVC), params={"q": req["number"]}))["items"][0]["status"] == "done"
    assert _ev(client, wo, "reopen").json()["event"]["result"] == "rejected"           # закрытый наряд не меняется
    # история — в реестре изменений
    ch = _ok(client.get("/api/changes", headers=H(GD), params={"entity": "work_order", "limit": 2000}))
    assert any(e["entity_id"] == wo["id"] and e["action"] == "close" for e in ch["events"])


def test_offline_close_becomes_ready_and_idempotent(client):  # noqa: F811
    _, wo = _order(client, "CONS-REM")
    wo = _assign(client, wo)
    wo = _ok(_ev(client, wo, "start"))["order"]
    wo = _ok(_ev(client, wo, "checklist", payload={"item_id": wo["checklist"][0]["id"], "done": True}))["order"]
    eid = str(uuid.uuid4())
    r1 = _ok(_ev(client, wo, "close", id=eid, offline=True, device_id="phone-1", payload={"signed_by_name": "Иванов"}))
    assert r1["order"]["status"] == "ready_to_close" and "Без связи" in r1["event"]["reason"]
    assert r1["order"]["signed_by_name"] == "Иванов"
    r2 = _ok(_ev(client, wo, "close", id=eid, offline=True, device_id="phone-1", payload={"signed_by_name": "Иванов"}))
    assert r2["event"]["id"] == eid and r2["order"]["version"] == r1["order"]["version"]   # повтор не применён второй раз
    assert _ev(client, r2["order"], "close", who=SVC, id=eid).status_code == 422          # чужой id
    wo = _ok(_ev(client, r2["order"], "close"))["order"]                                  # на связи — закрыт
    assert wo["status"] == "closed"


def test_conflicts_without_last_write_wins(client):  # noqa: F811
    _, wo = _order(client)
    wo = _assign(client, wo)
    stale = wo                                          # устройство инженера видело эту версию и ушло без связи
    wo = _ok(_ev(client, wo, "cancel", who=SVC, payload={"reason": "Клиент отменил"}))["order"]
    assert wo["status"] == "cancelled"
    # заметка по старой версии — объединяется
    r = _ok(_ev(client, stale, "note", offline=True, payload={"text": "Приехал на площадку"}))
    assert r["event"]["result"] == "merged"     # заметка с поля не теряется и после отмены наряда
    _, wo = _order(client)
    wo = _assign(client, wo)
    stale = wo
    wo = _ok(_ev(client, wo, "plan", who=SVC, payload={"planned_start": "2026-12-01T08:00:00+07:00"}))["order"]
    # смена статуса по старой версии: после неё были только сроки — применяется (merged)
    r = _ok(_ev(client, stale, "start", offline=True))
    assert r["event"]["result"] == "merged" and r["order"]["status"] == "in_progress"
    stale2 = r["order"]
    wo = _ok(_ev(client, r["order"], "hold", who=SVC))["order"]
    # инженер по старой версии отправляет «готов» — после неё статус меняли → конфликт, наряд не меняется
    r = _ok(_ev(client, stale2, "ready", offline=True))
    assert r["event"]["result"] == "conflict" and r["order"]["status"] == "on_hold" and r["order"]["conflicts"] == 1
    cid = r["event"]["id"]
    # чек-лист: два разных значения одного пункта по одной версии — второе конфликт
    item = r["order"]["checklist"][0]["id"]
    base = r["order"]
    wo = _ok(_ev(client, base, "resume", who=SVC))["order"]
    a = _ok(_ev(client, wo, "checklist", payload={"item_id": item, "done": True}))
    b = _ok(_ev(client, wo, "checklist", who=SVC, payload={"item_id": item, "done": False}))
    assert a["event"]["result"] == "applied" and b["event"]["result"] == "conflict"
    # решает руководитель; инженер решать не может
    wo = b["order"]
    assert _ev(client, wo, "resolve", payload={"event_id": cid, "decision": "dismiss"}).status_code == 403
    d = _ok(_ev(client, wo, "resolve", who=SVC, payload={"event_id": cid, "decision": "dismiss"}))
    assert d["event"]["result"] == "applied" and d["order"]["conflicts"] == 1
    again = _ok(_ev(client, d["order"], "resolve", who=SVC, payload={"event_id": cid, "decision": "accept"}))
    assert again["event"]["result"] == "rejected"                                         # уже решён
    future = _ok(_ev(client, d["order"], "note", base=d["order"]["version"] + 5, payload={"text": "x"}))
    assert future["event"]["result"] == "rejected"


def test_sync_push_pull_and_append_only(client):  # noqa: F811
    _, wo = _order(client)
    wo = _assign(client, wo)
    other = _assign(client, _order(client)[1], who=SVC)                                    # наряд другого исполнителя
    cur = _ok(client.get("/api/service/sync/pull", headers=H(ENG), params={"cursor": 0, "limit": 2000}))["cursor"]
    e1, e2 = str(uuid.uuid4()), str(uuid.uuid4())
    res = _ok(client.post("/api/service/sync/push", headers=H(ENG), json={"events": [
        {"order_id": wo["id"], "id": e1, "kind": "start", "base_version": wo["version"], "offline": True, "device_id": "phone-1"},
        {"order_id": wo["id"], "id": e2, "kind": "note", "base_version": wo["version"] + 1, "payload": {"text": "Начал"}, "offline": True},
        {"order_id": other["id"], "id": str(uuid.uuid4()), "kind": "start", "base_version": other["version"]},
    ]}))["results"]
    assert [x["result"] for x in res] == ["applied", "applied", "error"] and res[2]["http"] == 404
    pulled = _ok(client.get("/api/service/sync/pull", headers=H(ENG), params={"cursor": cur}))
    assert [o["id"] for o in pulled["orders"]] == [wo["id"]] and pulled["cursor"] > cur
    assert pulled["orders"][0]["status"] == "in_progress"
    assert client.get(f"/api/service/orders/{other['id']}", headers=H(ENG)).status_code == 404
    _ok(client.get(f"/api/service/orders/{other['id']}", headers=H(FD)))                 # ФД читает
    assert _ev(client, other, "note", who=FD, payload={"text": "x"}).status_code == 403
    from app.db import SessionLocal
    with SessionLocal() as db:
        with pytest.raises(Exception, match="только добавление"):
            db.execute(text("UPDATE work_order_events SET reason = 'x' WHERE id = :i"), {"i": e1})
        db.rollback()


def test_i1_card_actions_engineers_and_site_for_engineer(client):  # noqa: F811
    _, wo = _order(client)
    lst = _ok(client.get("/api/service/orders", headers=H(SVC)))
    assert lst["statuses"]["ready_to_close"] == "Готов к закрытию" and lst["counts"]["draft"] >= 1
    row = next(x for x in lst["items"] if x["id"] == wo["id"])
    assert "assign" in row["actions"] and "start" not in row["actions"]                   # инженер не назначен
    engs = _ok(client.get("/api/service/engineers", headers=H(SVC)))
    me = _me(client, ENG)
    assert any(e["id"] == me and e["is_engineer"] for e in engs) and not any(e["id"] == _me(client, MGR) for e in engs)
    assert client.get("/api/service/engineers", headers=H(ENG)).status_code == 403
    _assign(client, wo)
    card = _ok(client.get(f"/api/service/orders/{wo['id']}", headers=H(ENG)))
    assert card["site"]["name"] and "map_url" in card["site"] and card["request"]["machine_down"] is True
    assert set(card["actions"]) >= {"start", "note", "checklist"} and "assign" not in card["actions"] and "cancel" not in card["actions"]
    assert _ok(client.get("/api/service/orders", headers=H(ENG)))["counts"]["assigned"] >= 1


def test_counts_match_visible_orders(client):  # noqa: F811
    lst = _ok(client.get("/api/service/orders", headers=H(SVC), params={"limit": 500}))
    by = {}
    for o in lst["items"]:
        by[o["status"]] = by.get(o["status"], 0) + 1
    assert {k: v for k, v in lst["counts"].items() if v} == by
