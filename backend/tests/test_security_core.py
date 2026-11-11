"""Безопасность входа и отключение сотрудника (docs/operations/12 SEC-01…SEC-10, OFF-01…OFF-05).

Все сотрудники — синтетические, создаются в тесте; демо-учётные записи seed не меняются (кроме ЗГД/админа как действующих лиц)."""
from __future__ import annotations

import os
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.auth import SESSION_COOKIE, hash_password  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AccessUserOverride, AuditLog, AuthSession, MfaCredential, MfaRecoveryCode, NotificationLog, PasswordHistory,  # noqa: E402
                        SecurityEvent, Task, User)
from app.services import mfa as mfa_svc  # noqa: E402
from app.services import offboarding  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, TEST_PASSWORD, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADM, MGR = TEST_LOGIN, "admin@example.com", "manager@example.com"
PW = "Tundra-river-2026"            # пароль синтетических сотрудников
CANARY = "Canary-Secret-Pass-9137"


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


def make_user(roles=("manager",), password=PW, name=None) -> tuple[str, str, str]:
    tag = uuid.uuid4().hex[:8]
    email = f"sec.{tag}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=name or f"Тестов Тест {tag}", roles=list(roles),
                 password_hash=hash_password(password) if password else None, password_set_at=datetime.now(timezone.utc) if password else None)
        db.add(u)
        db.commit()
        return u.id, email, u.display_name


def login(email, password=PW, remember=False) -> TestClient:
    c = TestClient(app, base_url="http://testserver")
    r = c.post("/api/auth/password-login", json={"login": email, "password": password, "remember": remember})
    assert r.status_code == 200, r.text
    return c


def step_up(c, email):
    pw = TEST_PASSWORD if email == TEST_LOGIN else DEV_PASSWORD
    r = c.post("/api/auth/step-up", headers=H(email), json={"password": pw})
    assert r.status_code == 200, r.text


def expire_step_up(email):
    """Подтверждение живёт 10 минут в сессии; dev-сессия «Войти как» общая для тестов — сбрасываем явно."""
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        for s in db.scalars(select(AuthSession).where(AuthSession.user_id == uid)).all():
            s.last_step_up_at = None
        db.commit()


def admin_client() -> TestClient:
    c = TestClient(app, base_url="http://testserver")
    step_up(c, ADM)
    return c


