"""D-189: единый справочник контактов, этап 3 — юр. профиль клиента без собственного списка контактов, телефон и почта
организации для договоров, доверенности со статусом и ссылкой на контакт, разовый перенос."""
import uuid
from datetime import date, timedelta

from tests.test_api_flow import client, _ok, H, MGR  # noqa: F401

GD, LAW = "evgeniy@dobrov.pro", "lawyer@example.com"


def _inn() -> str:
    import random
    d = [random.randint(0, 9) for _ in range(9)]
    d[0] = d[0] or 7
    return "".join(map(str, d)) + str(sum(c * x for c, x in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10)


def _new_client(c, **kw):
    return _ok(c.post("/api/refs/clients", headers=H(MGR), json={"name": f"ООО «Юр-{uuid.uuid4().hex[:5]}»", "inn": _inn(), **kw}), 201)


def _profile(c, cid):
    return _ok(c.get(f"/api/legal/profiles?owner_kind=client&owner_id={cid}", headers=H(GD)))["items"][0]


def test_new_client_contacts_go_to_registry_not_card(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import AuditLog, Client
    t = uuid.uuid4().hex[:6]
    cl = _new_client(client, contacts=[{"name": f"Иванов {t}", "email": f"i.{t}@x.example", "phone": "+7 900 123-45-67"}])
    items = _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(MGR)))["items"]
    assert [x["full_name"] for x in items] == [f"Иванов {t}"]
    with SessionLocal() as db:
        assert db.get(Client, cl["id"]).contacts == []
        log = db.query(AuditLog).filter(AuditLog.entity == "client", AuditLog.entity_id == cl["id"], AuditLog.action == "create").one()
        assert f"i.{t}@x.example" not in repr(log.after)                       # персональные данные не в журнале


