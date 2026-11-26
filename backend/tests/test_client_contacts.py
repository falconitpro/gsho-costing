"""D-184 (K-1): контакты клиентов — права по группе client_contacts, проверки, журнал просмотров, удаление по запросу,
отметка о согласии, перенос из карточек клиентов."""
import uuid

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401

GD, ENG, OBS, CLERK, ADM = "evgeniy@dobrov.pro", "engineer@example.com", "observer@example.com", "office@example.com", "admin@example.com"


def _client(c):
    return _ok(c.get("/api/refs/clients", headers=H(MGR)))[0]


def _new(c, cid, who=MGR, **kw):
    import random
    t = uuid.uuid4().hex[:6]
    n = f"{random.randint(0, 9999999):07d}"
    body = {"client_id": cid, "full_name": f"Иванов Пётр {t}", "position": "Главный механик", "work_phone": f"+7 (913) {n[:3]}-{n[3:5]}-{n[5:]}",
            "work_email": f"p.{t}@rudnik.example", "roles": ["technical", "service"], **kw}
    return c.post("/api/clients/contacts", headers=H(who), json=body)


def test_crud_primary_and_validation(client):  # noqa: F811
    cl = _client(client)
    a = _ok(_new(client, cl["id"], is_primary=True), 201)
    assert a["roles_titles"] == ["Технический специалист", "Сервис и ремонт"] and a["is_primary"] is True
    b = _ok(_new(client, cl["id"], is_primary=True), 201)
    items = _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(MGR)))["items"]
    prim = [x["id"] for x in items if x["is_primary"]]
    assert prim == [b["id"]] and items[0]["id"] == b["id"]                       # основной — один и первым
    assert _new(client, cl["id"], work_email=a["work_email"].upper()).status_code == 409   # та же почта
    assert _new(client, cl["id"], work_phone="8-913-" + a["work_phone"][-9:]).status_code == 409    # тот же номер, записан иначе
    assert _new(client, cl["id"], work_email="not-an-email").status_code == 422
    assert _new(client, cl["id"], work_phone="звоните мне").status_code == 422
    assert _new(client, cl["id"], roles=["boss"]).status_code == 422
    assert _new(client, cl["id"], full_name="   ").status_code == 422
    # перенос к другому клиенту — нельзя; в архив — можно
    r = client.put(f"/api/clients/contacts/{a['id']}", headers=H(MGR), json={**{k: a[k] for k in ("full_name", "work_email", "work_phone")}, "client_id": "other"})
    assert r.status_code == 409
    _ok(client.put(f"/api/clients/contacts/{a['id']}", headers=H(MGR), json={"client_id": cl["id"], "full_name": a["full_name"], "is_active": False}))
    assert a["id"] not in [x["id"] for x in _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(MGR)))["items"]]


def test_site_link(client):  # noqa: F811
    cl = _client(client)
    site = _ok(client.post("/api/clients/sites", headers=H(MGR), json={"client_id": cl["id"], "name": f"Карьер {uuid.uuid4().hex[:5]}"}), 201)
    c = _ok(_new(client, cl["id"], site_id=site["id"]), 201)
    assert c["site_name"] == site["name"]
    assert _new(client, cl["id"], site_id="no-such").status_code == 422


def test_rights_follow_matrix(client):  # noqa: F811
    """Доступ = уровень группы client_contacts у сотрудника (другие тесты могут менять роли демо-сотрудников, поэтому
    ожидание берётся из его прав), плюс стартовая матрица для ролей по таблице владельца."""
    from app.core.roles import DEFAULT_MATRIX, Role
    assert [DEFAULT_MATRIX[Role(r)].get("client_contacts", 0) for r in ("manager", "cd", "gd", "service_head")] == [2, 2, 2, 2]
    assert [DEFAULT_MATRIX[Role(r)].get("client_contacts", 0) for r in ("logistician", "fd", "lawyer", "clerk", "purchasing")] == [1] * 5
    assert [DEFAULT_MATRIX[Role(r)].get("client_contacts", 0) for r in ("observer", "admin")] == [0, 0]
    cl = _client(client)
    for who in (MGR, CD, GD, "service@example.com", LOG, "fd@example.com", "lawyer@example.com", CLERK, OBS, ADM, ENG):
        me = _ok(client.get("/api/auth/me", headers=H(who)))
        lvl = (me.get("perms") or {}).get("client_contacts", 0)
        only_eng = [r["code"] for r in me["roles"]] == ["engineer"]
        r = client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(who))
        assert r.status_code == (200 if lvl >= 1 and not only_eng else 403), who
        assert _new(client, cl["id"], who=who).status_code == (201 if lvl >= 2 and not only_eng else 403), who
    assert client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(ENG)).status_code == 403   # инженер — до нарядов