def enable_mfa(c, password=PW) -> tuple[str, list[str]]:
    r = c.post("/api/auth/mfa/setup", json={"password": password})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = c.post("/api/auth/mfa/confirm", json={"code": mfa_svc.code_now(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def next_code(secret, steps=1):
    return mfa_svc.code_now(secret, time.time() + 30 * steps)   # следующий шаг: тот же код дважды не принимается


# ---------------------------------------------------------------- сессии

def test_logout_revokes_cookie_other_browser_keeps_working():   # SEC-01
    _, email, _ = make_user()
    c1, c2 = login(email), login(email)
    old = c1.cookies.get(SESSION_COOKIE)
    assert c1.post("/api/auth/logout").status_code == 200
    replay = TestClient(app, base_url="http://testserver")
    replay.cookies.set(SESSION_COOKIE, old)
    r = replay.get("/api/auth/me")
    assert r.status_code == 401 and "завершена" in r.json()["detail"]
    assert c2.get("/api/auth/me").status_code == 200


def test_revoke_others_keeps_current():   # SEC-02
    uid, email, _ = make_user()
    c1, c2, c3 = login(email), login(email), login(email)
    items = c1.get("/api/auth/sessions").json()["items"]
    assert len(items) == 3 and sum(x["current"] for x in items) == 1
    assert all(set(x) >= {"sid", "device", "ip_prefix", "last_seen_at"} for x in items)
    r = c1.post("/api/auth/sessions/revoke-others").json()
    assert r["revoked"] == 2 and len(r["items"]) == 1
    assert c2.get("/api/auth/me").status_code == 401 and c3.get("/api/auth/me").status_code == 401 and c1.get("/api/auth/me").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid, SecurityEvent.action == "revoke_others")) is not None


def test_revoke_single_session_and_current_is_protected():
    _, email, _ = make_user()
    c1, c2 = login(email), login(email)
    mine = {x["current"]: x["sid"] for x in c1.get("/api/auth/sessions").json()["items"]}
    assert c1.delete(f"/api/auth/sessions/{mine[True]}").status_code == 409
    assert c1.delete(f"/api/auth/sessions/{mine[False]}").status_code == 200
    assert c1.delete(f"/api/auth/sessions/{mine[False]}").status_code == 404   # повторно — не «успешно завершено»
    assert c2.get("/api/auth/me").status_code == 401
    other_uid, other_email, _ = make_user()
    o = login(other_email)
    osid = o.get("/api/auth/sessions").json()["items"][0]["sid"]
    assert c1.delete(f"/api/auth/sessions/{osid}").status_code == 404           # чужая сессия — как несуществующая


def test_admin_revoke_needs_permission_and_step_up_no_tokens_exposed():   # SEC-03
    uid, email, _ = make_user()
    u = login(email)
    token = u.cookies.get(SESSION_COOKIE)
    sid = u.get("/api/auth/sessions").json()["items"][0]["sid"]
    mgr = TestClient(app, base_url="http://testserver")
    assert mgr.get(f"/api/security/users/{uid}/sessions", headers=H(MGR)).status_code == 403
    assert mgr.delete(f"/api/security/users/{uid}/sessions/{sid}", headers=H(MGR)).status_code == 403
    a = TestClient(app, base_url="http://testserver")
    lst = a.get(f"/api/security/users/{uid}/sessions", headers=H(ADM))
    assert lst.status_code == 200 and token not in lst.text and "token" not in lst.text
    expire_step_up(ADM)
    r = a.delete(f"/api/security/users/{uid}/sessions/{sid}", headers=H(ADM))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "step_up_required"
    step_up(a, ADM)
    assert a.delete(f"/api/security/users/{uid}/sessions/{sid}", headers=H(ADM)).status_code == 200
    assert u.get("/api/auth/me").status_code == 401


def test_idle_absolute_and_touch_throttle():   # SEC-04
    _, email, _ = make_user()
    c = login(email)
    sid = c.get("/api/auth/sessions").json()["items"][0]["sid"]
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        assert (s.absolute_expires_at - s.created_at) == timedelta(hours=12) and (s.idle_expires_at - s.created_at) == timedelta(hours=8)
        first_seen = s.last_seen_at
    c.get("/api/auth/me")
    with SessionLocal() as db:
        assert db.scalar(select(AuthSession).where(AuthSession.sid == sid)).last_seen_at == first_seen   # чаще раза в 60 с — не пишем
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        s.last_seen_at = s.last_seen_at - timedelta(minutes=2)
        db.commit()
    c.get("/api/auth/me")
    with SessionLocal() as db:
        assert db.scalar(select(AuthSession).where(AuthSession.sid == sid)).last_seen_at > first_seen
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        s.idle_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    r = c.get("/api/auth/me")
    assert r.status_code == 401 and "бездействия" in r.json()["detail"]
    c2 = login(email)
    sid2 = next(x["sid"] for x in c2.get("/api/auth/sessions").json()["items"] if x["current"])
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid2))
        s.absolute_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert c2.get("/api/auth/me").status_code == 401
    rc = login(email, remember=True)
    sid3 = next(x["sid"] for x in rc.get("/api/auth/sessions").json()["items"] if x["current"])
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid3))
        assert not s.remember and (s.absolute_expires_at - s.created_at) == timedelta(hours=12)   # галочка сессию не продлевает (D-041)