def test_profile_contacts_readonly_and_org_phone_email_in_contract(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import LegalProfile
    from app.services.legal_packages import party_from_profile
    from app.services.legal_profiles import requisites_view
    cl = _new_client(client)
    p = _profile(client, cl["id"])
    assert p["contacts_readonly"] is True and p["contacts"] == []
    r = client.put(f"/api/legal/profiles/{p['id']}", headers=H(GD), json={"contacts": [{"name": "Новый", "email": "n@x.example"}]})
    assert r.status_code == 422
    _ok(client.put(f"/api/legal/profiles/{p['id']}", headers=H(GD), json={"contacts": [], "org_phone": "+7 495 000-00-00", "org_email": "office@x.example"}))
    with SessionLocal() as db:
        lp = db.get(LegalProfile, p["id"])
        assert requisites_view(lp)["phone"] == "+7 495 000-00-00" and requisites_view(lp)["email"] == "office@x.example"
        assert party_from_profile(lp, None)["email"] == "office@x.example"
        # профиль без org_* (как до 058 на стенде) — по-прежнему первый контакт, договоры не меняются
        lp.org_phone, lp.org_email = "", ""
        lp.contacts = [{"name": "Старый", "phone": "+7 1", "email": "old@x.example"}]
        assert requisites_view(lp)["email"] == "old@x.example" and party_from_profile(lp, None)["phone"] == "+7 1"
        db.rollback()


def test_poa_status_and_contact_link(client):  # noqa: F811
    cl = _new_client(client)
    p = _profile(client, cl["id"])
    rep = _ok(client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": cl["id"], "full_name": "Представитель", "work_email": f"r.{uuid.uuid4().hex[:5]}@x.example", "roles": ["poa"]}), 201)
    other = _new_client(client)
    stranger = _ok(client.post("/api/clients/contacts", headers=H(MGR), json={"client_id": other["id"], "full_name": "Чужой", "work_email": f"s.{uuid.uuid4().hex[:5]}@x.example"}), 201)
    old = (date.today() - timedelta(days=1)).isoformat()
    new = (date.today() + timedelta(days=30)).isoformat()
    poa = [{"person": "Представитель", "number": "1", "issued_on": "2026-01-01", "valid_until": old, "scope": "подписание", "contact_id": rep["id"]},
           {"person": "Представитель", "number": "2", "issued_on": "2026-11-01", "valid_until": new, "scope": "подписание", "contact_id": rep["id"]},
           {"person": "Без срока", "number": "3", "issued_on": "", "valid_until": "", "scope": ""}]
    out = _ok(client.put(f"/api/legal/profiles/{p['id']}", headers=H(GD), json={"powers_of_attorney": poa}))
    assert [d["status"] for d in out["powers_of_attorney"]] == ["expired", "active", "no_date"]
    assert out["powers_of_attorney"][0]["contact_id"] == rep["id"]                 # истёкшая не удалена
    bad = [{**poa[1], "contact_id": stranger["id"]}]
    assert client.put(f"/api/legal/profiles/{p['id']}", headers=H(GD), json={"powers_of_attorney": bad}).status_code == 422
    legal = _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}&role=legal_any", headers=H(LAW)))["items"]
    assert [x["id"] for x in legal] == [rep["id"]]


def test_contacts_migrate_rules_idempotent_and_no_pd(client, capsys):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Client, ClientContactLink, ContactMigrationMap, LegalProfile
    from scripts import contacts_migrate as cm
    t = uuid.uuid4().hex[:6]
    cl = _new_client(client)
    _profile(client, cl["id"])            # профиль создаётся при первом обращении
    with SessionLocal() as db:
        c = db.get(Client, cl["id"])
        c.contacts = [{"name": f"Петров {t}", "email": f"p.{t}@x.example"}, {"name": f"Сидоров {t}"}]
        lp = db.query(LegalProfile).filter(LegalProfile.owner_kind == "client", LegalProfile.owner_id == cl["id"]).first()
        lp.contacts = [{"name": f"Петров {t}", "email": f"P.{t}@x.example"}, {"name": f"Сидоров {t}", "phone": "+7 900 555-11-22"}]
        db.commit()
    assert cm.run(yes=False, only={cl["id"]}) == 0
    out = capsys.readouterr().out
    assert t not in out and "@" not in out
    with SessionLocal() as db:
        p = cm.plan(db, {cl["id"]})
        mine = [x for x in p["items"] if x["client_id"] == cl["id"]]
        assert sorted(x["outcome"] for x in mine) == ["ambiguous", "created", "created", "matched"]   # Сидоров с телефоном ≠ Сидоров без — только ФИО
    # неоднозначные есть — перенос останавливается и ничего не меняет
    assert cm.run(yes=True, only={cl["id"]}) == 1
    with SessionLocal() as db:
        assert db.query(ContactMigrationMap).filter(ContactMigrationMap.source_key.contains(cl["id"])).count() == 0
        db.get(Client, cl["id"]).contacts = [{"name": f"Петров {t}", "email": f"p.{t}@x.example"}]   # разобрали вручную: убрали дубль
        db.commit()
    assert cm.run(yes=True, only={cl["id"]}) == 0
    items = _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(MGR)))["items"]
    names = sorted(x["full_name"] for x in items)
    assert names == [f"Петров {t}", f"Сидоров {t}"]
    petrov = next(x for x in items if x["full_name"] == f"Петров {t}")
    assert "contracts" in petrov["roles"]                                     # из юр. профиля — роль «Договоры и документы»
    with SessionLocal() as db:
        n_links = db.query(ClientContactLink).filter(ClientContactLink.client_id == cl["id"]).count()
        assert db.get(Client, cl["id"]).contacts and db.query(LegalProfile).filter(LegalProfile.owner_id == cl["id"]).first().contacts   # источники не тронуты
    assert cm.run(yes=True, only={cl["id"]}) == 0                                              # повтор — без дублей
    with SessionLocal() as db:
        assert db.query(ClientContactLink).filter(ClientContactLink.client_id == cl["id"]).count() == n_links
