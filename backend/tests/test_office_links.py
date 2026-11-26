"""D-136–D-140: письма ↔ договоры и претензии, поиск по тексту, «Оформить письмом» из ленты сделки, ознакомление с приказами."""
from datetime import datetime, timedelta, timezone

from tests.test_api_flow import client, _ok, H, MGR, CD, GD, LOG  # noqa: F401

CLERK = "office@example.com"
LAW = "lawyer@example.com"
PDF = ("скан.pdf", b"%PDF-1.4 test", "application/pdf")


def test_links_search_from_feed(client):
    m = _ok(client.get("/api/office/meta", headers=H(CLERK)))
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    ent = _ok(client.get("/api/legal/contracts", headers=H(LAW)))["entities"][0]
    k = _ok(client.post("/api/legal/contracts", headers=H(CD), json={"kind": "logistics", "own_entity_id": ent["id"], "cp_name": "ООО «ТрансЛинк»",
                                                                      "subject": "перевозка", "amount": 1000, "currency": "rub"}), 201)
    # подбор договора и привязка
    assert any(x["id"] == k["id"] for x in _ok(client.get("/api/office/lookup", headers=H(CLERK), params={"q": "ТрансЛинк"})))
    d = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "request", "channel": "email",
                                                                    "subject": "Запрос по перевозке", "contract_id": k["id"],
                                                                    "body": "Просим подтвердить график вывоза техники с площадки Кедровый"}), 201)
    full = _ok(client.get(f"/api/office/docs/{d['id']}", headers=H(CLERK)))
    assert full["contract"]["id"] == k["id"]
    assert any(x["id"] == d["id"] for x in _ok(client.get("/api/office/linked", headers=H(CLERK), params={"contract_id": k["id"]})))
    assert client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "channel": "email", "subject": "x",
                                                                    "contract_id": "nope"}).status_code == 422
    # поиск по тексту письма и по имени вложения
    assert any(x["id"] == d["id"] for x in _ok(client.get("/api/office/docs", headers=H(CLERK), params={"q": "Кедровый"}))["items"])
    _ok(client.post(f"/api/office/docs/{d['id']}/files", headers=H(CLERK), files={"file": ("график_вывоза_ноябрь.pdf", b"%PDF-1.4", "application/pdf")},
                    data={"kind": "scan"}), 201)
    assert any(x["id"] == d["id"] for x in _ok(client.get("/api/office/docs", headers=H(CLERK), params={"q": "график_вывоза"}))["items"])
    # «Оформить письмом» из сообщения ленты сделки
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Лента"}), 201)["id"]
    f = _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "sale", "body": "Просим подтвердить комплектацию ковша 12 м³"}), 201)
    msg = next(i for i in f["items"] if i["kind"] == "comment")
    o = _ok(client.post("/api/office/docs/from-feed", headers=H(MGR), json={"message_id": msg["id"]}), 201)
    assert o["kind"] == "out" and o["status"] == "draft" and o["deal_ids"] == [did] and o["deal_section"] == "sale" and "12 м³" in o["body"]
    assert o["can_edit"]
    assert client.post("/api/office/docs/from-feed", headers=H(MGR), json={"message_id": "nope"}).status_code == 404


def test_acknowledgment(client):
    m = _ok(client.get("/api/office/meta", headers=H(CLERK)))
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    sg = _ok(client.post("/api/office/signers", headers=H(GD), json={"entity_id": ru["id"], "full_name": "Подписантов Иван", "position": "ГД"}), 201)["signers"]
    sg = next(x for x in sg if x["full_name"] == "Подписантов Иван")
    od = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "order", "category": "ah", "subject": "О пропускном режиме"}), 201)
    assert client.post(f"/api/office/docs/{od['id']}/acks", headers=H(CLERK), json={"everyone": True}).status_code == 409   # ещё не подписан
    # обязательная схема для приказов могла остаться от других тестов — согласуем при необходимости
    r = client.post(f"/api/office/docs/{od['id']}/sign", headers=H(CLERK), json={"signer_id": sg["id"], "method": "manual"})
    assert r.status_code == 200, r.text
    me_mgr = _ok(client.get("/api/auth/me", headers=H(MGR)))
    a = _ok(client.post(f"/api/office/docs/{od['id']}/acks", headers=H(CLERK), json={"user_ids": [me_mgr["id"]], "roles": ["logistician"]}))
    assert a["acks_total"] >= 2 and a["acks_done"] == 0
    # менеджер видит приказ и задачу «Ознакомиться»
    assert any(t["kind"] == "office" and "Ознакомиться" in str(t) for t in _ok(client.get("/api/workplace", headers=H(MGR)))["todo"])
    v = _ok(client.get(f"/api/office/docs/{od['id']}", headers=H(MGR)))
    assert v["my_ack"] == {"acked_at": None}
    v = _ok(client.post(f"/api/office/docs/{od['id']}/ack", headers=H(MGR)))
    assert v["my_ack"]["acked_at"] and v["acks_done"] == 1
    # отметку не снять; неознакомленного можно убрать
    assert client.delete(f"/api/office/docs/{od['id']}/acks/{me_mgr['id']}", headers=H(CLERK)).status_code == 409
    me_log = _ok(client.get("/api/auth/me", headers=H(LOG)))
    # напоминание через 3 рабочих дня
    from app.calc.calendar import DEFAULT_CALENDAR as C
    from app.db import SessionLocal
    from app.models import OfficeDoc
    from app.services import office_watch
    with SessionLocal() as db:
        doc = db.get(OfficeDoc, od["id"])
        # дата — по Москве, как в office_watch (в UTC после 21:00 это ещё вчера — D-193)
        start = datetime.fromisoformat(next(x for x in doc.acks if x["user_id"] == me_log["id"])["at"]).astimezone(office_watch.MSK).date()
        day = C.add_working_days(start, 3)
        n = office_watch._ack_reminders(db, day)
        db.commit()
        assert n >= 1 and office_watch._ack_reminders(db, day) == 0
    _ok(client.delete(f"/api/office/docs/{od['id']}/acks/{me_log['id']}", headers=H(CLERK)))
