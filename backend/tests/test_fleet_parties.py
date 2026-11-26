"""D-186 (K-3): стороны по машине — собственник, лизингодатель, эксплуатант с датами; главная сторона; передача; права."""
import uuid
from datetime import date, timedelta

from tests.test_api_flow import client, _ok, H, MGR, LOG  # noqa: F401

GD, ENG, CLERK, ADM = "evgeniy@dobrov.pro", "engineer@example.com", "office@example.com", "admin@example.com"


def _inn() -> str:
    import random
    d = [random.randint(0, 9) for _ in range(9)]
    d[0] = d[0] or 7
    return "".join(map(str, d)) + str(sum(c * x for c, x in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], d)) % 11 % 10)


def _two_clients(c):
    cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    while len(cls) < 3:
        _ok(c.post("/api/refs/clients", headers=H(MGR), json={"name": f"ООО «Парк-{uuid.uuid4().hex[:4]}»", "inn": _inn()}), 201)
        cls = _ok(c.get("/api/refs/clients", headers=H(MGR)))
    return cls[0], cls[1], cls[2]


def _machine(c, client_id, **kw):
    t = uuid.uuid4().hex[:6]
    return _ok(c.post("/api/parts/fleet/machines", headers=H(MGR), json={"client_id": client_id, "other_manufacturer": "BelAZ", "other_model": f"75131-{t}",
                                                                          "serial": f"B-{t}", **kw}), 201)


def test_new_machine_has_primary_owner_and_parties_flow(client):  # noqa: F811
    a, b, l = _two_clients(client)
    m = _machine(client, a["id"])
    ps = m["parties"]
    assert len(ps) == 1 and ps[0]["client_id"] == a["id"] and ps[0]["role"] == "owner" and ps[0]["is_primary"] and ps[0]["active"]
    # лизингодатель и эксплуатант
    m = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/parties", headers=H(MGR), json={"client_id": l["id"], "role": "lessor", "valid_from": "2026-01-01", "valid_to": "2028-12-31"}), 201)
    m = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/parties", headers=H(MGR), json={"client_id": b["id"], "role": "operator", "valid_from": "2026-11-01"}), 201)
    assert {(p["client_id"], p["role"]) for p in m["parties"] if p["active"]} == {(a["id"], "owner"), (l["id"], "lessor"), (b["id"], "operator")}
    assert client.post(f"/api/parts/fleet/machines/{m['id']}/parties", headers=H(MGR), json={"client_id": b["id"], "role": "operator"}).status_code == 409
    assert client.post(f"/api/parts/fleet/machines/{m['id']}/parties", headers=H(MGR), json={"client_id": b["id"], "role": "boss"}).status_code == 422
    assert client.post(f"/api/parts/fleet/machines/{m['id']}/parties", headers=H(MGR),
                       json={"client_id": b["id"], "role": "other", "valid_from": "2026-05-01", "valid_to": "2026-04-01"}).status_code == 422
    # машина видна в парке каждого действующего участника, с его ролью
    for cl, role in ((a, "Собственник"), (b, "Эксплуатант"), (l, "Лизингодатель")):
        row = [x for x in _ok(client.get(f"/api/parts/fleet/machines?client_id={cl['id']}", headers=H(MGR))) if x["id"] == m["id"]]
        assert row and role in row[0]["client_roles"] + (["Собственник"] if row[0]["client_is_main"] else []), cl["name"]
    # эксплуатант становится главным: сервис и счета — на него; клиент машины меняется, площадка собственника снимается
    op = next(p for p in m["parties"] if p["role"] == "operator")
    site = _ok(client.post("/api/clients/sites", headers=H(MGR), json={"client_id": a["id"], "name": f"Карьер {uuid.uuid4().hex[:5]}"}), 201)
    _ok(client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json={"client_id": a["id"], "site_id": site["id"]}))
    m = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/parties/{op['id']}/primary", headers=H(MGR)))
    assert m["client_id"] == b["id"] and m["site_id"] is None
    assert [p["client_id"] for p in m["parties"] if p["is_primary"]] == [b["id"]]
    # главную нельзя завершить, другую — можно; завершённая остаётся в истории
    assert client.post(f"/api/parts/fleet/machines/{m['id']}/parties/{op['id']}/close", headers=H(MGR), json={}).status_code == 409
    les = next(p for p in m["parties"] if p["role"] == "lessor")
    m = _ok(client.post(f"/api/parts/fleet/machines/{m['id']}/parties/{les['id']}/close", headers=H(MGR), json={"on": date.today().isoformat(), "comment": "лизинг выкуплен"}))
    assert "лизинг выкуплен" in next(p for p in m["parties"] if p["id"] == les["id"])["comment"]
    m = _ok(client.put(f"/api/parts/fleet/machines/{m['id']}/parties/{les['id']}", headers=H(MGR),
                       json={"client_id": l["id"], "role": "lessor", "valid_from": "2026-01-01", "valid_to": (date.today() - timedelta(days=1)).isoformat(), "comment": "лизинг выкуплен"}))
    les2 = next(p for p in m["parties"] if p["id"] == les["id"])
    assert les2["active"] is False and "лизинг выкуплен" in les2["comment"]
    assert m["id"] not in [x["id"] for x in _ok(client.get(f"/api/parts/fleet/machines?client_id={l['id']}", headers=H(MGR)))]
    # клиента в записи не меняют
    assert client.put(f"/api/parts/fleet/machines/{m['id']}/parties/{op['id']}", headers=H(MGR), json={"client_id": a["id"], "role": "operator"}).status_code == 409


