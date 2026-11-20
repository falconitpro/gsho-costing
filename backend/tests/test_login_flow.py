"""Вход по паролю целиком, как его проходит сотрудник рабочей системы: блокировка после 5 ошибок, одинаковый ответ
для неизвестного логина, вход с кодом из приложения, выход, подтверждение (step-up) для опасных действий.
Дополняет test_security_core.py — там проверены отдельные механизмы, здесь сквозные сценарии."""
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

from app.api import auth as auth_api  # noqa: E402
from app.core.auth import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, User  # noqa: E402
from app.services import mfa as mfa_svc  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

PW = "Basalt-lake-2026-north"


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    seed()


@pytest.fixture(autouse=True)
def _clean_ip_counter():
    auth_api._ip_fails.clear()      # счётчик ошибок по адресу общий для процесса тестов
    yield
    auth_api._ip_fails.clear()


def make_user(roles=("manager",)) -> tuple[str, str]:
    email = f"login.{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as db:
        u = User(email=email, external_id=email, display_name=f"Входов Тест {email[6:10]}", roles=list(roles),
                 password_hash=hash_password(PW), password_set_at=datetime.now(timezone.utc))
        db.add(u)
        db.commit()
        return u.id, email


def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def try_login(c: TestClient, email: str, password: str):
    return c.post("/api/auth/password-login", json={"login": email, "password": password, "remember": False})


def test_lockout_after_five_failures_and_unlock():
    uid, email = make_user()
    c = client()
    for i in range(5):
        r = try_login(c, email, "Wrong-password-123")
        assert r.status_code == 401 and r.json()["detail"] == "Неверный логин или пароль", (i, r.text)
    # после пятой ошибки — блок, даже с верным паролем
    r = try_login(c, email, PW)
    assert r.status_code == 429 and "заблокирована" in r.json()["detail"]
    assert c.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.entity_id == uid, AuditLog.action == "login_locked")) is not None
        u = db.get(User, uid)
        u.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)   # 15 минут прошли
        db.commit()
    r = try_login(c, email, PW)
    assert r.status_code == 200 and r.json()["mfa_required"] is False
    assert c.get("/api/auth/me").json()["email"] == email
    with SessionLocal() as db:
        u = db.get(User, uid)
        assert (u.failed_logins or 0) == 0 and u.locked_until is None


def test_success_resets_failure_counter():
    uid, email = make_user()
    c = client()
    for _ in range(4):
        assert try_login(c, email, "Wrong-password-123").status_code == 401
    assert try_login(c, email, PW).status_code == 200
    c2 = client()
    for _ in range(4):          # счётчик сброшен: ещё 4 ошибки не блокируют
        assert try_login(c2, email, "Wrong-password-123").status_code == 401
    assert try_login(c2, email, PW).status_code == 200


def test_unknown_login_same_answer_as_wrong_password():
    _, email = make_user()
    c = client()
    a = try_login(c, f"nobody.{uuid.uuid4().hex[:6]}@example.com", PW)
    b = try_login(c, email, "Wrong-password-123")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_login_with_app_code_then_logout():
    _, email = make_user()
    c = client()
    assert try_login(c, email, PW).status_code == 200
    r = c.post("/api/auth/mfa/setup", json={"password": PW})
    secret = r.json()["secret"]
    assert c.post("/api/auth/mfa/confirm", json={"code": mfa_svc.code_now(secret)}).status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401

    m = client()
    r = try_login(m, email, PW)
    assert r.status_code == 200 and r.json()["mfa_required"] is True
    assert m.get("/api/auth/me").status_code == 401            # до кода сессии нет
    assert m.post("/api/auth/mfa/verify", json={"code": "000000"}).status_code in (401, 429)
    import time
    assert m.post("/api/auth/mfa/verify", json={"code": mfa_svc.code_now(secret, time.time() + 30)}).status_code == 200
    assert m.get("/api/auth/me").json()["email"] == email
    assert m.post("/api/auth/logout").status_code in (200, 204)
    assert m.get("/api/auth/me").status_code == 401


def test_step_up_by_password_then_dangerous_action_allowed():
    """Админ-действие требует свежего подтверждения: без него — отказ, после ввода пароля — проходит."""
    _, email = make_user(roles=("admin",))
    _, victim = make_user()
    c = client()
    assert try_login(c, email, PW).status_code == 200
    with SessionLocal() as db:
        vid = db.scalar(select(User.id).where(User.email == victim))
    r = c.post(f"/api/security/users/{vid}/sessions/revoke-all")
    assert r.status_code in (401, 403) and ("step" in r.text.lower() or "подтверд" in r.text.lower()), r.text
    assert c.get("/api/auth/step-up").json()["method"] == "password"
    assert c.post("/api/auth/step-up", json={"password": "Wrong-password-123"}).status_code == 401
    assert c.post("/api/auth/step-up", json={"password": PW}).status_code == 200
    assert c.post(f"/api/security/users/{vid}/sessions/revoke-all").status_code == 200
