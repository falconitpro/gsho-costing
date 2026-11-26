"""D-182 (K-2): площадки клиентов — права по группе client_sites, проверки, связь с парком, перенос из карточек."""
import uuid

from tests.test_api_flow import client, _ok, H, MGR, LOG, CD  # noqa: F401

GD, ENG, OBS, CLERK, ADM = "evgeniy@dobrov.pro", "engineer@example.com", "observer@example.com", "office@example.com", "admin@example.com"


def _inn() -> str:
    import random
    d = [random.randint(0, 9) for _ in range(9)]
    d[0] = d[0] or 7
    return "".join(map(str, d)) + str(sum(c * x for c, x in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10)


def _clients(c, n=2):
    cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    while len(cls) < n:
        _ok(c.post("/api/refs/clients", headers=H(MGR), json={"name": f"ООО «Площадки-{uuid.uuid4().hex[:4]}»", "inn": _inn()}), 201)
        cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    return cls[:n]


def _site(c, client_id, name, who=MGR, **kw):
    return c.post("/api/clients/sites", headers=H(who), json={"client_id": client_id, "name": name, **kw})


def test_crud_map_link_and_machine(client):  # noqa: F811
    cl = _clients(client, 1)[0]
    tag = uuid.uuid4().hex[:6]
    site = _ok(_site(client, cl["id"], f"Карьер {tag}", site_type="quarry", address="Кемерово", latitude=55.35, longitude=86.08,
                     access_mode="permit", hse_notes="Каска, жилет"), 201)
    assert site["client_name"] == cl["name"] and site["site_type_title"] == "Карьер" and site["access_mode_title"] == "По наряду-допуску"
    assert site["map_url"] == "https://yandex.ru/maps/?pt=86.08,55.35&z=14&l=map" and site["machines_count"] == 0
    assert "contacts" not in site                                     # персональные данные — не в площадке (K-1)
    lst = _ok(client.get(f"/api/clients/sites?client_id={cl['id']}&q={tag}", headers=H(MGR)))
    assert [x["id"] for x in lst["items"]] == [site["id"]] and lst["total"] == 1

    m = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={
        "client_id": cl["id"], "site_id": site["id"], "origin": "third_party", "warranty_until": "2027-06-30",
        "other_manufacturer": "CAT", "other_model": f"R1700-{tag}", "serial": f"SN-{tag}"}), 201)
    assert m["site_id"] == site["id"] and m["site_name"] == site["name"] and m["site"] == site["name"]
    assert m["origin"] == "third_party" and m["origin_title"] == "Чужая поставка" and m["warranty_until"] == "2027-06-30"
    assert _ok(client.get(f"/api/clients/sites/{site['id']}", headers=H(MGR)))["machines_count"] == 1
    assert [x["id"] for x in _ok(client.get(f"/api/parts/fleet/machines?site_id={site['id']}", headers=H(MGR)))] == [m["id"]]

    # прежний вид присылает только текст: тот же текст — связь сохраняется, другой — снимается
    body = {"client_id": cl["id"], "serial": f"SN-{tag}", "site": site["name"]}
    assert _ok(client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json=body))["site_id"] == site["id"]
    m2 = _ok(client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json={**body, "site": "Другой участок"}))
    assert m2["site_id"] is None and m2["site_name"] == "Другой участок"


def test_validation_duplicates_and_client_move(client):  # noqa: F811
    a, b = _clients(client, 2)
    tag = uuid.uuid4().hex[:6]
    assert _site(client, a["id"], f"Участок {tag}", latitude=55.0).status_code == 422          # координаты — парой
    assert _site(client, a["id"], f"Участок {tag}", site_type="moon").status_code == 422      # тип — из списка
    assert _site(client, a["id"], f"Участок {tag}", access_mode="any").status_code == 422
    assert _site(client, a["id"], "   ").status_code == 422
    assert _site(client, "no-such", f"Участок {tag}").status_code == 422
    s = _ok(_site(client, a["id"], f"Участок {tag}"), 201)
    assert _site(client, a["id"], f"  участок {tag.upper()} ").status_code == 409             # дубль у того же клиента
    assert _site(client, b["id"], f"Участок {tag}").status_code == 201                          # у другого — можно

    m = _ok(client.post("/api/parts/fleet/machines", headers=H(MGR), json={
        "client_id": a["id"], "site_id": s["id"], "other_manufacturer": "Komatsu", "other_model": f"HD785-{tag}"}), 201)
    # площадку чужого клиента машине назначить нельзя
    other = _ok(_site(client, b["id"], f"Чужая {tag}"), 201)
    r = client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json={"client_id": a["id"], "site_id": other["id"]})
    assert r.status_code == 422
    # площадку с машинами нельзя перенести к другому клиенту
    r = client.put(f"/api/clients/sites/{s['id']}", headers=H(MGR), json={"client_id": b["id"], "name": s["name"]})
    assert r.status_code == 409 and "машин: 1" in r.json()["detail"]
    # в архив — можно; архивную площадку назначить нельзя
    _ok(client.put(f"/api/clients/sites/{s['id']}", headers=H(MGR), json={"client_id": a["id"], "name": s["name"], "is_active": False}))
    assert s["id"] not in [x["id"] for x in _ok(client.get(f"/api/clients/sites?client_id={a['id']}", headers=H(MGR)))["items"]]
    r = client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json={"client_id": a["id"], "site_id": s["id"]})
    assert r.status_code == 422


