"""D-128: Канцелярия — регистрация входящих, номера по юр. лицам, сроки, исполнитель, ответ, приказы, подписанты, права."""
from datetime import date

from app.core.workflow import add_business_days
from tests.test_api_flow import client, _ok, H, MGR, CD, LAW, GD  # noqa: F401

CLERK = "office@example.com"


def _meta(client, who=CLERK):
    return _ok(client.get("/api/office/meta", headers=H(who)))


def test_office_incoming_outgoing_flow(client):
    m = _meta(client)
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    assert ru["prefix"] == "РФ" and m["can_edit"]
    mgr = next(u for u in m["users"] if u["name"].startswith("Бочаров"))
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Канцелярия"}), 201)["id"]
    # входящий запрос — номер сразу, срок 2 рабочих дня, исполнитель — менеджер
    d = _ok(client.post("/api/office/docs", headers=H(CLERK), json={
        "entity_id": ru["id"], "kind": "in", "letter_type": "request", "channel": "email", "subject": "Запрос по высоте выработки",
        "counterparty": cl["name"], "sender_number": "15/11", "executor_id": mgr["id"], "deal_ids": [did], "deal_section": "sale"}), 201)
    y = date.today().year
    # D-129: без скана исполнитель не назначается — письмо ждёт файла
    assert d["number"] == f"РФ-ВХ-{y}-0001" and d["status"] == "registered" and not d["has_scan"]
    d = _ok(client.post(f"/api/office/docs/{d['id']}/files", headers=H(CLERK), files={"file": ("вх.pdf", b"%PDF-1.4 in", "application/pdf")}, data={"kind": "original"}), 201)
    assert d["status"] == "assigned" and d["has_scan"]
    assert d["due_on"] == add_business_days(date.today(), 2).isoformat()
    # второе входящее — следующий номер; претензия — 5 рабочих дней
    d2 = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "claim", "channel": "post_reg", "tracking": "80085012345678", "subject": "Претензия"}), 201)
    assert d2["number"] == f"РФ-ВХ-{y}-0002" and d2["due_on"] == add_business_days(date.today(), 5).isoformat()
    # запрос госоргана без срока — нельзя
    assert client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "gov", "channel": "edo", "subject": "ФНС"}).status_code == 422
    # D-131: источник поступления обязателен
    assert client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "request", "subject": "Без источника"}).status_code == 422
    # письмо — в ленте сделки, раздел «Продажа»
    feed = _ok(client.get(f"/api/deals/{did}/feed", headers=H(MGR), params={"section": "sale"}))
    assert any(i["kind"] == "office_in" and d["number"] in i["subject"] for i in feed["items"])
    # у исполнителя — в «Что сделать»; журнал ему закрыт, свой документ открыт
    assert any(t["kind"] == "office" for t in _ok(client.get("/api/workplace", headers=H(MGR)))["todo"])
    assert client.get("/api/office/docs", headers=H(MGR)).status_code == 403
    assert len(_ok(client.get("/api/office/docs", headers=H(MGR), params={"mine": True}))["items"]) == 1
    _ok(client.post(f"/api/office/docs/{d['id']}/action", headers=H(MGR), json={"action": "take"}))
    # исполнитель готовит ответ; без подписанта не подписать
    ans = _ok(client.post("/api/office/docs", headers=H(MGR), json={"entity_id": ru["id"], "kind": "out", "subject": "Ответ на запрос",
                                                                      "reply_to_id": d["id"], "body": "Высота 4,2 м принята"}), 201)
    assert ans["status"] == "draft" and ans["number"] == "" and ans["deal_ids"] == [did]
    # подписанта заводит ЗГД, делопроизводителю нельзя
    assert client.post("/api/office/signers", headers=H(CLERK), json={"entity_id": ru["id"], "full_name": "Добров Е."}).status_code == 403
    m = _ok(client.post("/api/office/signers", headers=H(GD), json={"entity_id": ru["id"], "full_name": "Добров Евгений", "position": "ЗГД",
                                                                    "basis": "Доверенность № 5 от 01.01.2026", "methods": ["manual", "facsimile"]}), 201)
    s = next(x for x in m["signers"] if x["full_name"] == "Добров Евгений" and x["entity_id"] == ru["id"])   # другие тесты тоже заводят подписантов
    assert client.post(f"/api/office/docs/{ans['id']}/sign", headers=H(CLERK), json={"signer_id": s["id"], "method": "facsimile"}).status_code == 409
    sg = _ok(client.post(f"/api/office/docs/{ans['id']}/sign", headers=H(CLERK), json={"signer_id": s["id"], "method": "manual"}))
    assert sg["number"] == f"РФ-ИСХ-{y}-0001" and sg["status"] == "signed"
    _ok(client.post(f"/api/office/docs/{ans['id']}/files", headers=H(CLERK), files={"file": ("скан.pdf", b"%PDF-1.4 test", "application/pdf")}, data={"kind": "scan"}), 201)
    sent = _ok(client.post(f"/api/office/docs/{ans['id']}/action", headers=H(CLERK), json={"action": "sent", "channel": "email"}))
    assert sent["status"] == "sent"
    # входящее закрыто ответом; из «Что сделать» ушло
    src = _ok(client.get(f"/api/office/docs/{d['id']}", headers=H(CLERK)))
    assert src["status"] == "closed" and sg["number"] in src["close_note"] and src["replies"][0]["id"] == ans["id"]
    assert not any(t["kind"] == "office" for t in _ok(client.get("/api/workplace", headers=H(MGR)))["todo"])
    # закрыть входящее без пояснения нельзя
    assert client.post(f"/api/office/docs/{d2['id']}/action", headers=H(CLERK), json={"action": "close"}).status_code == 422