def test_jwt_without_sid_forged_and_foreign_sid_rejected(monkeypatch):
    uid, email, _ = make_user()
    other_uid, other_email, _ = make_user()
    o = login(other_email)
    osid = o.get("/api/auth/sessions").json()["items"][0]["sid"]
    s = get_settings()
    with SessionLocal() as db:
        u = db.get(User, uid)
        pv = int(u.password_set_at.timestamp() * 1000)
    now = int(time.time())
    def call(claims, key=s.secret_key):
        c = TestClient(app, base_url="http://testserver")
        c.cookies.set(SESSION_COOKIE, jwt.encode(claims, key, algorithm="HS256"))
        return c.get("/api/auth/me")
    legacy = {"sub": uid, "iat": now, "exp": now + 3600, "pv": pv}
    r = call(legacy)
    assert r.status_code == 401 and "устарела" in r.json()["detail"]                 # окно перехода не задано — отказ
    assert call({**legacy, "sid": osid}).status_code == 401                            # чужая сессия
    assert call({**legacy, "sid": str(uuid.uuid4())}).status_code == 401              # несуществующая
    assert call({**legacy, "sid": osid, "sub": other_uid}, key="wrong-key").status_code == 401   # подделка подписи
    monkeypatch.setattr(s, "legacy_sessions_until", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    r = call(legacy)
    assert r.status_code == 200 and "sid" in jwt.decode(r.cookies.get(SESSION_COOKIE), s.secret_key, algorithms=["HS256"])   # вход переведён на сессию
    monkeypatch.setattr(s, "legacy_sessions_until", (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert call(legacy).status_code == 401


def test_offboarded_user_old_cookie_401_immediately():
    uid, email, name = make_user()
    u = login(email)
    a = admin_client()
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name}).status_code == 200
    assert u.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------- TOTP

def test_mfa_setup_login_challenge_and_attempts():   # SEC-05
    uid, email, _ = make_user()
    c = login(email)
    assert c.post("/api/auth/mfa/setup", json={"password": "wrong"}).status_code == 403
    secret, codes = enable_mfa(c)
    assert len(codes) == 10 and c.get("/api/auth/me").json()["security"]["mfa_enabled"] is True
    with SessionLocal() as db:
        cred = db.scalar(select(MfaCredential).where(MfaCredential.user_id == uid))
        assert secret not in cred.secret_ciphertext and cred.enabled_at and not cred.pending_secret_ciphertext
        hashes = [r.code_hash for r in db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == uid)).all()]
        assert len(hashes) == 10 and not any(cd.replace("-", "") in h for cd in codes for h in hashes)
    n = TestClient(app, base_url="http://testserver")
    r = n.post("/api/auth/password-login", json={"login": email, "password": PW})
    assert r.status_code == 200 and r.json()["mfa_required"] is True and SESSION_COOKIE not in r.cookies
    assert n.get("/api/auth/me").status_code == 401 and n.get("/api/deals").status_code == 401      # до второго шага доступа нет
    assert n.post("/api/auth/mfa/verify", json={"code": "000000"}).status_code == 401
    assert n.post("/api/auth/mfa/verify", json={"code": next_code(secret)}).status_code == 200
    assert n.get("/api/auth/me").json()["security"]["session_mfa"] == "totp"
    assert n.post("/api/auth/mfa/verify", json={"code": next_code(secret)}).status_code == 401      # вызов израсходован
    # 5 неверных попыток — вызов сгорает, даже верный код после этого не проходит
    m = TestClient(app, base_url="http://testserver")
    m.post("/api/auth/password-login", json={"login": email, "password": PW})
    for _ in range(5):
        assert m.post("/api/auth/mfa/verify", json={"code": "123456"}).status_code in (401, 429)
    assert m.post("/api/auth/mfa/verify", json={"code": next_code(secret, 2)}).status_code in (401, 429)


def test_challenge_expires_after_5_minutes():
    uid, email, _ = make_user()
    c = login(email)
    secret, _ = enable_mfa(c)
    n = TestClient(app, base_url="http://testserver")
    n.post("/api/auth/password-login", json={"login": email, "password": PW})
    with SessionLocal() as db:
        from app.models import MfaChallenge
        ch = db.scalars(select(MfaChallenge).where(MfaChallenge.user_id == uid, MfaChallenge.consumed_at.is_(None))).all()
        for x in ch:
            x.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    r = n.post("/api/auth/mfa/verify", json={"code": next_code(secret)})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_challenge_expired"


def test_recovery_code_single_use_and_regenerate():   # SEC-06
    uid, email, _ = make_user()
    c = login(email)
    secret, codes = enable_mfa(c)
    n = TestClient(app, base_url="http://testserver")
    n.post("/api/auth/password-login", json={"login": email, "password": PW})
    r = n.post("/api/auth/mfa/recovery", json={"code": codes[0].upper()})
    assert r.status_code == 200 and r.json()["recovery_left"] == 9
    m = TestClient(app, base_url="http://testserver")
    m.post("/api/auth/password-login", json={"login": email, "password": PW})
    assert m.post("/api/auth/mfa/recovery", json={"code": codes[0]}).status_code == 401          # второй раз — нет
    expire_step_up(email)                      # подтверждение при включении TOTP уже истекло
    assert c.post("/api/auth/mfa/recovery/regenerate").json()["detail"]["code"] == "step_up_required"
    assert c.post("/api/auth/step-up", json={"code": next_code(secret)}).status_code == 200
    new = c.post("/api/auth/mfa/recovery/regenerate").json()["recovery_codes"]
    assert len(new) == 10 and m.post("/api/auth/mfa/recovery", json={"code": codes[1]}).status_code == 401   # старые недействительны
    assert m.post("/api/auth/mfa/recovery", json={"code": new[0]}).status_code == 200


