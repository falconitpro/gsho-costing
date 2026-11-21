"""D-145: суммы в JSON-полях — без «хвостов» двоичной дроби при записи."""
from app.core.money_json import tidy

from tests.test_api_flow import client, _ok, H, MGR  # noqa: F401


def test_tidy_rounds_only_floats():
    src = {"amount": 1234.5600000000002, "items": [0.1 + 0.2, 5, "7.30000000000001", True, None], "rate": 12.818212345678, "z": -1e-9}
    assert tidy(src) == {"amount": 1234.56, "items": [0.3, 5, "7.30000000000001", True, None], "rate": 12.818212, "z": 0.0}


def test_hook_on_write(client):
    from app.db import SessionLocal
    from app.models import Position
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Хвосты"}), 201)["id"]
    fac = _ok(client.get("/api/refs/factories", headers=H(MGR)))[0]
    _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": fac["id"], "model": "TAIL-1", "machine_type": "Погрузчик", "qty": 1, "unit_price": 100}), 201)
    with SessionLocal() as db:
        p = db.query(Position).filter(Position.deal_id == did).first()
        assert p is not None, "у новой сделки есть позиция"
        p.zip_items = [{"name": "фильтр", "qty": 3, "price": 0.1 + 0.2, "sum": 3 * (0.1 + 0.2)}]
        db.commit()
        db.refresh(p)
        assert p.zip_items[0]["price"] == 0.3 and p.zip_items[0]["sum"] == 0.9 and p.zip_items[0]["qty"] == 3
