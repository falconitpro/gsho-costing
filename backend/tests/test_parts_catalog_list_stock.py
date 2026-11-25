"""D-170: сводка по складам в списке каталога запчастей — резерв, доступно, в пути, «ниже минимума» и фильтр.

Только чтение: прежние поля списка не меняются. Запускается, когда задан DATABASE_URL.
"""
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
from app.models import PartStock, Warehouse  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR = "manager@example.com"


@pytest.fixture(scope="module")
def client():
    seed()
    with TestClient(app) as c:
        yield c


def test_list_stock_summary_and_below_min_filter(client):
    tag = uuid.uuid4().hex[:6]
    mk = lambda art: client.post("/api/parts/catalog", headers=H(MGR), json={  # noqa: E731
        "article": art, "name_ru": f"Фильтр {tag}", "manufacturer": "Sandvik", "oem_number": "", "part_group": "Фильтры",
        "length_mm": 10, "width_mm": 10, "height_mm": 10}).json()
    low, ok, empty = mk(f"L-{tag}"), mk(f"O-{tag}"), mk(f"E-{tag}")
    with SessionLocal() as db:
        w1, w2 = Warehouse(name=f"Склад A {tag}"), Warehouse(name=f"Склад B {tag}", kind="virtual")
        db.add_all([w1, w2]); db.flush()
        db.add_all([
            PartStock(part_id=low["id"], warehouse_id=w1.id, qty=10, reserved=7, in_transit=4, min_qty=5),   # свободно 3 < 5
            PartStock(part_id=low["id"], warehouse_id=w2.id, qty=2, reserved=0, in_transit=0, min_qty=1),    # норма
            PartStock(part_id=ok["id"], warehouse_id=w1.id, qty=8, reserved=1, in_transit=0, min_qty=5),     # свободно 7 ≥ 5
        ])
        db.commit()
    items = {x["id"]: x for x in client.get(f"/api/parts/catalog?q={tag}", headers=H(MGR)).json()["items"]}
    a = items[low["id"]]
    assert (a["stock_total"], a["stock_reserved"], a["stock_available"], a["stock_in_transit"], a["below_min_count"]) == (12, 7, 5, 4, 1)
    assert items[ok["id"]]["below_min_count"] == 0 and items[ok["id"]]["stock_available"] == 7
    assert items[empty["id"]]["stock_total"] == 0 and items[empty["id"]]["below_min_count"] == 0
    # прежние поля на месте
    assert {"article", "name_ru", "status", "stock_total", "sale_price", "has_client_prices"} <= set(a)
    got = client.get(f"/api/parts/catalog?q={tag}&below_min=true", headers=H(MGR)).json()
    assert got["total"] == 1 and got["items"][0]["id"] == low["id"]
