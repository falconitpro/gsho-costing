"""Права на вкладки, которые роли видят в интерфейсе (решение владельца 20.11.2026): видимая вкладка не должна получать 403.

- «Снабжение → Партии»: кандидаты в партию видят ФД и администратор; создавать партии они по-прежнему не могут.
- Сделка → «Сервис»: исходные данные расчёта видят КД и ФД; считать и завершать — по-прежнему сервис и ЗГД.
- «Юр. блок → Правила и ИИ»: администратор видит настройки; менять — по-прежнему только ЗГД.
"""
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
MGR, CD, FD, ADMIN, LAW, OBS = "manager@example.com", "cd@example.com", "fd@example.com", "admin@example.com", "lawyer@example.com", "observer@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def test_batch_candidates_read_for_fd_and_admin(client):
    for who in (FD, ADMIN):
        assert client.get("/api/parts/batches/candidates", headers=H(who)).status_code == 200, who
        # создание партии — прежние роли
        assert client.post("/api/parts/batches", headers=H(who), json={"po_line_ids": []}).status_code == 403, who
    assert client.get("/api/parts/batches/candidates", headers=H(LAW)).status_code == 403


def test_service_defaults_read_for_cd_and_fd(client):
    cl = client.get("/api/refs/clients", headers=H(MGR)).json()[0]
    did = client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "сервис"}).json()["id"]
    fac = client.get("/api/refs/factories", headers=H(MGR)).json()[0]
    pos = client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "ZL50", "machine_type": "Погрузчик", "qty": 1, "unit_price": 0})
    assert pos.status_code in (200, 201), pos.text
    pid = pos.json()["id"]
    for who in (CD, FD):
        assert client.get(f"/api/deals/{did}/service/positions/{pid}/defaults", headers=H(who)).status_code == 200, who
        # расчёт и завершение — по-прежнему сервис и ЗГД
        assert client.post(f"/api/deals/{did}/service/complete", headers=H(who)).status_code == 403, who
    assert client.get(f"/api/deals/{did}/service/positions/{pid}/defaults", headers=H(MGR)).status_code == 403


def test_legal_ai_read_for_admin(client):
    r = client.get("/api/legal/ai", headers=H(ADMIN))
    assert r.status_code == 200 and r.json()["can_edit"] is False
    assert client.put("/api/legal/ai", headers=H(ADMIN), json={"provider": "kimi"}).status_code == 403
    assert client.get("/api/legal/ai", headers=H(OBS)).status_code in (200, 403)   # наблюдатель — как было (по правам legal_docs)