def test_required_policy_blocks_everything_until_setup(monkeypatch):   # SEC-07
    s = get_settings()
    monkeypatch.setattr(s, "mfa_policy", "required")
    uid, email, _ = make_user()
    c = login(email)
    me = c.get("/api/auth/me").json()
    assert me["security"]["mfa_setup_required"] is True
    r = c.get("/api/deals")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_setup_required"
    assert c.get("/api/notifications").status_code == 403
    enable_mfa(c)
    assert c.get("/api/deals").status_code == 200
    assert c.post("/api/auth/mfa/disable", json={"password": PW, "code": "000000"}).status_code == 409   # при required отключить нельзя


def test_mfa_reset_by_admin_revokes_and_notifies():   # SEC-08
    uid, email, _ = make_user()
    c = login(email)
    enable_mfa(c)
    a = TestClient(app, base_url="http://testserver")
    assert a.post(f"/api/security/users/{uid}/mfa/reset", headers=H(MGR)).status_code == 403
    expire_step_up(ADM)
    assert a.post(f"/api/security/users/{uid}/mfa/reset", headers=H(ADM)).json()["detail"]["code"] == "step_up_required"
    step_up(a, ADM)
    r = a.post(f"/api/security/users/{uid}/mfa/reset", headers=H(ADM))
    assert r.status_code == 200 and r.json()["sessions_revoked"] >= 1
    assert c.get("/api/auth/me").status_code == 401
    n = login(email)                                    # без TOTP — обычный вход снова
    assert n.get("/api/auth/me").json()["security"]["mfa_enabled"] is False
    with SessionLocal() as db:
        gd_id = db.scalar(select(User.id).where(User.email == GD))
        assert db.scalar(select(NotificationLog).where(NotificationLog.user_id == gd_id, NotificationLog.event == "security",
                                                       NotificationLog.channel == "app", NotificationLog.text.contains("Сброшен"))) is not None
    me_id = a.get("/api/auth/me", headers=H(ADM)).json()["id"]
    assert a.post(f"/api/security/users/{me_id}/mfa/reset", headers=H(ADM)).status_code == 409


def test_oidc_style_step_up_with_totp_and_wrong_password():
    uid, email, _ = make_user()
    c = login(email)
    secret, _ = enable_mfa(c)
    assert c.get("/api/auth/step-up").json()["method"] == "totp"
    assert c.post("/api/auth/step-up", json={"code": "000000"}).status_code == 401
    p = make_user()
    pc = login(p[1])
    assert pc.post("/api/auth/step-up", json={"password": "wrong"}).status_code == 401
    assert pc.post("/api/auth/step-up", json={"password": PW}).json()["method"] == "password"


def test_dev_step_up_only_for_passwordless_dev_session():
    uid, email, _ = make_user(password=None)
    c = TestClient(app, base_url="http://testserver")
    assert c.get("/api/auth/step-up", headers=H(email)).json()["method"] == "dev"
    assert c.post("/api/auth/step-up", headers=H(email), json={}).status_code == 200


# ---------------------------------------------------------------- пароли

@pytest.mark.parametrize("pw,ok", [
    ("Short-pw-1", False), ("abcdefghijklmn", False), ("123456789012345", False), ("Password1234", False), ("P@ssw0rd1234", False),
    ("Kx7-polar-river-42", True), ("a" * 127 + "1", True), ("a" * 128 + "1", False),
])
def test_password_policy_via_api(pw, ok):   # SEC-09
    _, email, _ = make_user()
    c = login(email)
    r = c.post("/api/auth/change-password", json={"current_password": PW, "new_password": pw})
    assert (r.status_code == 200) is ok, r.text
    if not ok:
        assert r.json()["detail"]["code"] == "password_policy"


