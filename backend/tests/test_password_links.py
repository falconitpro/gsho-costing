"""D-132: приглашение нового сотрудника и восстановление пароля по одноразовой ссылке «Задать пароль»."""
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

from app.main import app  # noqa: E402
from app.services import password_links as pl  # noqa: E402
from scripts.seed import DEV_PASSWORD, TEST_LOGIN, run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
GD, ADM = TEST_LOGIN, "admin@example.com"
PW1, PW2 = "Severny-kovsh-2031", "Novy-zub-4412-tundra"


def _tok(url: str) -> str:
    return url.split("t=", 1)[1]


def test_invite_reset_forgot():
    seed()
    pl._forgot_hits.clear()
    with TestClient(app) as c:
        # создание сотрудника: почта на стенде не подключена — ссылка возвращается администратору
        r = c.post("/api/admin/users", headers=H(GD), json={"email": "new.clerk@example.com", "display_name": "Новикова Анна", "roles": ["clerk"],
                                                             "link_email": "anna.personal@example.org"})
        assert r.status_code == 201, r.text
        u = r.json()
        link = u["link"]
        assert link["sent"] is False and link["to"] == "anna.personal@example.org" and "/set-password?t=" in link["url"]
        tok = _tok(link["url"])
        # проверка ссылки и слабый пароль
        assert c.post("/api/auth/link/check", json={"token": tok}).json()["login"] == "new.clerk@example.com"
        assert c.post("/api/auth/link/set", json={"token": tok, "new_password": "short1"}).status_code == 422
        assert c.post("/api/auth/link/set", json={"token": tok, "new_password": PW1}).status_code == 200
        # одноразовая
        assert c.post("/api/auth/link/check", json={"token": tok}).status_code == 410
        assert c.post("/api/auth/link/set", json={"token": tok, "new_password": PW2}).status_code == 410
        assert c.post("/api/auth/password-login", json={"login": "new.clerk@example.com", "password": PW1}).status_code == 200
        c.cookies.clear()
        # сброс — только с подтверждением (step-up); прежний пароль больше не действует
        from app.db import SessionLocal
        from app.models import AuthSession, User
        with SessionLocal() as db:   # подтверждение живёт 10 минут в общей dev-сессии — другие тесты могли его оставить
            for row in db.query(AuthSession).filter(AuthSession.user_id == db.query(User.id).filter(User.email == ADM).scalar_subquery()):
                row.last_step_up_at = None
            db.commit()
        assert c.post(f"/api/security/users/{u['id']}/password-link", headers=H(ADM), json={}).status_code == 403
        assert c.post("/api/auth/step-up", headers=H(ADM), json={"password": DEV_PASSWORD}).status_code == 200
        r = c.post(f"/api/security/users/{u['id']}/password-link", headers=H(ADM), json={})
        assert r.status_code == 200 and r.json()["to"] == "new.clerk@example.com"
        tok2 = _tok(r.json()["url"])
        assert c.post("/api/auth/password-login", json={"login": "new.clerk@example.com", "password": PW1}).status_code == 401
        # новая ссылка гасит прежнюю
        r3 = c.post(f"/api/security/users/{u['id']}/password-link", headers=H(ADM), json={"reset": False})
        assert c.post("/api/auth/link/check", json={"token": tok2}).status_code == 410
        tok3 = _tok(r3.json()["url"])
        # сброшенный пароль задать снова нельзя (история)
        assert c.post("/api/auth/link/set", json={"token": tok3, "new_password": PW1}).status_code == 422
        assert c.post("/api/auth/link/set", json={"token": tok3, "new_password": PW2}).status_code == 200
        # «Забыли пароль?» — одинаковый ответ, пароль не сбрасывается, лимит 3 в час
        a = c.post("/api/auth/forgot", json={"email": "new.clerk@example.com"}).json()
        b = c.post("/api/auth/forgot", json={"email": "nobody@example.com"}).json()
        assert a == b
        assert c.post("/api/auth/password-login", json={"login": "new.clerk@example.com", "password": PW2}).status_code == 200
        c.cookies.clear()
        c.post("/api/auth/forgot", json={"email": "new.clerk@example.com"})
        assert c.post("/api/auth/forgot", json={"email": "new.clerk@example.com"}).status_code == 429
        # не администратор — нельзя
        assert c.post(f"/api/security/users/{u['id']}/password-link", headers=H("manager@example.com"), json={}).status_code == 403
        # хэш пароля не отдаётся в списке сотрудников
        assert all("password_hash" not in x for x in c.get("/api/admin/users", headers=H(GD)).json())
