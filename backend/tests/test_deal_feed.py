"""D-127: обсуждения сделки — сообщения по разделам, права, упоминания, вопросы и ответы, закреплённое, правка, письма .eml, поиск."""
from datetime import date, datetime, timedelta, timezone

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401

LAWYER = "lawyer@example.com"


def _deal(client):
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    return _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Лента"}), 201)["id"]


def test_feed_messages_sections_rights_questions(client):
    did = _deal(client)
    me_cd = _ok(client.get("/api/auth/me", headers=H(CD)))
    # менеджер пишет вопрос клиенту в разделе «Продажа» и ждёт ответа
    f = _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={
        "section": "sale", "body": "Уточнить у клиента высоту выработки", "tags": ["q_client"], "awaiting": True,
        "due_on": (date.today() + timedelta(days=2)).isoformat(), "mentions": [me_cd["id"]]}), 201)
    q = next(i for i in f["items"] if i["kind"] == "comment")
    assert q["section"] == "sale" and q["awaiting"] and q["tags"] == ["q_client"] and f["open"].get("sale") == 1
    # упоминание → уведомление КД
    notes = _ok(client.get("/api/notifications", headers=H(CD)))
    rows = notes["items"] if isinstance(notes, dict) else notes
    assert any("упомянул" in (n.get("text") or "") for n in rows)
    # вопрос — в «Что сделать» у автора
    wp = _ok(client.get("/api/workplace", headers=H(MGR)))
    assert any(t["kind"] == "question" for t in wp["todo"])
    # ответ закрывает вопрос
    f = _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={
        "section": "sale", "body": "Клиент: 4,2 м", "tags": ["a_client"], "parent_id": q["id"]}), 201)
    q2 = next(i for i in f["items"] if i["id"] == q["id"])
    assert q2["resolved_at"] and not f["open"]
    # раздел «Расчёт и утверждение» менеджеру закрыт, КД — открыт
    assert client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "pricing", "body": "x"}).status_code == 403
    _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(CD), json={"section": "pricing", "body": "Маржа ниже порога — обсудить"}), 201)
    mgr_view = _ok(client.get(f"/api/deals/{did}/feed", headers=H(MGR)))
    assert all(i["section"] != "pricing" for i in mgr_view["items"]) and "pricing" not in mgr_view["sections"]
    assert any(i["section"] == "pricing" for i in _ok(client.get(f"/api/deals/{did}/feed", headers=H(CD)))["items"])
    # закрепить ответ
    ans = next(i for i in mgr_view["items"] if i["body"] == "Клиент: 4,2 м")
    f = _ok(client.post(f"/api/deals/{did}/feed/messages/{ans['id']}/pin", headers=H(MGR)))
    assert [p["id"] for p in f["pinned"]] == [ans["id"]]
    # поиск
    assert [i["body"] for i in _ok(client.get(f"/api/deals/{did}/feed", headers=H(MGR), params={"q": "выработки"}))["items"]] == ["Уточнить у клиента высоту выработки"]
    # фильтр по разделу и метке
    assert len(_ok(client.get(f"/api/deals/{did}/feed", headers=H(MGR), params={"section": "sale", "tag": "a_client"}))["items"]) == 1


def test_feed_edit_delete_window_and_observer(client):
    did = _deal(client)
    f = _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "", "body": "Черновой текст"}), 201)
    m = next(i for i in f["items"] if i["kind"] == "comment")
    one = lambda f: next(i for i in f["items"] if i["id"] == m["id"])   # noqa: E731
    assert m["can_delete"] and m["can_edit"]
    # чужое сообщение не править
    assert client.patch(f"/api/deals/{did}/feed/messages/{m['id']}", headers=H(CD), json={"body": "x"}).status_code == 403
    # правка в первые 15 минут — без пометки
    f = _ok(client.patch(f"/api/deals/{did}/feed/messages/{m['id']}", headers=H(MGR), json={"body": "Текст 2"}))
    assert one(f)["body"] == "Текст 2" and not one(f)["edited"]
    # через 20 минут: правка — «исправлено», удалить нельзя
    from app.db import SessionLocal
    from app.models import FeedEntry
    with SessionLocal() as db:
        e = db.get(FeedEntry, m["id"])
        e.at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()
    f = _ok(client.patch(f"/api/deals/{did}/feed/messages/{m['id']}", headers=H(MGR), json={"body": "Текст 3"}))
    assert one(f)["edited"]
    with SessionLocal() as db:
        assert db.get(FeedEntry, m["id"]).edit_history[-1]["body"] == "Текст 2"
    assert client.delete(f"/api/deals/{did}/feed/messages/{m['id']}", headers=H(MGR)).status_code == 409
    # пустое сообщение и вопрос без срока
    assert client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "", "body": "  "}).status_code == 422
    assert client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "", "body": "?", "awaiting": True}).status_code == 422


def test_feed_files_and_eml(client):
    did = _deal(client)
    up = _ok(client.post(f"/api/deals/{did}/feed/files", headers=H(MGR), files={"file": ("скрин.png", b"\x89PNG\r\n\x1a\n0000", "image/png")}), 201)
    f = _ok(client.post(f"/api/deals/{did}/feed/messages", headers=H(MGR), json={"section": "sale", "body": "Скриншот от клиента",
                                                                                 "attachment_ids": [up["id"]]}), 201)
    assert next(i for i in f["items"] if i["kind"] == "comment")["files"][0]["name"] == "скрин.png"
    # чужой файл к своему сообщению не прикрепить
    assert client.post(f"/api/deals/{did}/feed/messages", headers=H(CD), json={"section": "sale", "body": "x", "attachment_ids": [up["id"]]}).status_code == 422
    eml = (b"From: Ivan <ivan@client.ru>\r\nTo: mgr@company.ru\r\nSubject: =?utf-8?b?" + __import__("base64").b64encode("Ответ по высоте".encode()) +
           b"?=\r\nDate: Fri, 20 Nov 2026 10:00:00 +0300\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n" +
           "Высота 4,2 м.".encode())
    f = _ok(client.post(f"/api/deals/{did}/feed/mail", headers=H(MGR), files={"file": ("reply.eml", eml, "message/rfc822")},
                        data={"section": "sale", "direction": "in", "tags": "a_client"}), 201)
    mail = next(i for i in f["items"] if i["kind"] == "mail_in")
    assert mail["subject"] == "Ответ по высоте" and "4,2" in mail["body"] and "ivan@client.ru" in mail["counterparty"] and mail["tags"] == ["a_client"]
    assert client.post(f"/api/deals/{did}/feed/mail", headers=H(MGR), files={"file": ("x.txt", b"x", "text/plain")}).status_code == 415


def test_observer_reads_but_does_not_write():
    from app.core.auth import CurrentUser
    from app.core.roles import Role
    from app.services.deal_feed import can_write, visible_sections
    obs = CurrentUser(id="o", email="o@x", display_name="Набл.", roles={Role.OBSERVER}, observer_scope={}, perms={"cost": 1})
    assert "pricing" in visible_sections(obs) and not can_write(obs, "sale") and not can_write(obs, "pricing")
    mgr = CurrentUser(id="m", email="m@x", display_name="Менеджер", roles={Role.MANAGER}, observer_scope={}, perms={})
    assert can_write(mgr, "sale") and not can_write(mgr, "pricing") and "finance" not in visible_sections(mgr)
