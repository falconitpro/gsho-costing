"""D-129/130/131: схемы согласования, рассмотрение и резолюция, обязательный скан и источник поступления, комментарии."""
from tests.test_api_flow import client, _ok, H, MGR, CD, FD, LAW, GD  # noqa: F401

CLERK = "office@example.com"
PDF = ("скан.pdf", b"%PDF-1.4 test", "application/pdf")


def _m(client, who=CLERK):
    return _ok(client.get("/api/office/meta", headers=H(who)))


def _uid(m, prefix):
    return next(u["id"] for u in m["users"] if u["name"].startswith(prefix))


def test_review_resolution_return(client):
    m = _m(client)
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    gd, mgr = _uid(m, "Добров"), _uid(m, "Бочаров")
    d = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "in", "letter_type": "request", "channel": "email",
                                                                    "tracking": "info@client.ru", "subject": "Запрос КП", "urgent": True, "pages": 2}), 201)
    assert d["tracking"] == "info@client.ru" and d["urgent"]
    # на рассмотрение — только со сканом
    assert client.post(f"/api/office/docs/{d['id']}/review", headers=H(CLERK), json={"reviewer_id": gd}).status_code == 409
    _ok(client.post(f"/api/office/docs/{d['id']}/files", headers=H(CLERK), files={"file": PDF}, data={"kind": "scan"}), 201)
    r = _ok(client.post(f"/api/office/docs/{d['id']}/review", headers=H(CLERK), json={"reviewer_id": gd, "note": "срочно"}))
    assert r["status"] == "review" and r["reviewer"].startswith("Добров")
    # у руководителя — «Рассмотреть»; резолюцию накладывает он, не менеджер
    todo = _ok(client.get("/api/workplace", headers=H(GD)))["todo"]
    assert any(t["kind"] == "office" and "Рассмотреть" in str(t) for t in todo)
    assert client.post(f"/api/office/docs/{d['id']}/resolution", headers=H(MGR), json={"kind": "execute", "executor_id": mgr}).status_code in (403, 404)
    assert client.post(f"/api/office/docs/{d['id']}/resolution", headers=H(GD), json={"kind": "execute"}).status_code == 422
    r = _ok(client.post(f"/api/office/docs/{d['id']}/resolution", headers=H(GD), json={"kind": "execute", "executor_id": mgr, "text": "Подготовить КП"}))
    assert r["status"] == "assigned" and r["executor_id"] == mgr and r["resolutions"][0]["executor"].startswith("Бочаров")
    # исполнитель возвращает: «прошу продлить» — снова у руководителя
    rt = _ok(client.post(f"/api/office/docs/{d['id']}/return", headers=H(MGR), json={"reason": "нужен расчёт завода", "new_due": "2030-01-10"}))
    assert rt["status"] == "review" and "Возврат" in rt["comments"][-1]["text"]
    # «к сведению» — закрыто
    r = _ok(client.post(f"/api/office/docs/{d['id']}/resolution", headers=H(GD), json={"kind": "info", "text": "ознакомить отдел продаж"}))
    assert r["status"] == "closed" and r["close_note"].startswith("к сведению")
    # комментарий
    c = _ok(client.post(f"/api/office/docs/{d['id']}/comments", headers=H(CLERK), json={"text": "Оригинал в папке 3"}), 201)
    assert c["comments"][-1]["text"] == "Оригинал в папке 3"


