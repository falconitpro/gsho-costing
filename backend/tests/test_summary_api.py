"""Сводные экраны: Отгрузки, Деньги, Сервис, Закупки — доступ по ролям и форма ответа."""
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

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"
KINDS = ("shipments", "money", "service", "purchasing")


def test_summary_screens():
    seed()
    with TestClient(app) as c:
        # КД видит все четыре сводки
        for kind in KINDS:
            r = c.get(f"/api/summary/{kind}", headers=H(CD))
            assert r.status_code == 200, (kind, r.text)
            body = r.json()
            assert "totals" in body, kind
        # логист — отгрузки да, деньги нет
        assert c.get("/api/summary/shipments", headers=H(LOG)).status_code == 200
        assert c.get("/api/summary/money", headers=H(LOG)).status_code == 403
        # менеджер — финансовая аналитика закрыта
        assert c.get("/api/summary/money", headers=H(MGR)).status_code == 403
