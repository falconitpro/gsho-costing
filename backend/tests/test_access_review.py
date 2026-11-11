"""Пересмотр прав (docs/operations/04 §6, 13 §10): снимок без секретов, решения, второй проверяющий, завершение, напоминания."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AccessReviewCampaign, AccessReviewItem, AccessUserOverride, IntegrationLog, NotificationLog, User  # noqa: E402
from app.services import access_review as ar  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, run as seed  # noqa: E402



def make_user(roles=("manager",)):
    """Синтетический сотрудник для пересмотра (свой помощник: тесты запускаются и из backend/, и из tests/)."""
    import uuid
    from datetime import datetime, timezone
    from app.core.auth import hash_password
    tag = uuid.uuid4().hex[:8]
    email = f"ar.{tag}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=f"Проверяемый {tag}", roles=list(roles),
                 password_hash=hash_password("Tundra-river-2026"), password_set_at=datetime.now(timezone.utc))
        db.add(u)
        db.commit()
        return u.id, email, u.display_name

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADM, MGR = TEST_LOGIN, "admin@example.com", "manager@example.com"
c = TestClient(app, base_url="http://testserver")


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def _create(roles=None, due_days=30, who=GD):
    body = {"title": "Пересмотр тест", "period_start": str(date.today() - timedelta(days=90)), "period_end": str(date.today()),
            "due_date": str(date.today() + timedelta(days=due_days)), "roles": roles or []}
    return c.post("/api/security/access-reviews", headers=H(who), json=body)


def _item(cid, user_id, who=GD):
    return next(i for i in c.get(f"/api/security/access-reviews/{cid}", headers=H(who)).json()["items"] if i["user_id"] == user_id)


def _decide(cid, iid, decision, comment="", who=GD):
    return c.post(f"/api/security/access-reviews/{cid}/items/{iid}/decision", headers=H(who), json={"decision": decision, "comment": comment})


def test_permissions_and_snapshot_has_no_secrets():
    assert _create(who=MGR).status_code == 403
    assert c.get("/api/security/access-reviews", headers=H(MGR)).status_code == 403
    r = _create()
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    with SessionLocal() as db:
        active = len(db.scalars(select(User.id).where(User.is_active.is_(True))).all())
        snaps = [i.snapshot for i in db.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == cid)).all()]
    assert r.json()["progress"]["total"] == active == len(snaps)
    blob = json.dumps(snaps, ensure_ascii=False)
    for bad in ("password_hash", "secret_ciphertext", "code_hash", "sid", "token", DEV_PASSWORD):
        assert bad not in blob
    assert all({"roles", "perms", "overrides", "mfa_enabled", "last_seen_at", "sensitive"} <= set(s) for s in snaps)
    assert _create(due_days=-1).status_code == 422


def test_keep_change_requires_real_change_and_comment():
    uid, email, _ = make_user()
    cid = _create(roles=["manager"]).json()["id"]
    it = _item(cid, uid)
    assert _decide(cid, it["id"], "change", "что-то").status_code == 409            # прав не меняли — «изменено» нельзя
    with SessionLocal() as db:
        db.add(AccessUserOverride(user_id=uid, field_group="legal_docs", level=1, comment="на квартал"))
        db.commit()
    from app.services import access_service
    access_service.invalidate()
    assert _item(cid, uid)["current_diff"].get("perms") or _item(cid, uid)["current_diff"].get("overrides")
    assert _decide(cid, it["id"], "change", "").status_code == 422                  # без описания — нельзя
    r = _decide(cid, it["id"], "change", "Выдан просмотр юр. документов на квартал")
    assert r.status_code == 200 and r.json()["changes"]
    uid2, _, _ = make_user()
    cid2 = _create(roles=["manager"]).json()["id"]
    assert _decide(cid2, _item(cid2, uid2)["id"], "keep").status_code == 200
    assert _item(cid2, uid2)["decision"] == "keep" and _item(cid2, uid2)["reviewer"]


def test_own_elevated_rights_need_second_reviewer():
    cid = _create(roles=["admin"]).json()["id"]
    with SessionLocal() as db:
        adm_id = db.scalar(select(User.id).where(User.email == ADM))
    mine = _item(cid, adm_id, who=ADM)
    assert mine["is_self"] and mine["second_reviewer_needed"]
    r = _decide(cid, mine["id"], "keep", who=ADM)
    assert r.status_code == 409 and "другой проверяющий" in r.json()["detail"]
    assert _decide(cid, mine["id"], "keep", who=GD).status_code == 200


def test_disable_goes_through_offboarding_and_skip_needs_reason():
    uid, email, name = make_user()
    cid = _create(roles=["manager"]).json()["id"]
    it = _item(cid, uid)
    r = _decide(cid, it["id"], "disable")
    assert r.status_code == 409 and "Сначала отключите" in r.json()["detail"]
    c.post("/api/auth/step-up", headers=H(ADM), json={"password": DEV_PASSWORD})
    assert c.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name}).status_code == 200
    assert _decide(cid, it["id"], "disable").status_code == 200
    uid2, _, _ = make_user()
    cid2 = _create(roles=["manager"]).json()["id"]
    it2 = _item(cid2, uid2)
    assert _decide(cid2, it2["id"], "skipped", "коротко").status_code == 422
    assert _decide(cid2, it2["id"], "skipped", "Владелец: сотрудник в отпуске, проверим в мае").status_code == 200
    assert c.post(f"/api/security/access-reviews/{cid2}/items/{it2['id']}/reopen", headers=H(GD)).status_code == 200
    assert _item(cid2, uid2)["decision"] == "pending"


def test_complete_only_when_all_decided_and_then_frozen():
    cid = _create(roles=["observer"]).json()["id"]
    d = c.get(f"/api/security/access-reviews/{cid}", headers=H(GD)).json()
    assert d["progress"]["by_decision"]["pending"] == d["progress"]["total"] > 0
    r = c.post(f"/api/security/access-reviews/{cid}/complete", headers=H(GD), json={"note": ""})
    assert r.status_code == 409 and "Не решено" in r.json()["detail"]
    for i in d["items"]:
        who = ADM if i["second_reviewer_needed"] else GD
        assert _decide(cid, i["id"], "keep", who=who).status_code == 200
    r = c.post(f"/api/security/access-reviews/{cid}/complete", headers=H(GD), json={"note": "Квартал проверен"})
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["progress"]["percent"] == 100
    assert _decide(cid, d["items"][0]["id"], "keep").status_code == 409
    assert c.post(f"/api/security/access-reviews/{cid}/cancel", headers=H(GD), json={"note": "поздно"}).status_code == 409


def test_cancel_requires_reason():
    cid = _create(roles=["observer"]).json()["id"]
    assert c.post(f"/api/security/access-reviews/{cid}/cancel", headers=H(GD), json={"note": ""}).status_code == 422
    assert c.post(f"/api/security/access-reviews/{cid}/cancel", headers=H(GD), json={"note": "Создана по ошибке"}).json()["status"] == "cancelled"


def test_reminders_in_app_once_and_overdue():
    with SessionLocal() as db:
        db.query(AccessReviewCampaign).filter(AccessReviewCampaign.status == "open").update({"status": "cancelled"})
        db.execute(delete(IntegrationLog).where(IntegrationLog.system == "access_review"))
        db.execute(delete(NotificationLog).where(NotificationLog.event == "access_review"))
        db.commit()
    cid = _create(roles=["observer"], due_days=7).json()["id"]
    with SessionLocal() as db:
        assert ar.remind(db) == [f"{cid}:7"]
        assert ar.remind(db) == []                                   # без повторов
        gd_id = db.scalar(select(User.id).where(User.email == GD))
        notes = db.scalars(select(NotificationLog).where(NotificationLog.event == "access_review", NotificationLog.user_id == gd_id)).all()
        assert len(notes) == 1 and notes[0].channel == "app" and "до срока 7 дн." in notes[0].text
        assert ar.remind(db, today=date.today() + timedelta(days=8)) == [f"{cid}:overdue"]
    bell = c.get("/api/notifications", headers=H(GD)).json()["items"]
    assert any(x["event"] == "access_review" and x["url"] == "/settings?s=access_review" for x in bell)
