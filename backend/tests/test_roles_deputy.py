"""Заместитель на период, сделки коллег, область наблюдателя, цены юристу с выигрыша, предупреждение «считал — не утверждает»."""
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

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from datetime import date, timedelta  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, ADM, OBS, LAW = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "admin@example.com", "observer@example.com", "lawyer@example.com"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text)
    return r.json()


def test_deputy_colleagues_observer_lawyer():
    seed()
    with TestClient(app) as c:
        users = _ok(c.get("/api/admin/users", headers=H(ADM)))
        me = next(u for u in users if u["email"] == MGR)
        m2 = next((u for u in users if u["email"] == "manager2@example.com"), None) or \
            _ok(c.post("/api/admin/users", headers=H(ADM), json={"email": "manager2@example.com", "display_name": "Второй Менеджер", "roles": ["manager"]}), 201)
        M2 = "manager2@example.com"
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d1 = _ok(c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"зам-{uuid.uuid4().hex[:4]}"}), 201)
        # коллега не видит чужую сделку целиком, но видит её в минимальном списке; при заведении по тому же клиенту — предупреждение
        assert c.get(f"/api/deals/{d1['id']}", headers=H(M2)).status_code in (403, 404)
        col = _ok(c.get("/api/deals/colleagues", headers=H(M2)))
        row = next(x for x in col if x["id"] == d1["id"])
        assert row["manager_name"] and "price" not in str(row) and set(row) <= {"id", "number", "client_id", "client_name", "site", "equipment", "status", "status_detail", "manager_name", "kp_due", "updated_at"}
        assert _ok(c.get("/api/deals/colleagues", headers=H(CD))) == []
        d2 = _ok(c.post("/api/deals", headers=H(M2), json={"client_id": cl["id"], "site": "дубль"}), 201)
        assert d2.get("warning") and "работает" in d2["warning"]
        # заместитель на период: видит сделки и задачи замещаемого, получает уведомления; вне периода — нет
        assert c.put(f"/api/team/{me['id']}/deputy", headers=H(M2), json={"deputy_id": m2["id"]}).status_code == 403
        assert c.put(f"/api/team/{me['id']}/deputy", headers=H(MGR), json={"deputy_id": me["id"]}).status_code == 422
        far = (date.today() + timedelta(days=10)).isoformat()
        _ok(c.put(f"/api/team/{me['id']}/deputy", headers=H(MGR), json={"deputy_id": m2["id"], "deputy_from": far}))
        assert c.get(f"/api/deals/{d1['id']}", headers=H(M2)).status_code in (403, 404)                      # период ещё не начался
        card = _ok(c.put(f"/api/team/{me['id']}/deputy", headers=H(CD), json={"deputy_id": m2["id"], "deputy_from": date.today().isoformat(), "deputy_to": far}))
        assert card["deputy_active"] is True
        assert _ok(c.get(f"/api/deals/{d1['id']}", headers=H(M2)))["id"] == d1["id"]
        assert any(x["id"] == d1["id"] for x in _ok(c.get("/api/deals", headers=H(M2))))
        _ok(c.patch(f"/api/deals/{d1['id']}", headers=H(M2), json={"site": "правка заместителя"}))           # и редактирует как владелец
        assert any(x["text"].startswith("Вы назначены заместителем") for x in _ok(c.get("/api/notifications", headers=H(M2)))["items"])
        _ok(c.put(f"/api/team/{me['id']}/deputy", headers=H(MGR), json={"deputy_id": None}))
        assert c.get(f"/api/deals/{d1['id']}", headers=H(M2)).status_code in (403, 404)
        # наблюдатель: пустая область — ничего; по клиенту — видит; с истёкшим сроком — нет
        obs = next(u for u in users if u["email"] == OBS)
        _ok(c.put(f"/api/team/{obs['id']}/observer-scope", headers=H(GD), json={}))
        assert _ok(c.get("/api/deals", headers=H(OBS))) == []
        _ok(c.put(f"/api/team/{obs['id']}/observer-scope", headers=H(GD), json={"client_ids": [cl["id"]], "phase": "sale"}))
        assert any(x["id"] == d1["id"] for x in _ok(c.get("/api/deals", headers=H(OBS))))
        _ok(c.put(f"/api/team/{obs['id']}/observer-scope", headers=H(GD), json={"client_ids": [cl["id"]], "until": (date.today() - timedelta(days=1)).isoformat()}))
        assert _ok(c.get("/api/deals", headers=H(OBS))) == []
        assert c.put(f"/api/team/{obs['id']}/observer-scope", headers=H(MGR), json={"all": True}).status_code == 403
        # юрист до выигрыша не видит цену и условия оплаты
        fac = next(f for f in c.get("/api/refs/factories", headers=H(MGR)).json() if f["approval_status"] == "approved")
        _ok(c.post(f"/api/deals/{d1['id']}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "L-1", "machine_type": "Самосвал", "unit_price": 100000}), 201)
        ld = _ok(c.get(f"/api/deals/{d1['id']}", headers=H(LAW)))
        assert "client_payments" not in ld and all("price_rub" not in v for v in ld["variants"])