def test_password_login_derived_and_history():
    uid, email, name = make_user(name="Северов Олег (тест)")
    c = login(email)
    for bad in ("Олег2026!!!!!!", "Северов123456", "Северов-Олег-1", "СеверовСеверов1"):
        r = c.post("/api/auth/change-password", json={"current_password": PW, "new_password": bad})
        assert r.status_code == 422, bad
    pws = [f"Granite-{i}-wolf-river" for i in range(6)]
    cur = PW
    for p in pws:
        assert c.post("/api/auth/change-password", json={"current_password": cur, "new_password": p}).status_code == 200
        cur = p
    for old in pws[1:-1]:
        r = c.post("/api/auth/change-password", json={"current_password": cur, "new_password": old})
        assert r.status_code == 422 and "прежними" in r.json()["detail"]["message"], old
    assert c.post("/api/auth/change-password", json={"current_password": cur, "new_password": cur}).status_code == 422
    with SessionLocal() as db:
        assert len(db.scalars(select(PasswordHistory).where(PasswordHistory.user_id == uid)).all()) == 5


def test_old_pbkdf2_hash_rehashed_on_login():
    uid, email, _ = make_user()
    import hashlib
    salt = "abcd" * 4
    old = f"pbkdf2_sha256$1000${salt}${hashlib.pbkdf2_hmac('sha256', PW.encode(), salt.encode(), 1000).hex()}"
    with SessionLocal() as db:
        db.get(User, uid).password_hash = old
        db.commit()
    login(email)
    with SessionLocal() as db:
        h = db.get(User, uid).password_hash
        assert h.split("$")[1] == "200000" and h != old
    login(email)


def test_temporary_password_restricts_until_changed():   # SEC-10
    uid, email, _ = make_user()
    u = login(email)
    a = admin_client()
    r = a.post(f"/api/security/users/{uid}/password-reset", headers=H(ADM))
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert u.get("/api/auth/me").status_code == 401                           # прежние входы отозваны
    assert TestClient(app).post("/api/auth/password-login", json={"login": email, "password": PW}).status_code == 401   # старый пароль не работает
    t = login(email, temp)
    assert t.get("/api/auth/me").json()["security"]["password_must_change"] is True
    r = t.get("/api/deals")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "password_change_required"
    assert t.post("/api/auth/change-password", json={"current_password": temp, "new_password": "Fresh-cedar-river-9"}).status_code == 200
    assert t.get("/api/deals").status_code == 200
    with SessionLocal() as db:
        blob = " ".join(str(x.after) + str(x.before) for x in db.scalars(select(AuditLog).where(AuditLog.entity_id == uid)).all())
        blob += " ".join(str(e.meta) for e in db.scalars(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid)).all())
        assert temp not in blob and "Fresh-cedar-river-9" not in blob and PW not in blob


# ---------------------------------------------------------------- отключение сотрудника

def _arrange_offboard_target():
    uid, email, name = make_user(roles=("manager",))
    deputy_of_id, _, _ = make_user()
    u = login(email)
    api = TestClient(app, base_url="http://testserver")
    cl = api.get("/api/refs/clients", headers=H(MGR)).json()[0]
    deal = api.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "тест отключения"}).json()
    with SessionLocal() as db:
        db.add(AccessUserOverride(user_id=uid, field_group="legal_docs", level=1, comment="тест"))
        db.get(User, deputy_of_id).deputy_id = uid                   # он замещает другого
        db.add(Task(deal_id=deal["id"], role="manager", assignee_id=uid, title="задача отключаемого", due=date.today()))
        db.commit()
    return uid, email, name, u, deputy_of_id


def test_offboard_atomic_effects():   # OFF-01
    uid, email, name, u, deputy_of_id = _arrange_offboard_target()
    a = TestClient(app, base_url="http://testserver")
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(MGR), json={"confirm_name": name}).status_code == 403
    expire_step_up(ADM)
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name}).json()["detail"]["code"] == "step_up_required"
    step_up(a, ADM)
    pv = a.get(f"/api/security/users/{uid}", headers=H(ADM)).json()["offboard_preview"]
    assert pv["active_sessions"] == 1 and pv["open_tasks"] == 1 and pv["active_overrides"] == 1 and pv["deputy_links"] == 1
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": "Не тот"}).status_code == 422
    r = a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name.upper()})
    assert r.status_code == 200, r.text
    cnt = r.json()["counts"]
    assert cnt["sessions_revoked"] == 1 and cnt["tasks_flagged"] == 1 and cnt["overrides_ended"] == 1 and cnt["delegations_ended"] == 1
    assert u.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/password-login", json={"login": email, "password": PW}).status_code == 401
    with SessionLocal() as db:
        user = db.get(User, uid)
        assert not user.is_active and user.offboarded_at and user.offboarded_by
        assert db.scalar(select(AccessUserOverride).where(AccessUserOverride.user_id == uid)).valid_to is not None
        assert db.scalar(select(Task).where(Task.assignee_id == uid)).needs_reassignment is True
        assert db.get(User, deputy_of_id).deputy_to < date.today()
        ev = db.scalar(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid, SecurityEvent.action == "offboard"))
        assert ev is not None and ev.meta["sessions_revoked"] == 1 and ev.request_id
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name}).status_code == 409   # повтор — без дублей