def test_office_orders_and_rights(client):
    m = _meta(client, GD)
    kg = next(e for e in m["entities"] if e["code"] == "kg")
    y = date.today().year
    s = _ok(client.post("/api/office/signers", headers=H(GD), json={"entity_id": kg["id"], "full_name": "Директор КГ", "position": "Директор",
                                                                    "basis": "Устав"}), 201)["signers"]
    sid = next(x["id"] for x in s if x["entity_id"] == kg["id"])
    o = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": kg["id"], "kind": "order", "category": "od", "subject": "О порядке регистрации писем",
                                                                      "body": "Приказываю…"}), 201)
    o = _ok(client.post(f"/api/office/docs/{o['id']}/sign", headers=H(CLERK), json={"signer_id": sid}))
    assert o["number"] == f"КГ-ПР-ОД-{y}-0001"
    o = _ok(client.post(f"/api/office/docs/{o['id']}/action", headers=H(CLERK), json={"action": "in_force"}))
    assert o["status"] == "in_force" and o["effective_on"]
    # подписант другого юр. лица — нельзя
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    r = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "directive", "subject": "Распоряжение"}), 201)
    assert client.post(f"/api/office/docs/{r['id']}/sign", headers=H(CLERK), json={"signer_id": sid}).status_code == 422
    # приказ по личному составу — юристу не виден, КД журнал видит, но не регистрирует
    hr = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "order", "category": "hr", "subject": "О приёме"}), 201)
    assert client.get(f"/api/office/docs/{hr['id']}", headers=H(LAW)).status_code == 404
    assert all(i["id"] != hr["id"] for i in _ok(client.get("/api/office/docs", headers=H(LAW)))["items"])
    assert client.post("/api/office/docs", headers=H(CD), json={"entity_id": ru["id"], "kind": "in", "subject": "x"}).status_code == 403
    assert len(_ok(client.get("/api/office/docs", headers=H(CD), params={"kind": "order"}))["items"]) >= 1
    # отмена — с причиной; номер остаётся
    assert client.post(f"/api/office/docs/{r['id']}/action", headers=H(CLERK), json={"action": "cancel"}).status_code == 422
    c = _ok(client.post(f"/api/office/docs/{r['id']}/action", headers=H(CLERK), json={"action": "cancel", "note": "ошибочно создано"}))
    assert c["status"] == "cancelled"
