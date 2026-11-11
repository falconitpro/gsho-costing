"""Страница входа: вход по логину и паролю, «Запомнить меня», блокировка после неверных попыток, смена и сброс пароля, выход."""
from __future__ import annotations

import os
import re
import tempfile

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import SESSION_COOKIE, hash_password, verify_password  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, TEST_PASSWORD, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
NEW, RESET = "Kx7-polar-river-42", "Reset-tundra-2026"   # политика: 12+ символов, буква и цифра (docs/operations/04 §4)


def _restore(email):
    """Вернуть демо-пароль напрямую в БД: повторно задать его через API нельзя — история паролей (так и задумано)."""
    from datetime import datetime, timezone
    from app.db import SessionLocal
    from app.models import PasswordHistory, User
    with SessionLocal() as db:
        u = db.query(User).filter(User.email == email).one()
        u.password_hash, u.password_set_at, u.password_must_change = hash_password(DEV_PASSWORD), datetime.now(timezone.utc), False
        db.query(PasswordHistory).filter(PasswordHistory.user_id == u.id).delete()
        db.commit()
MGR, GD, LOG = "manager@example.com", TEST_LOGIN, "logist@example.com"


def test_hash_roundtrip():
    h = hash_password("Secret123")
    assert h.startswith("pbkdf2_sha256$") and verify_password("Secret123", h) and not verify_password("secret123", h) and not verify_password("x", None)


def test_password_login_flow():
    seed()
    with TestClient(app, base_url="http://testserver") as c:
        prov = c.get("/api/auth/providers").json()
        assert prov["password"] is True and prov["dev"] is True and "администратор" in prov["support"]
        # неверный пароль и неизвестный логин — одна и та же ошибка, без подсказок
        r1 = c.post("/api/auth/password-login", json={"login": MGR, "password": "wrong-pass"})
        r2 = c.post("/api/auth/password-login", json={"login": "nobody@example.com", "password": "wrong-pass"})
        assert r1.status_code == 401 and r2.status_code == 401 and r1.json()["detail"] == r2.json()["detail"] == "Неверный логин или пароль"
        assert c.get("/api/auth/me").status_code == 401
        # успешный вход: cookie сессии, /me без X-Dev-User
        r = c.post("/api/auth/password-login", json={"login": MGR.upper(), "password": DEV_PASSWORD, "remember": True})
        assert r.status_code == 200 and SESSION_COOKIE in r.cookies
        set_cookie = r.headers["set-cookie"]
        assert re.search(r"Max-Age=259(2000|1999)", set_cookie) and "HttpOnly" in set_cookie
        me = c.get("/api/auth/me").json()
        assert me["email"] == MGR
        # переключатель «Войти как» — только после входа; тестовая учётная запись входит по паролю
        assert c.get("/api/auth/dev-users").status_code == 200
        c.cookies.clear()
        assert c.get("/api/auth/dev-users").status_code == 401
        r = c.post("/api/auth/password-login", json={"login": TEST_LOGIN, "password": TEST_PASSWORD})
        assert r.status_code == 200 and c.get("/api/auth/me").json()["email"] == TEST_LOGIN
        assert any(u["email"] == TEST_LOGIN for u in c.get("/api/auth/dev-users").json())
        # выход
        assert c.post("/api/auth/logout").status_code == 200
        c.cookies.clear()
        assert c.get("/api/auth/me").status_code == 401
        # без «Запомнить меня» — cookie на абсолютный срок 24 ч, бездействие 8 ч проверяет сервер (docs/operations/04 §2.2)
        r = c.post("/api/auth/password-login", json={"login": MGR, "password": DEV_PASSWORD, "remember": False})
        assert re.search(r"Max-Age=86(400|399)", r.headers["set-cookie"])
        c.cookies.clear()

        # смена своего пароля: нужен текущий, проверка сложности
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": "bad", "new_password": NEW}).status_code == 403
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": DEV_PASSWORD, "new_password": "short"}).status_code == 422
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": DEV_PASSWORD, "new_password": NEW}).status_code == 200
        c.cookies.clear()
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": NEW}).status_code == 200
        c.cookies.clear()

        # блокировка после 5 ошибок; сброс пароля ЗГД снимает блок
        uid = next(u for u in c.get("/api/team", headers=H(GD)).json()["users"] if u["email"] == LOG)["id"]
        for _ in range(5):
            assert c.post("/api/auth/password-login", json={"login": LOG, "password": "nope-nope"}).status_code == 401
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": NEW}).status_code == 429
        assert c.put(f"/api/auth/users/{uid}/password", headers=H(MGR), json={"new_password": RESET}).status_code == 403
        assert c.put(f"/api/auth/users/{uid}/password", headers=H(GD), json={"new_password": RESET}).status_code == 200
        c.cookies.clear()
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": RESET}).status_code == 200
        c.cookies.clear()
        _restore(LOG)


def test_password_change_revokes_sessions():
    """Смена или сброс пароля отзывает прежние сессии сотрудника; при смене своего пароля текущая сессия продолжается."""
    seed()
    SRV = "service@example.com"
    new_pw, reset_pw = "Changed-taiga-77", RESET
    with TestClient(app, base_url="http://testserver") as c1, TestClient(app, base_url="http://testserver") as c2, TestClient(app, base_url="http://testserver") as gd:
        for c in (c1, c2):
            assert c.post("/api/auth/password-login", json={"login": SRV, "password": DEV_PASSWORD}).status_code == 200
            assert c.get("/api/auth/me").status_code == 200
        # сотрудник меняет пароль в первом браузере: там вход продолжается, во втором — отозван
        assert c1.post("/api/auth/password", json={"current_password": DEV_PASSWORD, "new_password": new_pw}).status_code == 200
        assert c1.get("/api/auth/me").json()["email"] == SRV
        r = c2.get("/api/auth/me")
        assert r.status_code == 401 and "войдите заново" in r.json()["detail"]
        assert c2.post("/api/auth/password-login", json={"login": SRV, "password": new_pw}).status_code == 200
        assert c2.get("/api/auth/me").status_code == 200
        # ЗГД сбрасывает пароль — обе сессии сотрудника отозваны
        uid = next(u for u in gd.get("/api/team", headers=H(GD)).json()["users"] if u["email"] == SRV)["id"]
        assert gd.put(f"/api/auth/users/{uid}/password", headers=H(GD), json={"new_password": reset_pw}).status_code == 200
        assert c1.get("/api/auth/me").status_code == 401 and c2.get("/api/auth/me").status_code == 401
        assert c1.post("/api/auth/password-login", json={"login": SRV, "password": reset_pw}).status_code == 200
        assert c1.get("/api/auth/me").status_code == 200
        assert c1.get("/api/auth/me").json()["security"]["password_must_change"] is True   # временный пароль администратора
        _restore(SRV)
