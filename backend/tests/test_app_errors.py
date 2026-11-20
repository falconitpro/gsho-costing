"""Рабочее место: сводка под роль, блоки по роли, личная раскладка и закрепление блоков ЗГД."""
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
from scripts.seed import run as seed  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import AppError, NotificationLog  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, GD, ADM = "manager@example.com", "evgeniy@dobrov.pro", "admin@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    from fastapi import APIRouter
    r = APIRouter()

    @r.get("/api/_boom/{deal_id}")
    def boom(deal_id: str):
        raise ValueError(f"сломалось для ivan@example.com, сделка {deal_id}")
    app.include_router(r)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_server_error_recorded_once_and_notified(client):
    """D-117: необработанная ошибка сервера — запись, номер ошибки в ответе, уведомление администратору и ЗГД;
    повтор на другой сделке — счётчик той же записи; без почты и идентификаторов в сообщении."""
    with SessionLocal() as db:
        n0 = db.query(NotificationLog).filter(NotificationLog.event == "app_error").count()
    r = client.get("/api/_boom/11111111-2222-3333-4444-555555555555", headers=H(MGR))
    assert r.status_code == 500 and "Номер ошибки" in r.json()["detail"] and r.headers.get("x-request-id")
    client.get("/api/_boom/99999999-2222-3333-4444-555555555555", headers=H(MGR))
    with SessionLocal() as db:
        rows = db.query(AppError).filter(AppError.source == "server", AppError.kind == "ValueError").all()
        assert len(rows) == 1 and rows[0].count == 2 and rows[0].where == "GET /api/_boom/{id}"
        assert "ivan@example.com" not in rows[0].message and "<почта>" in rows[0].message and "<id>" in rows[0].message
        assert "upload_gate" not in rows[0].trace and "download_audit" not in rows[0].trace   # обёртки запроса — не место ошибки
        n1 = db.query(NotificationLog).filter(NotificationLog.event == "app_error").count()
        assert n1 - n0 >= 2            # администратор и ЗГД, один раз на новую ошибку
    lst = client.get("/api/admin/app-errors", headers=H(GD)).json()
    e = next(x for x in lst["rows"] if x["kind"] == "ValueError")
    assert e["count"] == 2 and lst["open"] >= 1
    assert client.get("/api/admin/app-errors", headers=H(MGR)).status_code == 403
    # «Исправлено» → повтор открывает запись снова и снова уведомляет
    assert client.post(f"/api/admin/app-errors/{e['id']}/resolve", headers=H(GD)).status_code == 200
    assert all(x["id"] != e["id"] for x in client.get("/api/admin/app-errors", headers=H(GD)).json()["rows"])
    client.get("/api/_boom/77777777-2222-3333-4444-555555555555", headers=H(MGR))
    with SessionLocal() as db:
        again = db.get(AppError, e["id"])
        assert again.resolved_at is None and again.count == 3
        assert db.query(NotificationLog).filter(NotificationLog.event == "app_error").count() > n1


def test_browser_error_and_rate_limit(client):
    stack = "TypeError: x is undefined\n    at DealCard (http://crm/assets/index-abc.js?v=1:12:345)\n    at render (http://crm/assets/vendor.js:1:2)"
    body = {"kind": "TypeError", "message": "x is undefined, звонить +7 900 123-45-67", "stack": stack, "url": "/deals/11111111-2222-3333-4444-555555555555"}
    assert client.post("/api/admin/client-errors", headers=H(MGR), json=body).status_code == 204
    with SessionLocal() as db:
        e = db.query(AppError).filter(AppError.source == "browser", AppError.kind == "TypeError").one()
        assert e.where == "/deals/{id}" and "<телефон>" in e.message and "assets/index-abc.js:12 in DealCard" in e.trace
    for _ in range(40):
        client.post("/api/admin/client-errors", headers=H(MGR), json=body)
    with SessionLocal() as db:
        assert db.query(AppError).filter(AppError.source == "browser", AppError.kind == "TypeError").one().count <= 30
    client.cookies.clear()
    assert client.post("/api/admin/client-errors", json=body).status_code in (401, 403)   # только после входа
