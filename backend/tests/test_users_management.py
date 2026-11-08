"""Сотрудники: добавление/удаление и смена e-mail — администратор и ЗГД; сам сотрудник заполняет пустые поля сразу, а меняет заполненные с подтверждением."""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
ADM, GD, CD, MGR = "admin@example.com", "evgeniy@dobrov.pro", "cd@example.com", "manager@example.com"


def test_users_add_edit_confirm_delete():
    seed()
    with TestClient(app) as c:
        tag = uuid.uuid4().hex[:6]
        email = f"new.{tag}@example.com"
        # добавить — только админ / ЗГД
        assert c.post("/api/admin/users", headers=H(CD), json={"email": email, "display_name": "Новый Сотрудник", "roles": ["manager"]}).status_code == 403
        u = c.post("/api/admin/users", headers=H(GD), json={"email": email, "display_name": "Новый Сотрудник", "roles": ["manager"]}).json()
        assert u["id"]
        # сам сотрудник заполняет пустые поля сразу
        r = c.put(f"/api/team/{u['id']}/contacts", headers=H(email), json={"contacts": {"mobile": "+7 900 000-00-01", "position": "Менеджер"}}).json()
        assert r["contacts"]["mobile"] == "+7 900 000-00-01" and r["applied"] and not r["pending"] and r["pending_changes"] == {}
        # изменение заполненного поля и ФИО — на подтверждение; прежние значения действуют
        r = c.put(f"/api/team/{u['id']}/contacts", headers=H(email), json={"contacts": {"mobile": "+7 900 000-00-02", "telegram": "@new"}, "display_name": "Новый Сотрудникович"}).json()
        assert r["contacts"]["mobile"] == "+7 900 000-00-01" and r["contacts"]["telegram"] == "@new" and r["display_name"] == "Новый Сотрудник"
        assert set(r["pending"]) == {"mobile", "display_name"} and set(r["pending_changes"]["fields"]) == {"mobile", "display_name"}
        # подтверждает только админ / ЗГД, частично
        assert c.post(f"/api/team/{u['id']}/pending/decide", headers=H(CD), json={"approve": True}).status_code == 403
        r = c.post(f"/api/team/{u['id']}/pending/decide", headers=H(ADM), json={"approve": True, "fields": ["mobile"]}).json()
        assert r["contacts"]["mobile"] == "+7 900 000-00-02" and list(r["pending_changes"]["fields"]) == ["display_name"]
        r = c.post(f"/api/team/{u['id']}/pending/decide", headers=H(GD), json={"approve": False, "comment": "ФИО как в паспорте"}).json()
        assert r["display_name"] == "Новый Сотрудник" and r["pending_changes"] == {}
        assert c.post(f"/api/team/{u['id']}/pending/decide", headers=H(GD), json={"approve": True}).status_code == 409
        # администратор меняет всё сразу, включая e-mail (уникальность, вход по новому адресу)
        r = c.put(f"/api/team/{u['id']}/contacts", headers=H(ADM), json={"contacts": {"mobile": "+7 900 000-00-03"}, "display_name": "Иванов Иван"}).json()
        assert r["contacts"]["mobile"] == "+7 900 000-00-03" and r["display_name"] == "Иванов Иван" and not r["pending"]
        assert c.patch(f"/api/admin/users/{u['id']}", headers=H(CD), json={"email": f"x.{tag}@example.com"}).status_code == 403
        assert c.patch(f"/api/admin/users/{u['id']}", headers=H(ADM), json={"email": MGR}).status_code == 409
        new_email = f"ivanov.{tag}@example.com"
        assert c.patch(f"/api/admin/users/{u['id']}", headers=H(ADM), json={"email": new_email}).json()["email"] == new_email
        assert c.get("/api/team", headers=H(new_email)).status_code == 200          # вход по новому e-mail
        # удаление: себя нельзя; не-админу нельзя; у сотрудника есть записи в журнале → отключение с сохранением истории
        assert c.delete(f"/api/admin/users/{u['id']}", headers=H(CD)).status_code == 403
        me = next(x for x in c.get("/api/admin/users", headers=H(ADM)).json() if x["email"] == ADM)
        assert c.delete(f"/api/admin/users/{me['id']}", headers=H(ADM)).status_code == 409
        r = c.delete(f"/api/admin/users/{u['id']}", headers=H(GD)).json()
        assert r["deactivated"] and not r["deleted"]
        assert next(x for x in c.get("/api/admin/users", headers=H(ADM)).json() if x["id"] == u["id"])["is_active"] is False
        # совсем новый сотрудник без следов (аудит его создания — на админе) — удаляется
        fresh = c.post("/api/admin/users", headers=H(ADM), json={"email": f"fresh.{tag}@example.com", "display_name": "Временный", "roles": ["observer"]}).json()
        r = c.delete(f"/api/admin/users/{fresh['id']}", headers=H(ADM)).json()
        assert r["deleted"] and all(x["id"] != fresh["id"] for x in c.get("/api/admin/users", headers=H(ADM)).json())
