"""D-211: сверка переноса контактов и приёмка владельцем данных."""
import uuid

from tests.test_api_flow import client, _ok, H  # noqa: F401
from scripts.seed import TEST_LOGIN as GD


def test_contacts_migration_review_and_accept(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Client
    from scripts import contacts_migrate
    t = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        c = Client(name=f"Сверка {t}", inn=str(uuid.uuid4().int)[:10], contacts=[{"name": f"Иванов {t}", "email": f"iv{t}@example.com", "phone": "+7 900 111-22-33"},
                                                 {"name": f"Петров {t}", "email": "petrov-at-mail", "phone": ""},
                                                 {"name": f"Без канала {t}"}])
        db.add(c)
        db.commit()
        cid = c.id
    assert contacts_migrate.run(yes=True, only={cid}) == 0
    assert client.get("/api/contacts-migration", headers=H("logist@example.com")).status_code == 403
    r = _ok(client.get(f"/api/contacts-migration?sample=50&seed={t}", headers=H(GD)))
    card = next(s for s in r["sources"] if s["source"] == "client_card")
    assert card["created"] >= 3
    assert any(x["client_id"] == cid and x["name"] == f"Петров {t}" for x in r["bad_email"])
    assert any(x["client_id"] == cid and x["name"] == f"Без канала {t}" for x in r["no_channel"])
    smp = next((s for s in r["sample"] if s["client_id"] == cid), None)
    if smp:
        assert {x["outcome"] for x in smp["legacy"]} == {"created"} and len(smp["directory"]) == 3
    # строка прежнего списка изменилась после переноса — в «не перенесено», и как поступил бы скрипт
    with SessionLocal() as db:
        c = db.get(Client, cid)
        c.contacts = [*c.contacts, {"name": "", "email": f"x{t}@example.com"}]
        db.commit()
    r = _ok(client.get("/api/contacts-migration", headers=H(GD)))
    assert any(p["client_id"] == cid and p["outcome"] == "ambiguous" for p in r["pending"]) and r["pending_ambiguous"] >= 1
    assert client.post("/api/contacts-migration/accept", headers=H(GD), json={"comment": "проверено"}).status_code == 409
    with SessionLocal() as db:
        c = db.get(Client, cid)
        c.contacts = c.contacts[:3]
        db.commit()
    r = _ok(client.get("/api/contacts-migration", headers=H(GD)))
    if r["pending_ambiguous"] == 0:   # другие тесты могли оставить свои строки для ручного разбора
        a = _ok(client.post("/api/contacts-migration/accept", headers=H(GD), json={"comment": "выборка 10 клиентов сверена", "sample_seed": t, "checked_clients": 10}))
        assert a["by_name"] and a["counts"]["directory"] >= 3 and a["checked_clients"] == 10
        assert _ok(client.get("/api/contacts-migration", headers=H(GD)))["accepted"]["comment"].startswith("выборка")
