"""Страница входа: вход по логину и паролю, «Запомнить меня», блокировка после неверных попыток, смена и сброс пароля, выход."""
from __future__ import annotations

import os
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
        assert "Max-Age=2592000" in set_cookie and "HttpOnly" in set_cookie
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
        # без «Запомнить меня» — сессия 8 часов
        r = c.post("/api/auth/password-login", json={"login": MGR, "password": DEV_PASSWORD, "remember": False})
        assert "Max-Age=28800" in r.headers["set-cookie"]
        c.cookies.clear()

        # смена своего пароля: нужен текущий, проверка сложности
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": "bad", "new_password": "NewPass123"}).status_code == 403
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": DEV_PASSWORD, "new_password": "short"}).status_code == 422
        assert c.post("/api/auth/password", headers=H(LOG), json={"current_password": DEV_PASSWORD, "new_password": "NewPass123"}).status_code == 200
        c.cookies.clear()
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": "NewPass123"}).status_code == 200
        c.cookies.clear()

        # блокировка после 5 ошибок; сброс пароля ЗГД снимает блок
        uid = next(u for u in c.get("/api/team", headers=H(GD)).json()["users"] if u["email"] == LOG)["id"]
        for _ in range(5):
            assert c.post("/api/auth/password-login", json={"login": LOG, "password": "nope-nope"}).status_code == 401
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": "NewPass123"}).status_code == 429
        assert c.put(f"/api/auth/users/{uid}/password", headers=H(MGR), json={"new_password": "Reset12345"}).status_code == 403
        assert c.put(f"/api/auth/users/{uid}/password", headers=H(GD), json={"new_password": "Reset12345"}).status_code == 200
        c.cookies.clear()
        assert c.post("/api/auth/password-login", json={"login": LOG, "password": "Reset12345"}).status_code == 200
        c.cookies.clear()
        c.post("/api/auth/password", headers=H(LOG), json={"current_password": "Reset12345", "new_password": DEV_PASSWORD})
