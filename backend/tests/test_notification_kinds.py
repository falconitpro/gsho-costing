"""D-101: на значке — только требующие действия; задача выполнена — её уведомление прочитано; этап сменился — уведомления этапа прочитаны."""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, NotificationLog, Task, User  # noqa: E402
from app.services.deal_service import notify, notify_new_task  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"


@pytest.fixture(scope="module")
def c():
    seed()
    with TestClient(app) as cl:
        yield cl


def _uid(email):
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == email))


def _draft(c) -> str:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    return c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "уведомления"}).json()["id"]


def _clear(email):
    with SessionLocal() as db:
        db.execute(text("update notifications set read_at = now() where user_id = :u and read_at is null"), {"u": _uid(email)})
        db.commit()


def test_badge_counts_only_actions_and_tabs_split(c):
    _clear(MGR)
    did = _draft(c)
    with SessionLocal() as db:
        d = db.get(Deal, did)
        notify(db, d, _uid(MGR), "won", None, f"{d.number}: Сделка выиграна")             # сведения
        notify(db, d, _uid(MGR), "visa_required", None, f"{d.number}: Требуется виза")      # действие
        notify(db, d, _uid(MGR), "brand_new_event", None, f"{d.number}: неизвестное")       # неизвестное — считается действием
        db.commit()
    r = c.get("/api/notifications/unread-count", headers=H(MGR)).json()
    assert r["unread"] == 2 and r["info"] == 1
    act = c.get("/api/notifications?kind=action", headers=H(MGR)).json()
    info = c.get("/api/notifications?kind=info", headers=H(MGR)).json()
    assert {x["event"] for x in act["items"] if x["deal_id"] == did} == {"visa_required", "brand_new_event"} and all(x["action"] for x in act["items"])
    assert {x["event"] for x in info["items"] if x["deal_id"] == did} == {"won"} and not any(x["action"] for x in info["items"])
    assert len(c.get("/api/notifications", headers=H(MGR)).json()["items"]) >= 3          # «все» — как раньше
    ids = [x["id"] for x in info["items"] if not x["read"]]
    assert c.post("/api/notifications/read", headers=H(MGR), json={"ids": ids}).json()["unread"] == 2   # прочитать сведения — значок не меняется


def test_task_done_marks_its_notification_read(c):
    _clear(LOG)
    did = _draft(c)
    with SessionLocal() as db:
        t = Task(deal_id=did, role="logistician", assignee_id=_uid(LOG), title="Проверить ставку перевозчика [тест-101]", block="logistics", due=date.today())
        db.add(t)
        db.flush()
        notify_new_task(db, t)
        db.commit()
        tid = t.id
    assert c.get("/api/notifications/unread-count", headers=H(LOG)).json()["unread"] >= 1
    assert c.post(f"/api/tasks/{tid}/done", headers=H(LOG), json={}).status_code == 200
    with SessionLocal() as db:
        rows = db.scalars(select(NotificationLog).where(NotificationLog.deal_id == did, NotificationLog.channel == "app", NotificationLog.text.contains("[тест-101]"))).all()
        assert rows and all(r.read_at is not None for r in rows)


def test_stage_change_closes_stage_notices(c):
    _clear(MGR)
    did = _draft(c)
    with SessionLocal() as db:
        d = db.get(Deal, did)
        notify(db, d, _uid(MGR), "draft_stale", None, f"{d.number}: черновик 4 дн. без движения")
        db.commit()
    assert c.get("/api/notifications/unread-count", headers=H(MGR)).json()["unread"] == 1
    # черновик удалён — напоминание о нём больше не ждёт действия
    assert c.post(f"/api/deals/{did}/delete", headers=H(CD), json={"reason": "тест"}).status_code == 200
    assert c.get("/api/notifications/unread-count", headers=H(MGR)).json()["unread"] == 0


def test_decisions_are_info():
    from app.services.notification_kinds import is_action
    assert not is_action("factory_decision") and not is_action("user_change_decision") and is_action("factory_approval") and is_action("user_change")


def test_overdue_copy_for_leaders_is_info_and_backfill_closes_done(c):
    from datetime import timedelta
    from app.services.deal_service import check_overdue_tasks
    from app.services.notification_kinds import close_done_task_notices
    _clear(CD)
    did = _draft(c)
    with SessionLocal() as db:
        t = Task(deal_id=did, role="logistician", assignee_id=_uid(LOG), title="Просроченная [тест-101b]", block="logistics", due=date.today() - timedelta(days=5))
        db.add(t)
        db.commit()
        check_overdue_tasks(db)
        db.commit()
        cd_rows = db.scalars(select(NotificationLog).where(NotificationLog.user_id == _uid(CD), NotificationLog.text.contains("[тест-101b]"), NotificationLog.channel == "app")).all()
        assert cd_rows and {r.event for r in cd_rows} == {"task_overdue_team"}
        # задачу закрыли «в обход» (прямой записью) — ежечасная уборка всё равно гасит уведомления
        db.execute(text("update tasks set done_at = now() where id = :t"), {"t": t.id})
        db.commit()
        assert close_done_task_notices(db) >= 2
        db.commit()
        left = db.scalars(select(NotificationLog).where(NotificationLog.text.contains("[тест-101b]"), NotificationLog.channel == "app", NotificationLog.read_at.is_(None))).all()
        assert not left