def test_offboard_through_old_endpoints():   # OFF-02
    uid, email, name = make_user()
    u = login(email)
    a = admin_client()
    assert a.patch(f"/api/admin/users/{uid}", headers=H(ADM), json={"is_active": False}).status_code == 200
    assert u.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid, SecurityEvent.action == "offboard")) is not None
    uid2, email2, _ = make_user()
    u2 = login(email2)
    with SessionLocal() as db:
        db.add(AuditLog(user_id=uid2, entity="user", entity_id=uid2, action="touch"))   # есть ссылки → «удаление» отключает
        db.commit()
    r = a.delete(f"/api/admin/users/{uid2}", headers=H(ADM)).json()
    assert r["deactivated"] and u2.get("/api/auth/me").status_code == 401


def test_offboard_safeguards(monkeypatch):   # OFF-03
    a = admin_client()
    me_id = a.get("/api/auth/me", headers=H(ADM)).json()["id"]
    with SessionLocal() as db:
        name = db.get(User, me_id).display_name
    assert a.post(f"/api/security/users/{me_id}/offboard", headers=H(ADM), json={"confirm_name": name}).status_code == 409
    uid, _, uname = make_user(roles=("admin",))
    monkeypatch.setattr(offboarding, "security_admin_ids", lambda db, exclude=None: [])
    r = a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": uname})
    assert r.status_code == 409 and "последний" in r.json()["detail"]


def test_offboard_rollback_on_failed_step(monkeypatch):   # OFF-04
    uid, email, name, u, _ = _arrange_offboard_target()
    a = admin_client()
    def boom(db, user):
        raise RuntimeError("сбой шага")
    monkeypatch.setattr(offboarding, "_flag_tasks", boom)
    with pytest.raises(RuntimeError):
        a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name})
    assert u.get("/api/auth/me").status_code == 200                       # сессия не отозвана — откатилось всё
    with SessionLocal() as db:
        assert db.get(User, uid).is_active is True
        assert db.scalar(select(AccessUserOverride).where(AccessUserOverride.user_id == uid)).valid_to is None
        assert db.scalar(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid, SecurityEvent.action == "offboard")) is None


def test_reactivate_does_not_revive_sessions_or_overrides():   # OFF-05
    uid, email, name, u, _ = _arrange_offboard_target()
    old = u.cookies.get(SESSION_COOKIE)
    a = admin_client()
    assert a.post(f"/api/security/users/{uid}/offboard", headers=H(ADM), json={"confirm_name": name}).status_code == 200
    assert a.post(f"/api/security/users/{uid}/reactivate", headers=H(ADM), json={"confirm_name": "x"}).status_code == 422
    assert a.post(f"/api/security/users/{uid}/reactivate", headers=H(ADM), json={"confirm_name": name}).status_code == 200
    replay = TestClient(app, base_url="http://testserver")
    replay.cookies.set(SESSION_COOKIE, old)
    assert replay.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(AccessUserOverride).where(AccessUserOverride.user_id == uid)).valid_to is not None
    assert login(email).get("/api/auth/me").status_code == 200


def test_security_users_list_and_events_permissions():
    a = TestClient(app, base_url="http://testserver")
    r = a.get("/api/security/users", headers=H(ADM))
    assert r.status_code == 200 and r.json()["can_edit"] is True and all("password_hash" not in x for x in r.json()["items"])
    assert a.get("/api/security/users", headers=H(MGR)).status_code == 403
    assert a.get("/api/security/events?limit=5", headers=H(GD)).status_code == 200
    assert a.get("/api/security/events", headers=H(MGR)).status_code == 403