def test_rights_follow_access_matrix(client):  # noqa: F811
    cl = _clients(client, 1)[0]
    tag = uuid.uuid4().hex[:6]
    for who in (MGR, CD, GD, "service@example.com"):                   # 2 — редактирует
        assert _site(client, cl["id"], f"{who} {tag}", who=who).status_code == 201, who
    for who in (LOG, "fd@example.com", OBS, ADM, "lawyer@example.com"):  # 1 — видит
        assert client.get("/api/clients/sites", headers=H(who)).status_code == 200, who
        assert _site(client, cl["id"], f"{who} {tag}", who=who).status_code == 403, who
    for who in (CLERK, ENG):                                            # 0, и инженер — до нарядов (I-1)
        assert client.get("/api/clients/sites", headers=H(who)).status_code == 403, who
    meta = _ok(client.get("/api/clients/sites/meta", headers=H(LOG)))
    assert meta["can_edit"] is False and "quarry" in meta["site_types"]


def test_changes_registry_records_sites(client):  # noqa: F811
    from app.db import SessionLocal
    from app.services.change_events import settled
    cl = _clients(client, 1)[0]
    with SessionLocal() as db:
        cur = 0
        while True:
            _, nxt = settled(db, after=cur, limit=2000)
            if nxt == cur:
                break
            cur = nxt
    s = _ok(_site(client, cl["id"], f"Событие {uuid.uuid4().hex[:6]}"), 201)
    with SessionLocal() as db:
        evs, _ = settled(db, after=cur, entity="client_site")
    assert [(e.entity_id, e.action) for e in evs] == [(s["id"], "create")]


def test_sites_import_dry_run_then_apply_idempotent(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import Client, ClientMachine, ClientSite
    from scripts import sites_import
    tag = uuid.uuid4().hex[:6]
    cl = _clients(client, 1)[0]
    with SessionLocal() as db:
        c = db.get(Client, cl["id"])
        c.sites = [*(c.sites or []), {"name": f"Разрез «Восточный-{tag}»", "address": "Хакасия"}, f"Склад {tag}"]
        db.add(ClientMachine(client_id=c.id, manufacturer="X", model=f"M-{tag}", site=f"разрез восточный-{tag}"))   # то же место, иначе записано
        db.add(ClientMachine(client_id=c.id, manufacturer="X", model=f"N-{tag}", site=f"Разрез Восточный-2 {tag}"))   # похожее — вручную
        db.commit()
        before = db.query(ClientSite).count()
        assert sites_import.run(yes=False) == 0
        assert db.query(ClientSite).count() == before                      # пробный прогон ничего не меняет
        p = sites_import.plan(db)
        mine = [x for x in p["create"] if tag in x["key"]]
        assert sorted(x["name"] for x in mine) == sorted([f"Разрез «Восточный-{tag}»", f"Склад {tag}", f"Разрез Восточный-2 {tag}"])
        assert any(tag in a and tag in b for _, a, b in p["review"])
    assert sites_import.run(yes=True) == 0
    with SessionLocal() as db:
        made = db.query(ClientSite).filter(ClientSite.client_id == cl["id"], ClientSite.name.contains(tag)).all()
        assert len(made) == 3
        m = db.query(ClientMachine).filter(ClientMachine.model == f"M-{tag}").one()
        site = db.get(ClientSite, m.site_id)
        assert site.name == f"Разрез «Восточный-{tag}»" and m.site == f"разрез восточный-{tag}"   # текст машины не тронут
        assert db.get(Client, cl["id"]).sites[-1] == f"Склад {tag}"                                  # карточка не тронута
        n = db.query(ClientSite).count()
    assert sites_import.run(yes=True) == 0
    with SessionLocal() as db:
        assert db.query(ClientSite).count() == n                            # повторный запуск — без дублей
