"""Поддержка внутри CRM (docs/operations/12 SUP-01…SUP-08, 05 §11). Сотрудники — синтетические."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.auth import hash_password  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AccessUserOverride, AuthSession, NotificationLog, SecurityEvent, SupportMessage, SupportTicket, User  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, TEST_PASSWORD, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADM = TEST_LOGIN, "admin@example.com"
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-" + "TEST-FILE!$H+H*").encode()


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture()
def c():
    with TestClient(app) as client:
        yield client


def make_user(roles=("manager",), support: int = 0, name=None) -> tuple[str, str]:
    tag = uuid.uuid4().hex[:8]
    email = f"sup.{tag}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=name or f"Поддержкин Тест {tag}", roles=list(roles),
                 password_hash=hash_password("Tundra-river-2026"), password_set_at=datetime.now(timezone.utc))
        db.add(u)
        db.flush()
        if support:
            db.add(AccessUserOverride(user_id=u.id, field_group="support_admin", level=support, comment="тест", created_by=u.id))
        db.commit()
        return u.id, email


def new_ticket(c, email, subject="Не открывается карточка сделки", files=None, key=None, urgency="normal", category="error", **extra):
    data = {"category": category, "subject": subject, "description": "Нажимаю на сделку — белый экран, ожидал карточку. 10:15.",
            "urgency": urgency, "route": "/deals/123?token=secret123&tab=docs", "app_commit": "abc1234", "correlation_id": "rid-42",
            "client_metadata": '{"browser": "Chrome 140", "os": "Windows", "viewport": "1440x900", "cookie": "x"}', **extra}
    headers = {**H(email), **({"Idempotency-Key": key} if key else {})}
    return c.post("/api/support/tickets", headers=headers, data=data, files=[("files", f) for f in (files or [])])


def notes(uid, since=None):
    with SessionLocal() as db:
        q = select(NotificationLog).where(NotificationLog.user_id == uid, NotificationLog.event.like("support_%"))
        if since:
            q = q.where(NotificationLog.at >= since)
        return list(db.scalars(q).all())


# ------------------------------------------------------------------ SUP-01

def test_sup01_create_with_files_number_metadata_app_only(c, monkeypatch):
    from app import adapters
    mailed = []
    monkeypatch.setattr(adapters, "mail", lambda *a, **k: mailed.append(1))
    monkeypatch.setattr(adapters, "messenger", lambda *a, **k: mailed.append(1))
    uid, me = make_user()
    sid, spec = make_user(support=2)
    t0 = datetime.now(timezone.utc)
    r = new_ticket(c, me, files=[("скрин.png", b"\x89PNG screen"), ("лог.txt", b"error line")], key=uuid.uuid4().hex)
    assert r.status_code == 201, r.text
    t = r.json()
    year = datetime.now(timezone.utc).year
    assert t["number"].startswith(f"SUP-{year}-") and len(t["number"]) == 13 and t["status"] == "new" and t["priority"] == "normal"
    assert t["current_route"] == "/deals/123?tab=docs"                         # токен из адреса не сохраняется
    assert t["client_metadata"] == {"browser": "Chrome 140", "os": "Windows", "viewport": "1440x900"}   # лишнее отброшено
    assert t["correlation_id"] == "rid-42" and t["app_commit"] == "abc1234"
    [m] = t["messages"]
    assert m["kind"] == "user" and len(m["attachments"]) == 2 and all(a["url"] for a in m["attachments"])
    a = m["attachments"][0]
    r = c.get(a["url"], headers=H(me))
    assert r.status_code == 200 and r.content in (b"\x89PNG screen", b"error line")
    # уведомление очереди — только «в системе», со ссылкой на очередь; ни почты, ни мессенджера
    n = notes(sid, t0)
    assert len(n) == 1 and n[0].channel == "app" and n[0].link == f"/support/admin?ticket={t['id']}" and "белый экран" not in n[0].text
    assert mailed == []
    # проверки полей
    for bad in ({"subject": "abc"}, {"description": "коротко"}, {"category": "x"}, {"urgency": "now"}):
        assert new_ticket(c, me, **({"subject": bad["subject"]} if "subject" in bad else {}),
                          **({k: v for k, v in bad.items() if k != "subject"})).status_code == 400
    assert new_ticket(c, me, files=[(f"f{i}.txt", b"x") for i in range(6)]).status_code == 422
    # «мои обращения»
    lst = c.get("/api/support/tickets?tab=all", headers=H(me)).json()
    assert [x["number"] for x in lst["items"]] == [t["number"]] and lst["tabs"]["active"] == 1


# ------------------------------------------------------------------ SUP-02

def test_sup02_parallel_numbers_unique_and_idempotency(c):
    uid, me = make_user()
    key = uuid.uuid4().hex
    first = new_ticket(c, me, key=key).json()
    again = new_ticket(c, me, key=key)
    assert again.status_code == 201 and again.json()["id"] == first["id"] and again.json()["created"] is False
    with SessionLocal() as db:
        assert db.scalar(select(SupportTicket.id).where(SupportTicket.reporter_id == uid).limit(2)) == first["id"]
    numbers, errors = [], []

    def worker(i):
        try:
            with TestClient(app) as cl:
                r = new_ticket(cl, me, subject=f"Параллельное обращение {i}", key=uuid.uuid4().hex)
                (numbers if r.status_code == 201 else errors).append(r.json().get("number") if r.status_code == 201 else r.text)
        except Exception as e:   # pragma: no cover
            errors.append(repr(e))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:      # 8 одновременно (пул соединений — 15), всего 20
        list(ex.map(worker, range(20)))
    assert not errors and len(numbers) == 20 and len(set(numbers)) == 20
    seq = sorted(int(n.split("-")[-1]) for n in numbers)
    assert seq == list(range(seq[0], seq[0] + 20))                              # без пропусков и повторов


# ------------------------------------------------------------------ SUP-03

def test_sup03_ownership_404_for_others_and_plain_admin(c):
    uid, me = make_user()
    t = new_ticket(c, me, subject="Секретная тема про доступ", files=[("a.txt", b"secret")]).json()
    att = t["messages"][0]["attachments"][0]["url"]
    _, other = make_user()
    for who in (other, ADM):                                                   # обычный администратор без support_admin
        assert c.get(f"/api/support/tickets/{t['id']}", headers=H(who)).status_code == 404
        assert c.get(att, headers=H(who)).status_code == 404
        assert c.post(f"/api/support/tickets/{t['id']}/messages", headers=H(who), data={"body": "вмешаюсь"}).status_code == 404
        assert c.get("/api/support/admin/tickets", headers=H(who)).status_code == 403
        lst = c.get("/api/support/tickets?tab=all", headers=H(who)).json()
        assert "Секретная тема" not in repr(lst)
    meta = c.get("/api/support/meta", headers=H(other)).json()
    assert meta["unread"] == 0 and meta["is_admin"] is False
    # отказ — в журнале безопасности без темы
    with SessionLocal() as db:
        ev = db.scalars(select(SecurityEvent).where(SecurityEvent.event_type == "support", SecurityEvent.entity_id == t["id"],
                                                    SecurityEvent.result == "denied")).all()
        assert ev and "Секретная" not in repr([e.meta for e in ev])
    # журнал безопасности не показывает текст обращения аудитору
    audit = c.get("/api/security/events?event_type=support", headers=H(GD)).json()
    assert "Секретная" not in repr(audit) and "secret" not in repr(audit)


# ------------------------------------------------------------------ SUP-04

def test_sup04_read_vs_edit(c):
    _, me = make_user()
    t = new_ticket(c, me).json()
    rid, reader = make_user(support=1)
    q = c.get("/api/support/admin/tickets?status=open", headers=H(reader)).json()
    assert any(x["id"] == t["id"] for x in q["items"]) and q["can_edit"] is False
    assert c.get(f"/api/support/tickets/{t['id']}", headers=H(reader)).status_code == 200
    assert c.post(f"/api/support/tickets/{t['id']}/messages", headers=H(reader), data={"body": "ответ"}).status_code == 403
    assert c.post(f"/api/support/tickets/{t['id']}/assign", headers=H(reader), json={"assignee_id": rid}).status_code == 403
    assert c.post(f"/api/support/tickets/{t['id']}/priority", headers=H(reader), json={"priority": "high"}).status_code == 403
    assert c.post(f"/api/support/tickets/{t['id']}/transition", headers=H(reader), json={"action": "start"}).status_code == 403
    eid, editor = make_user(support=2)
    r = c.post(f"/api/support/tickets/{t['id']}/assign", headers=H(editor), json={"assignee_id": eid})
    assert r.status_code == 200 and r.json()["assignee_id"] == eid
    assert c.post(f"/api/support/tickets/{t['id']}/assign", headers=H(editor), json={"assignee_id": rid}).status_code == 400   # READ нельзя назначить
    r = c.post(f"/api/support/tickets/{t['id']}/priority", headers=H(editor), json={"priority": "high"})
    assert r.status_code == 200 and r.json()["priority"] == "high" and r.json()["requested_urgency"] == "normal"
    kinds = [m["kind"] for m in r.json()["messages"]]
    assert kinds.count("system") == 2
    m = c.get("/api/support/admin/metrics", headers=H(reader)).json()
    assert m["new"] >= 1 and "sla_note" in m


# ------------------------------------------------------------------ SUP-05

def test_sup05_state_machine(c):
    rid, me = make_user()
    eid, spec = make_user(support=2)
    t = new_ticket(c, me, urgency="critical").json()
    tid = t["id"]
    tr = lambda who, action, body="": c.post(f"/api/support/tickets/{tid}/transition", headers=H(who), json={"action": action, "body": body})   # noqa: E731
    # автор не может «ждать себя» и решать
    assert tr(me, "wait", "нужны данные").status_code == 403
    r = c.post(f"/api/support/tickets/{tid}/messages", headers=H(spec), data={"body": "Смотрю, какой браузер?"})
    j = r.json()
    assert r.status_code == 201 and j["status"] == "in_progress" and j["first_response_at"]
    first = j["first_response_at"]
    assert tr(spec, "wait", "").status_code == 400                                # нужно сказать, что требуется
    assert tr(spec, "wait", "Пришлите скриншот").json()["status"] == "waiting_reporter"
    again = tr(spec, "wait", "Пришлите скриншот")
    assert again.status_code == 200 and again.json()["changed"] is False          # повтор без дублей
    r = c.post(f"/api/support/tickets/{tid}/messages", headers=H(me), data={"body": "Вот скриншот"}, files=[("files", ("s.png", b"png"))])
    assert r.json()["status"] == "in_progress"
    assert tr(spec, "resolve", "Исправлено в версии 1.2").json()["status"] == "resolved"
    assert tr(me, "reopen").json()["status"] == "in_progress"
    assert tr(spec, "resolve", "Теперь точно исправлено").json()["status"] == "resolved"
    j = tr(me, "close").json()
    assert j["status"] == "closed" and j["closed_at"] and datetime.fromisoformat(j["first_response_at"]) == datetime.fromisoformat(first)
    assert c.post(f"/api/support/tickets/{tid}/messages", headers=H(me), data={"body": "ещё"}).status_code == 409
    assert tr(me, "reopen").status_code == 409
    assert tr(spec, "resolve", "что-то").status_code == 409
    before = len(notes(rid))
    assert tr(spec, "start").status_code == 409 and len(notes(rid)) == before   # недопустимый переход — без уведомлений
    # сообщения неизменяемы
    with SessionLocal() as db:
        m = db.scalar(select(SupportMessage).where(SupportMessage.ticket_id == tid).limit(1))
        m.body = "подмена"
        with pytest.raises(Exception):
            db.commit()


# ------------------------------------------------------------------ SUP-06

def test_sup06_notifications_recipients_routes_no_mail(c, monkeypatch):
    from app.services import deal_service
    called = []
    monkeypatch.setattr(deal_service, "notify", lambda *a, **k: called.append(a))
    rid, me = make_user()
    eid, spec = make_user(support=2)
    t0 = datetime.now(timezone.utc)
    t = new_ticket(c, me).json()
    tid = t["id"]
    c.post(f"/api/support/tickets/{tid}/assign", headers=H(spec), json={"assignee_id": eid})
    c.post(f"/api/support/tickets/{tid}/messages", headers=H(spec), data={"body": "Ответ специалиста"})
    c.post(f"/api/support/tickets/{tid}/messages", headers=H(me), data={"body": "Ответ автора"})
    c.post(f"/api/support/tickets/{tid}/transition", headers=H(spec), json={"action": "wait", "body": "Нужен скриншот"})
    c.post(f"/api/support/tickets/{tid}/transition", headers=H(spec), json={"action": "resolve", "body": "Готово"})
    c.post(f"/api/support/tickets/{tid}/transition", headers=H(me), json={"action": "reopen"})
    mine = sorted(n.event for n in notes(rid, t0))
    assert mine == ["support_message_specialist", "support_resolved", "support_waiting_reporter"]
    assert all(n.link == f"/support/{tid}" and n.channel == "app" for n in notes(rid, t0))
    spec_events = [n.event for n in notes(eid, t0)]
    # назначенный получает ответ автора и возврат; общая очередь после назначения не дёргается
    assert "support_ticket_assigned" not in spec_events            # сам себя назначил — себе не пишем
    assert spec_events.count("support_message_reporter") == 1 and spec_events.count("support_reopened") == 1
    assert all("Ответ" not in n.text for n in notes(eid, t0) + notes(rid, t0))   # текст сообщений не дублируется
    assert called == []                                             # общий notify (почта/WeCom) не вызывается
    # открыл обращение — уведомления по нему прочитаны, счётчик кнопки обнулился
    assert c.get("/api/support/meta", headers=H(me)).json()["unread"] == 3
    c.get(f"/api/support/tickets/{tid}", headers=H(me))
    assert c.get("/api/support/meta", headers=H(me)).json()["unread"] == 0
    bell = c.get("/api/notifications", headers=H(me)).json()["items"]
    assert any(x["url"] == f"/support/{tid}" and x["event_title"] == "Поддержка" for x in bell)


# ------------------------------------------------------------------ SUP-07

def test_sup07_offboarding_unassigns_specialist_keeps_reporter_history(c):
    rid, me = make_user()
    eid, spec = make_user(support=2)
    _, spec2 = make_user(support=2)
    t = new_ticket(c, me).json()
    c.post(f"/api/support/tickets/{t['id']}/assign", headers=H(spec2), json={"assignee_id": eid})
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == GD))
        for s in db.scalars(select(AuthSession).where(AuthSession.user_id == uid)).all():
            s.last_step_up_at = None
        db.commit()
        name = db.get(User, eid).display_name
    assert c.post(f"/api/security/users/{eid}/offboard", headers=H(GD), json={"confirm_name": name}).status_code == 403
    assert c.post("/api/auth/step-up", headers=H(GD), json={"password": TEST_PASSWORD}).status_code == 200
    r = c.post(f"/api/security/users/{eid}/offboard", headers=H(GD), json={"confirm_name": name})
    assert r.status_code == 200 and r.json()["counts"]["support_tickets_unassigned"] == 1
    card = c.get(f"/api/support/tickets/{t['id']}", headers=H(spec2)).json()
    assert card["assignee_id"] is None and card["status"] != "closed"
    assert any(m["kind"] == "system" and "отключён" in m["body"] for m in card["messages"])
    with SessionLocal() as db:
        sid2 = db.scalar(select(User.id).where(User.email == spec2))
    assert any(n.event == "support_unassigned_offboard" for n in notes(sid2))
    # отключён автор — история остаётся, видно «сотрудник отключён»
    with SessionLocal() as db:
        name = db.get(User, rid).display_name
    assert c.post(f"/api/security/users/{rid}/offboard", headers=H(GD), json={"confirm_name": name}).status_code == 200
    card = c.get(f"/api/support/tickets/{t['id']}", headers=H(spec2)).json()
    assert "сотрудник отключён" in card["reporter_name"] and len(card["messages"]) >= 2


# ------------------------------------------------------------------ SUP-08 и файлы

def test_sup08_script_text_stored_as_text_and_request_id(c):
    _, me = make_user()
    xss = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    r = new_ticket(c, me, subject=f"Тема {xss}")
    assert r.status_code == 201 and r.json()["subject"] == f"Тема {xss}"          # хранится как текст, экранирует интерфейс
    assert r.headers.get("x-request-id")
    r = c.get("/api/support/tickets/nope", headers=H(me))
    assert r.status_code == 404 and "Traceback" not in r.text and r.headers.get("x-request-id")


def test_infected_attachment_rejected_and_nothing_created(c):
    from tests.test_file_scanning import FakeClamd
    fake = FakeClamd()
    s = get_settings()
    old = (s.file_scan, s.clamd_host, s.clamd_port)
    s.file_scan, s.clamd_host, s.clamd_port = "clamav", "127.0.0.1", fake.port
    try:
        uid, me = make_user()
        r = new_ticket(c, me, files=[("ok.txt", b"fine"), ("bad.txt", EICAR)])
        assert r.status_code == 422 and r.json()["detail"]["code"] == "file_infected"
        with SessionLocal() as db:
            assert db.scalar(select(SupportTicket.id).where(SupportTicket.reporter_id == uid)) is None
        r = new_ticket(c, me, files=[("ok.txt", b"fine")])
        assert r.status_code == 201 and r.json()["messages"][0]["attachments"][0]["scan_status"] == "clean"
        s.clamd_port = 1
        assert new_ticket(c, me, subject="Ещё одно обращение", files=[("ok2.txt", b"fine2")]).status_code == 503
    finally:
        s.file_scan, s.clamd_host, s.clamd_port = old


def test_sla_overdue_flag():
    from app.services import support as sup
    t = SupportTicket(requested_urgency="critical", status="new", created_at=datetime.now(timezone.utc) - timedelta(hours=5))
    assert sup.sla(t)["overdue"] is True and sup.sla(t)["hours"] == 4
    t.first_response_at = datetime.now(timezone.utc)
    assert sup.sla(t)["overdue"] is False
    t2 = SupportTicket(requested_urgency="normal", status="new", created_at=datetime.now(timezone.utc) - timedelta(hours=47))
    assert sup.sla(t2)["overdue"] is False


def test_gd_has_support_admin_by_default_others_not(c):
    gd = c.get("/api/auth/me", headers=H(GD)).json()
    adm = c.get("/api/auth/me", headers=H(ADM)).json()
    assert gd["perms"]["support_admin"] == 2 and adm["perms"].get("support_admin", 0) == 0
    assert DEV_PASSWORD   # демо-пароль задан (используется в соседних тестах)
