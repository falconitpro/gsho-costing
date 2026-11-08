"""Каталог: исполнение на подтверждении — КД и ЗГД правят все поля (характеристики, опции, ТН ВЭД, логистика, регламенты) до подтверждения."""
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
MGR, CD, GD = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json() if r.content else None


def test_pending_edit():
    seed()
    t = uuid.uuid4().hex[:5].upper()
    with TestClient(app) as c:
        fac = next(f for f in _ok(c.get("/api/refs/factories", headers=H(MGR))) if f["approval_status"] == "approved")
        ex = _ok(c.post("/api/catalog/equipment", headers=H(MGR), json={"factory_id": fac["id"], "machine_type": "Погрузчик", "model": f"PE-{t}",
                                                                         "tech_params": [{"code": "1.1", "name_ru": "Грузоподъёмность", "unit": "т", "value_ru": "10"}]}), 201)
        assert ex["status"] == "pending"
        body = {"name": "Исполнение 1", "tech_params": [{"code": "1.1", "name_ru": "Грузоподъёмность", "name_zh": "载重", "unit": "т", "value_ru": "12", "required": True},
                                                       {"name_ru": "Ковш", "unit": "м³", "value_ru": "5"}, {"name_ru": "", "code": ""}],
                "local_options": [{"name": "Пожаротушение", "cost": 300000, "currency": "RUB", "install_days": 2}, {"name": ""}]}
        assert c.patch(f"/api/catalog/executions/{ex['id']}", headers=H(MGR), json=body).status_code == 403
        e2 = _ok(c.patch(f"/api/catalog/executions/{ex['id']}", headers=H(CD), json=body))
        assert [r["value_ru"] for r in e2["tech_params"]] == ["12", "5"] and [r["no"] for r in e2["tech_params"]] == [1, 2]
        assert len(e2["local_options"]) == 1 and e2["status"] == "pending" and e2["name"] == "Исполнение 1"
        m = _ok(c.patch(f"/api/catalog/models/{ex['model_id']}", headers=H(GD), json={
            "default_hs_code": "8429510000", "hs_info": {"description": "Погрузчики фронтальные", "duty_pct": 0, "vat_pct": 22},
            "logistics_params": {"14.1": {"name": "Масса", "value": 28000, "unit": "кг"}},
            "maintenance_regulations": [{"name": "ТО-1", "interval_hours": 250, "operations": [{"name": "замена масла", "hours": 2}]}, {"name": "", "interval_hours": 0}],
            "default_hours_per_month": 400, "comment": "проверено перед подтверждением"}))
        assert m["status"] == "pending"                                    # правка до подтверждения не подтверждает модель
        card = _ok(c.get(f"/api/catalog/models/{ex['model_id']}/card", headers=H(CD)))
        assert card["hs"]["code"] == "8429510000" and card["hs"]["vat_pct"] == 22 and len(card["maintenance_regulations"]) == 1
        assert card["logistics_params"]["14.1"]["value"] == 28000 and card["default_hours_per_month"] == 400
        _ok(c.post(f"/api/catalog/executions/{ex['id']}/decision", headers=H(CD), json={"approve": True}))
        card = _ok(c.get(f"/api/catalog/models/{ex['model_id']}/card", headers=H(MGR)))
        assert card["status"] == "approved" and card["executions"][0]["tech_params"][0]["value_ru"] == "12"