def test_client_change_in_old_form_is_transfer(client):  # noqa: F811
    a, b, _ = _two_clients(client)
    m = _machine(client, a["id"])
    m = _ok(client.put(f"/api/parts/fleet/machines/{m['id']}", headers=H(MGR), json={"client_id": b["id"], "serial": m["serial"], "site": ""}))
    ps = m["parties"]
    old = next(p for p in ps if p["client_id"] == a["id"])
    new = next(p for p in ps if p["client_id"] == b["id"])
    assert old["active"] is False and not old["is_primary"] and new["is_primary"] and new["valid_from"] == date.today().isoformat()
    assert m["client_id"] == b["id"]


def test_backfill_migration_and_lazy_primary(client):  # noqa: F811
    """Машина без сторон (как до 056) получает главного собственника при первом обращении; SQL 056 заполняет так же."""
    from pathlib import Path
    from app.db import SessionLocal, engine
    from app.models import ClientMachine, MachineParty
    a, _, _ = _two_clients(client)
    with SessionLocal() as db:
        m = ClientMachine(client_id=a["id"], manufacturer="X", model=f"OLD-{uuid.uuid4().hex[:5]}", commissioned_on=date(2024, 5, 1))
        db.add(m)
        db.commit()
        mid = m.id
        assert db.query(MachineParty).filter(MachineParty.machine_id == mid).count() == 0
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "056_machine_parties.sql").read_text()
    raw = engine.raw_connection()
    try:
        raw.driver_connection.execute(sql)
        raw.commit()
    finally:
        raw.close()
    with SessionLocal() as db:
        ps = db.query(MachineParty).filter(MachineParty.machine_id == mid).all()
        assert len(ps) == 1 and ps[0].is_primary and ps[0].role == "owner" and ps[0].valid_from == date(2024, 5, 1)
    # повторный прогон ничего не добавляет
    raw = engine.raw_connection()
    try:
        raw.driver_connection.execute(sql)
        raw.commit()
    finally:
        raw.close()
    with SessionLocal() as db:
        assert db.query(MachineParty).filter(MachineParty.machine_id == mid).count() == 1


def test_fleet_rights_by_group(client):  # noqa: F811
    from app.core.roles import DEFAULT_MATRIX, Role
    # стартовые уровни = прежний список ролей парка
    assert {r.value for r in Role if DEFAULT_MATRIX[r].get("client_fleet") == 2} == {"manager", "service_head", "cd", "gd", "admin"}
    assert {r.value for r in Role if DEFAULT_MATRIX[r].get("client_fleet") == 1} == {"fd", "logistician", "purchasing", "engineer"}
    a, _, _ = _two_clients(client)
    assert client.get("/api/parts/fleet/machines", headers=H(LOG)).status_code == 200
    assert client.post("/api/parts/fleet/machines", headers=H(LOG), json={"client_id": a["id"], "other_manufacturer": "X", "other_model": "Y"}).status_code == 403
    for who in (CLERK, ENG):
        assert client.get("/api/parts/fleet/machines", headers=H(who)).status_code == 403, who
    assert client.get("/api/parts/fleet/machines", headers=H(ADM)).status_code == 200