def test_views_journal_and_no_pd_in_audit(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import AuditLog, ClientContactView
    cl = _client(client)
    c = _ok(_new(client, cl["id"]), 201)
    with SessionLocal() as db:
        n0 = db.query(ClientContactView).count()
    _ok(client.get(f"/api/clients/contacts?client_id={cl['id']}", headers=H(LOG)))
    _ok(client.get(f"/api/clients/contacts/{c['id']}", headers=H(MGR)))
    with SessionLocal() as db:
        new = db.query(ClientContactView).order_by(ClientContactView.id.desc()).limit(2).all()
        assert db.query(ClientContactView).count() == n0 + 2
        assert {v.kind for v in new} == {"list", "card"}
        rows = db.query(AuditLog).filter(AuditLog.entity == "client_contact", AuditLog.entity_id == c["id"]).all()
        blob = repr([(r.before, r.after, r.comment) for r in rows])
        assert c["work_email"] not in blob and c["work_phone"] not in blob and c["full_name"] not in blob
    j = _ok(client.get(f"/api/clients/contacts/views?client_id={cl['id']}", headers=H(GD)))
    assert j and {"at", "user", "kind"} <= set(j[0])
    assert client.get("/api/clients/contacts/views", headers=H(MGR)).status_code == 403


def test_delete_on_request_removes_everywhere(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import AuditLog, Client, ClientContact
    cl = _client(client)
    c = _ok(_new(client, cl["id"]), 201)
    with SessionLocal() as db:
        k = db.get(Client, cl["id"])
        k.contacts = [*(k.contacts or []), {"name": c["full_name"], "email": c["work_email"]}, {"name": "Сохранить Меня", "email": "keep@x.example"}]
        db.commit()
    assert client.post(f"/api/clients/contacts/{c['id']}/delete", headers=H(MGR), json={"reason": ""}).status_code == 422
    _ok(client.post(f"/api/clients/contacts/{c['id']}/delete", headers=H(MGR), json={"reason": "запрос субъекта от 26.11"}))
    with SessionLocal() as db:
        assert db.get(ClientContact, c["id"]) is None
        legacy = db.get(Client, cl["id"]).contacts
        assert not [x for x in legacy if x.get("email") == c["work_email"]] and [x for x in legacy if x.get("email") == "keep@x.example"]
        log = db.query(AuditLog).filter(AuditLog.entity_id == c["id"], AuditLog.action == "delete").one()
        assert log.comment == "запрос субъекта от 26.11" and c["work_email"] not in repr((log.before, log.after))


def test_consent_required_switch(client):  # noqa: F811
    from app.db import SessionLocal
    from app.services import settings_service
    cl = _client(client)
    with SessionLocal() as db:
        settings_service.set_value(db, "contacts_consent_required", True, None, "тест")
        db.commit()
    try:
        assert _ok(client.get("/api/clients/contacts/meta", headers=H(MGR)))["consent_required"] is True
        assert _new(client, cl["id"]).status_code == 422
        assert _new(client, cl["id"], consent_on="2026-11-26", consent_source="письмо клиента").status_code == 201
    finally:
        with SessionLocal() as db:
            settings_service.set_value(db, "contacts_consent_required", False, None, "тест")
            db.commit()
    assert _new(client, cl["id"]).status_code == 201


def test_contacts_import_dry_run_apply_idempotent(client, capsys):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Client, ClientContact
    from scripts import contacts_import
    t = uuid.uuid4().hex[:6]
    cl = _client(client)
    with SessionLocal() as db:
        k = db.get(Client, cl["id"])
        k.contacts = [*(k.contacts or []), {"name": f"Сидоров {t}", "email": f"S.{t}@x.example", "phone": "+7 900 111-22-33"},
                      {"name": f"Сидоров {t}", "email": f"s.{t}@x.example"}, {"name": f"Петрова {t}", "position": "Бухгалтер"}]
        db.commit()
        before = db.query(ClientContact).count()
    assert contacts_import.run(yes=False) == 0
    out = capsys.readouterr().out
    assert t not in out and "@" not in out                    # в вывод не попадают персональные данные
    with SessionLocal() as db:
        assert db.query(ClientContact).count() == before
    assert contacts_import.run(yes=True) == 0
    with SessionLocal() as db:
        mine = db.query(ClientContact).filter(ClientContact.full_name.contains(t)).all()
        assert sorted(c.full_name for c in mine) == [f"Петрова {t}", f"Сидоров {t}"]
        assert [c.work_email for c in mine if c.full_name.startswith("Сидоров")] == [f"s.{t}@x.example"]
        n = db.query(ClientContact).count()
    assert contacts_import.run(yes=True) == 0
    with SessionLocal() as db:
        assert db.query(ClientContact).count() == n
