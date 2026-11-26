"""D-187: обследование контактов — только чтение, вывод без персональных данных."""
import uuid

from tests.test_api_flow import client, _ok, H, MGR  # noqa: F401


def test_inventory_counts_without_pd(client, capsys):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Client, ClientContact
    from scripts import contacts_inventory
    t = uuid.uuid4().hex[:6]
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    with SessionLocal() as db:
        c = db.get(Client, cl["id"])
        c.contacts = [*(c.contacts or []), {"name": f"Орлов {t}", "email": f"o.{t}@x.example"}, {"name": f"Орлов {t}"}, {"name": f"Лис {t}", "email": "bad"}]
        db.add(ClientContact(client_id=c.id, full_name=f"Орлов {t}", work_email=f"o.{t}@x.example"))
        db.commit()
        before = (db.query(ClientContact).count(), list(db.get(Client, cl["id"]).contacts))
        res = contacts_inventory.collect(db)
    assert res["прежний список: совпадает со справочником по почте"] >= 1
    assert res["прежний список: совпадает только по ФИО — вручную"] >= 1
    assert res["прежний список: почта с ошибкой"] >= 1
    assert contacts_inventory.run() == 0
    out = capsys.readouterr().out
    assert t not in out and "@" not in out
    with SessionLocal() as db:
        assert (db.query(ClientContact).count(), list(db.get(Client, cl["id"]).contacts)) == before   # ничего не изменено