def test_approval_routes(client):
    m = _m(client)
    ru = next(e for e in m["entities"] if e["code"] == "ru")
    # типовая обязательная схема для исходящих: юрист (по роли) → ФД (по роли), по очереди
    assert client.post("/api/office/routes", headers=H(CLERK), json={"name": "Схема", "steps": [{"role": "lawyer"}]}).status_code == 403
    m = _ok(client.post("/api/office/routes", headers=H(GD), json={"name": "Исходящие — юрист и ФД", "kinds": ["out"], "mode": "seq", "required": True,
                                                                  "steps": [{"role": "lawyer", "title": "юр. проверка"}, {"role": "fd"}]}), 201)
    route = next(r for r in m["routes"] if r["name"] == "Исходящие — юрист и ФД")
    sg = _ok(client.post("/api/office/signers", headers=H(GD), json={"entity_id": ru["id"], "full_name": "Проверкин Пётр", "position": "ЗГД"}), 201)["signers"]
    sg = next(x for x in sg if x["full_name"] == "Проверкин Пётр")
    o = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "out", "subject": "Письмо заводу", "body": "Текст"}), 201)
    # схема обязательна — подписать без согласования нельзя
    assert client.post(f"/api/office/docs/{o['id']}/sign", headers=H(CLERK), json={"signer_id": sg["id"], "method": "manual"}).status_code == 409
    a = _ok(client.post(f"/api/office/docs/{o['id']}/approval", headers=H(CLERK), json={"route_id": route["id"]}))
    assert a["status"] == "approval" and [s["status"] for s in a["approval"]["steps"]] == ["pending", "waiting"]
    # ФД пока не его очередь; юрист отклоняет без комментария — нельзя, с комментарием — черновик
    assert client.post(f"/api/office/docs/{o['id']}/approval/decide", headers=H(FD), json={"decision": "approve"}).status_code == 403
    assert client.post(f"/api/office/docs/{o['id']}/approval/decide", headers=H(LAW), json={"decision": "reject"}).status_code == 422
    r = _ok(client.post(f"/api/office/docs/{o['id']}/approval/decide", headers=H(LAW), json={"decision": "reject", "comment": "уточнить сумму"}))
    assert r["status"] == "draft"
    _ok(client.patch(f"/api/office/docs/{o['id']}", headers=H(CLERK), json={"body": "Текст, сумма 100"}))
    _ok(client.post(f"/api/office/docs/{o['id']}/approval", headers=H(CLERK), json={"route_id": route["id"]}))
    # на согласовании текст не меняется
    assert client.patch(f"/api/office/docs/{o['id']}", headers=H(CLERK), json={"body": "другой"}).status_code == 409
    _ok(client.post(f"/api/office/docs/{o['id']}/approval/decide", headers=H(LAW), json={"decision": "approve"}))
    r = _ok(client.post(f"/api/office/docs/{o['id']}/approval/decide", headers=H(FD), json={"decision": "approve", "comment": "ок"}))
    assert r["status"] == "approved" and r["approval"]["round"] == 2
    s = _ok(client.post(f"/api/office/docs/{o['id']}/sign", headers=H(CLERK), json={"signer_id": sg["id"], "method": "manual"}))
    assert s["status"] == "signed" and s["number"]
    # отправка: без скана и без способа отправки нельзя
    assert client.post(f"/api/office/docs/{o['id']}/action", headers=H(CLERK), json={"action": "sent"}).status_code == 409
    _ok(client.post(f"/api/office/docs/{o['id']}/files", headers=H(CLERK), files={"file": PDF}, data={"kind": "scan"}), 201)
    assert client.post(f"/api/office/docs/{o['id']}/action", headers=H(CLERK), json={"action": "sent"}).status_code == 422
    _ok(client.post(f"/api/office/docs/{o['id']}/action", headers=H(CLERK), json={"action": "sent", "channel": "post_reg"}))
    # ручной маршрут для приказа: конкретный сотрудник, одновременно
    od = _ok(client.post("/api/office/docs", headers=H(CLERK), json={"entity_id": ru["id"], "kind": "order", "category": "od", "subject": "О графике"}), 201)
    cd = next(u["id"] for u in m["users"] if not u["name"].startswith("Добров"))
    a = _ok(client.post(f"/api/office/docs/{od['id']}/approval", headers=H(CLERK), json={"mode": "par", "steps": [{"user_id": cd}, {"role": "lawyer"}]}))
    assert all(st["status"] == "pending" for st in a["approval"]["steps"])
    _ok(client.post(f"/api/office/docs/{od['id']}/approval/recall", headers=H(CLERK)))
