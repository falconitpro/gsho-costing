"""Черновики без движения: напоминание через 3 дня с перечнем недостающего, предупреждение на 30-й день,
перенос в архив через сутки после предупреждения, сброс отсчёта при любом движении, возврат из архива."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

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
from app.models import AuditLog, Deal, NotificationLog, User  # noqa: E402
from app.services import draft_watch  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD = "manager@example.com", "cd@example.com"


@pytest.fixture(scope="module")
def c():
    seed()
    with TestClient(app) as cl:
        yield cl


def _draft(c) -> str:
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    return c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "черновик-простой"}).json()["id"]


def _age(did: str, days: float) -> None:
    """Состарить черновик и всё, что считается движением."""
    t = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        for sql in ("update deals set updated_at = :t, created_at = :t where id = :d", "update positions set updated_at = :t where deal_id = :d",
                    "update variants set updated_at = :t where deal_id = :d", "update attachments set uploaded_at = :t where deal_id = :d",
                    "update deal_feed set at = :t where deal_id = :d"):
            db.execute(text(sql), {"t": t, "d": did})
        db.commit()


def _run(now=None) -> dict:
    with SessionLocal() as db:
        r = draft_watch.check(db, now)
        db.commit()
        return r


def _events(did: str, event: str) -> list[NotificationLog]:
    with SessionLocal() as db:
        return db.scalars(select(NotificationLog).where(NotificationLog.deal_id == did, NotificationLog.event == event)).all()


def test_fresh_draft_untouched(c):
    did = _draft(c)
    _run()
    assert not _events(did, draft_watch.EV_REMIND)


def test_reminder_after_3_days_once_with_missing_list(c):
    did = _draft(c)
    _age(did, 3.2)
    _run()
    ev = [e for e in _events(did, draft_watch.EV_REMIND) if e.channel == "app"]
    assert len(ev) == 1 and "без движения" in ev[0].text and "не хватает" in ev[0].text and "Позиции" in ev[0].text
    with SessionLocal() as db:
        mgr = db.scalar(select(User).where(User.email == MGR))
        assert ev[0].user_id == mgr.id
    _run()                                           # повторный прогон — без повторов
    assert len([e for e in _events(did, draft_watch.EV_REMIND) if e.channel == "app"]) == 1


def test_any_movement_resets_and_reminds_again_after_new_idle(c):
    did = _draft(c)
    _age(did, 4)
    _run()
    assert len([e for e in _events(did, draft_watch.EV_REMIND) if e.channel == "app"]) == 1
    # движение: менеджер правит сделку
    assert c.patch(f"/api/deals/{did}", headers=H(MGR), json={"site": "черновик-ожил"}).status_code == 200
    _run()
    assert len([e for e in _events(did, draft_watch.EV_REMIND) if e.channel == "app"]) == 1   # сейчас не напоминаем
    # новый простой — новое напоминание (журнал уведомлений тоже «стареет» вместе со сделкой)
    _age(did, 5)
    with SessionLocal() as db:
        db.execute(text("update notifications set at = :t where deal_id = :d"), {"t": datetime.now(timezone.utc) - timedelta(days=6), "d": did})
        db.commit()
    _run()
    assert len([e for e in _events(did, draft_watch.EV_REMIND) if e.channel == "app"]) == 2


def test_warn_at_30_then_archive_next_day_and_restore(c):
    did = _draft(c)
    _age(did, 30.2)
    r = _run()
    assert r["warned"] >= 1
    warn = [e for e in _events(did, draft_watch.EV_WARN) if e.channel == "app"]
    with SessionLocal() as db:
        cd = db.scalar(select(User).where(User.email == CD))
    assert warn and "завтра будет перенесён в архив" in warn[0].text and cd.id in {e.user_id for e in warn}
    _run()                                           # в тот же день в архив не переносим
    with SessionLocal() as db:
        assert db.get(Deal, did).archived_at is None
    _run(datetime.now(timezone.utc) + timedelta(hours=21))   # через сутки
    with SessionLocal() as db:
        d = db.get(Deal, did)
        assert d.archived_at is not None and d.deleted_at is None and "автоматически" in d.archive_reason
        assert db.scalar(select(AuditLog).where(AuditLog.entity_id == did, AuditLog.action == "archive")) is not None
    assert [e for e in _events(did, draft_watch.EV_ARCHIVED) if e.channel == "app"]
    # в реестре — в блоке «Архив», вернуть может КД
    assert did in {x["id"] for x in c.get("/api/deals?archived=true", headers=H(CD)).json()}
    assert c.post(f"/api/deals/{did}/unarchive", headers=H(MGR)).status_code == 403
    assert c.post(f"/api/deals/{did}/unarchive", headers=H(CD)).status_code == 200
    _run(datetime.now(timezone.utc) + timedelta(hours=22))   # после возврата отсчёт заново: сразу в архив не уходит
    with SessionLocal() as db:
        assert db.get(Deal, did).archived_at is None


def test_movement_after_warning_cancels_archive(c):
    did = _draft(c)
    _age(did, 31)
    _run()
    assert [e for e in _events(did, draft_watch.EV_WARN) if e.channel == "app"]
    assert c.patch(f"/api/deals/{did}", headers=H(MGR), json={"site": "черновик-нужен"}).status_code == 200
    _run(datetime.now(timezone.utc) + timedelta(hours=25))
    with SessionLocal() as db:
        assert db.get(Deal, did).archived_at is None


def test_not_drafts_are_not_touched(c):
    did = _draft(c)
    with SessionLocal() as db:
        db.execute(text("update deals set status = 'LOGISTICS_CALC' where id = :d"), {"d": did})
        db.commit()
    _age(did, 40)
    _run()
    _run(datetime.now(timezone.utc) + timedelta(hours=25))
    with SessionLocal() as db:
        assert db.get(Deal, did).archived_at is None
    assert not _events(did, draft_watch.EV_WARN) and not _events(did, draft_watch.EV_REMIND)