def test_no_secret_canaries_in_events_or_audit():
    uid, email, _ = make_user(password=CANARY + "1a")
    c = TestClient(app, base_url="http://testserver")
    c.post("/api/auth/password-login", json={"login": email, "password": CANARY + "wrong"})
    lc = login(email, CANARY + "1a")
    secret, codes = enable_mfa(lc, CANARY + "1a")
    lc.post("/api/auth/step-up", json={"code": "000000"})
    with SessionLocal() as db:
        blob = " ".join(str(e.meta) + e.action + e.entity_id for e in db.scalars(select(SecurityEvent).where(SecurityEvent.subject_user_id == uid)).all())
        blob += " ".join(str(x.after) for x in db.scalars(select(AuditLog).where(AuditLog.entity_id == uid)).all())
    assert CANARY not in blob and secret not in blob and not any(cd in blob for cd in codes)


def test_session_status_does_not_extend_and_reports_reason():
    """D-041: опрос для предупреждения «скоро выход» не считается активностью."""
    _, email, _ = make_user()
    c = login(email)
    sid = c.get("/api/auth/sessions").json()["items"][0]["sid"]
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        s.last_seen_at = s.last_seen_at - timedelta(hours=7, minutes=57)
        s.idle_expires_at = s.idle_expires_at - timedelta(hours=7, minutes=57)
        db.commit()
    for _ in range(3):
        st = c.get("/api/auth/session-status").json()
        assert st["session"] and st["reason"] == "idle" and 0 < st["seconds_left"] <= 180
    # фоновые опросы (колокольчик, курсы) тоже не продлевают вход — иначе открытая вкладка никогда не выходила бы по бездействию
    assert c.get("/api/notifications/unread-count", headers={"X-Background-Request": "1"}).status_code == 200
    assert c.get("/api/auth/session-status").json()["seconds_left"] <= 180
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        s.last_seen_at = s.last_seen_at - timedelta(minutes=1)   # чтобы следующий обычный запрос продлил
        db.commit()
    assert c.get("/api/auth/me").status_code == 200              # «Продолжить работу»
    st = c.get("/api/auth/session-status").json()
    assert st["seconds_left"] > 7 * 3600


def test_old_30_day_session_is_cut_to_12_hours():
    """Входы, выданные до D-041 с «Запомнить меня» на 30 дней, ограничиваются сразу — по времени входа."""
    _, email, _ = make_user()
    c = login(email)
    sid = c.get("/api/auth/sessions").json()["items"][0]["sid"]
    t = datetime.now(timezone.utc)
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession).where(AuthSession.sid == sid))
        s.created_at, s.remember = t - timedelta(hours=13), True
        s.absolute_expires_at, s.idle_expires_at = s.created_at + timedelta(days=30), t + timedelta(days=29)
        db.commit()
    r = c.get("/api/auth/me")
    assert r.status_code == 401 and "Срок сессии истёк" in r.json()["detail"]
    assert all(x["sid"] != sid for x in login(email).get("/api/auth/sessions").json()["items"])


@pytest.mark.parametrize("raw,status", [
    ("", "dev"), ("  {k}  ", "ok"), ('"{k}"', "ok"), ("'{k}'\n", "ok"), ("q3V…Xk=", "invalid"), ("abc", "invalid"), ("{k}{k}", "invalid"),
])
def test_mfa_key_status_and_normalization(monkeypatch, raw, status):
    """Ключ с пробелами/кавычками по краям принимается; многоточие из примера или неполная строка — «задан неверно»."""
    import base64, os as _os
    k = base64.b64encode(_os.urandom(32)).decode()
    s = get_settings()
    monkeypatch.setattr(s, "mfa_encryption_key", raw.format(k=k))
    assert mfa_svc.key_status(s) == status
    if status == "ok":
        blob, kid = mfa_svc.encrypt("SECRET", s)
        assert kid == "env1" and mfa_svc.decrypt(blob, kid, s) == "SECRET"
    if status == "invalid":
        with pytest.raises(Exception) as e:
            mfa_svc.encrypt("SECRET", s)
        assert "задан неверно" in str(e.value.detail)


def test_mfa_key_missing_in_prod_is_reported(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "mfa_encryption_key", "")
    monkeypatch.setattr(s, "env", "prod")
    assert mfa_svc.key_status(s) == "missing" and not mfa_svc.available(s)
