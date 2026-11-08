"""Единый «Каталог» (решение владельца 08.11.2026): общий поиск по оборудованию, запчастям и технике других производителей,
применимые запчасти и комплекты ТО в карточке модели, справочник техники других производителей."""
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
MGR, SRV, LOG = "manager@example.com", "service@example.com", "logist@example.com"


def _ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:800])
    return r.json()


def test_catalog_hub():
    seed()
    with TestClient(app) as c:
        t = uuid.uuid4().hex[:6]
        tree = _ok(c.get("/api/catalog/tree", headers=H(MGR)))
        model = tree[0]["types"][0]["models"][0]
        # запчасть, применимая к модели ГШО, с кросс-номером
        art = f"HUB-{t}"
        p = _ok(c.post("/api/parts/catalog", headers=H(MGR), json={"article": art, "name_ru": "Фильтр масляный каталог", "manufacturer": "Donaldson", "part_group": "Фильтры",
                                                                   "purchase_price": 10, "purchase_currency": "EUR", "cross_refs": [{"kind": "supplier", "number": f"X-{t}"}],
                                                                   "applicability": [{"equipment_model_id": model["id"], "assembly": "Двигатель"}]}), 201)
        # общий поиск: модель, артикул, кросс-номер, короткий запрос — пусто
        assert _ok(c.get(f"/api/catalog/search?q={model['model'][:4]}", headers=H(MGR)))["models"]
        assert _ok(c.get(f"/api/catalog/search?q={art}", headers=H(MGR)))["parts"][0]["id"] == p["id"]
        assert _ok(c.get(f"/api/catalog/search?q=X-{t}", headers=H(MGR)))["parts"][0]["id"] == p["id"]
        assert _ok(c.get("/api/catalog/search?q=a", headers=H(MGR))) == {"models": [], "parts": [], "other": []}
        # карточка модели: применимые запчасти и комплекты ТО
        bm = _ok(c.get(f"/api/parts/catalog/by-model/{model['id']}", headers=H(LOG)))
        assert any(x["id"] == p["id"] and x["assembly"] == "Двигатель" for x in bm["parts"])
        k = _ok(c.post("/api/parts/fleet/kits", headers=H(SRV), json={"equipment_model_id": model["id"], "name": f"ТО-250 {t}", "interval_hours": 250,
                                                                      "lines": [{"article": art, "qty": 2}]}), 201)
        bm = _ok(c.get(f"/api/parts/catalog/by-model/{model['id']}", headers=H(LOG)))
        assert any(x["id"] == k["id"] for x in bm["kits"])
        assert next(x for x in _ok(c.get("/api/parts/fleet/kits", headers=H(MGR))) if x["id"] == k["id"])["model_title"]
        # техника других производителей: справочник, дубли, правка, поиск
        assert c.post("/api/parts/fleet/other-models", headers=H(LOG), json={"manufacturer": "Epiroc", "model": f"ST14-{t}"}).status_code == 403
        om = _ok(c.post("/api/parts/fleet/other-models", headers=H(MGR), json={"manufacturer": "Epiroc", "model": f"ST14-{t}", "machine_type": "ПДМ"}), 201)
        assert c.post("/api/parts/fleet/other-models", headers=H(MGR), json={"manufacturer": "epiroc", "model": f"st14-{t}"}).status_code == 409
        om = _ok(c.put(f"/api/parts/fleet/other-models/{om['id']}", headers=H(MGR), json={"manufacturer": "Epiroc", "model": f"ST14-{t}", "machine_type": "ПДМ 14 т"}))
        assert om["machine_type"] == "ПДМ 14 т"
        lst = _ok(c.get(f"/api/parts/fleet/other-models?q=ST14-{t}", headers=H(MGR)))
        assert len(lst) == 1 and lst[0]["machines"] == 0 and lst[0]["kits"] == 0
        assert _ok(c.get(f"/api/catalog/search?q=ST14-{t}", headers=H(MGR)))["other"][0]["id"] == om["id"]
