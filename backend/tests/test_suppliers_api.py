"""Раздел «Поставщики»: ГШО / запасные части — фильтр, обязательная отметка, автоматическая отметка «запчасти» при использовании."""
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
CD = "cd@example.com"


def test_supplies():
    seed()
    t = uuid.uuid4().hex[:5].upper()
    with TestClient(app) as c:
        assert c.post("/api/refs/factories", headers=H(CD), json={"name": f"SUP0 {t}", "supplies": []}).status_code == 422
        p = c.post("/api/refs/factories", headers=H(CD), json={"name": f"SUPP {t}", "country": "DE", "supplies": ["parts"]}).json()
        g = c.post("/api/refs/factories", headers=H(CD), json={"name": f"SUPG {t}"}).json()
        assert g["supplies"] == ["gsho"]
        ids = lambda kind: {f["id"] for f in c.get(f"/api/refs/factories?supplies={kind}", headers=H(CD)).json()}   # noqa: E731
        assert p["id"] in ids("parts") and p["id"] not in ids("gsho") and g["id"] in ids("gsho") and g["id"] not in ids("parts")
        # карточка запчасти с этим поставщиком — он становится и поставщиком запчастей
        c.post("/api/parts/catalog", headers=H(CD), json={"article": f"SUP-{t}", "name_ru": "Деталь", "supplier_id": g["id"]})
        assert g["id"] in ids("parts") and g["id"] in ids("gsho")
