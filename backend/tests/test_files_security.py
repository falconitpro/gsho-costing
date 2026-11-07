"""Безопасная выдача файлов: html/svg — только скачивание, изображения и PDF — inline; лимит размера; имена не перезаписываются."""
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

from app.core.files import file_response  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD = "manager@example.com", "cd@example.com"


def test_uploads_served_safely_and_not_overwritten():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        fac = next(f for f in c.get("/api/refs/factories", headers=H(MGR)).json() if f["approval_status"] == "approved")
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "x"}).json()
        p = c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "SAFE-1", "machine_type": "Самосвал", "unit_price": 1}).json()
        # «руководство» в виде html — отдаётся как скачивание, не как страница
        doc = c.post(f"/api/deals/{d['id']}/positions/{p['id']}/documents", headers=H(MGR), files={"file": ("evil.html", b"<script>alert(1)</script>")},
                     data={"kind": "manual"}).json()
        r = c.get(doc["url"], headers=H(CD))
        assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment") and "text/html" not in r.headers["content-type"]
        assert r.headers["x-content-type-options"] == "nosniff"
        # документы сделки: тот же файл дважды — две записи, два разных файла
        for body in (b"%PDF v1", b"%PDF v2"):
            assert c.post(f"/api/deals/{d['id']}/documents", headers=H(MGR), files={"file": ("tz.pdf", body)}, data={"category": "client_spec"}).status_code == 201
        docs = [x for x in c.get(f"/api/deals/{d['id']}/documents", headers=H(CD)).json()["documents"] if x["file_name"] == "tz.pdf"]
        assert len(docs) == 2 and docs[0]["url"] != docs[1]["url"]
        assert {c.get(x["url"], headers=H(CD)).content for x in docs} == {b"%PDF v1", b"%PDF v2"}
        # лимит размера: заявленный Content-Length больше лимита → 413 до чтения тела
        r = c.post(f"/api/deals/{d['id']}/documents", headers={**H(MGR), "Content-Length": str(60 * 1024 * 1024)}, data=b"x")
        assert r.status_code == 413


def test_in_app_notifications():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "n"}).json()
        from app.db import SessionLocal
        from app.models import Deal
        from app.services.deal_service import notify
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            from sqlalchemy import select
            from app.models import User
            uid = db.scalar(select(User.id).where(User.email == MGR))
            notify(db, deal, uid, "task", None, "Новая задача по сделке: проверить")
            db.commit()
        n = c.get("/api/notifications/unread-count", headers=H(MGR)).json()["unread"]
        assert n >= 1
        lst = c.get("/api/notifications", headers=H(MGR)).json()
        it = next(x for x in lst["items"] if x["text"].startswith("Новая задача по сделке"))
        assert it["deal_number"] == d["number"] and it["read"] is False and it["event_title"] == "Задача"
        assert it["id"] not in {x["id"] for x in c.get("/api/notifications", headers=H(CD)).json()["items"]}   # чужие уведомления не видны
        assert c.post("/api/notifications/read", headers=H(MGR), json={"ids": [it["id"]]}).json()["unread"] == n - 1
        assert c.post("/api/notifications/read", headers=H(MGR), json={}).json()["unread"] == 0
