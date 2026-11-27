"""D-181 (K-0): роль «Инженер», группы доступа K/I и реестр событий изменений."""
import pytest
from sqlalchemy import text

from tests.test_api_flow import client, _ok, H, MGR  # noqa: F401

ENG = "engineer@example.com"
GD = "evgeniy@dobrov.pro"
KI = ("client_contacts", "client_sites", "client_fleet", "service_orders", "service_refs")


def test_matrix_matches_owner_table():
    from app.core.roles import DEFAULT_MATRIX, Role, effective_perms
    want = {  # таблица, согласованная владельцем 26.11.2026 (контакты, площадки, парк, сервис, справочники)
        "manager": (2, 2, 2, 1, 1), "logistician": (1, 1, 1, 0, 0), "service_head": (2, 2, 2, 2, 2), "cd": (2, 2, 2, 1, 1),
        "fd": (1, 1, 1, 1, 0), "gd": (2, 2, 2, 2, 2), "admin": (0, 1, 2, 0, 0), "observer": (0, 1, 0, 0, 0),
        "purchasing": (1, 1, 1, 0, 0), "lawyer": (1, 1, 0, 0, 0), "clerk": (1, 0, 0, 0, 0), "engineer": (1, 1, 1, 1, 1),
        "payroll": (0, 0, 0, 0, 0),   # D-205: бухгалтер по зарплате — только «Персонал»
    }
    assert set(want) == {r.value for r in Role}
    for code, levels in want.items():
        got = tuple(DEFAULT_MATRIX[Role(code)].get(g, 0) for g in KI)
        assert got == levels, code
    eng = effective_perms([Role.ENGINEER])
    assert {g for g, v in eng.items() if v} == set(KI) | {"assistant_use"}


def test_existing_roles_keep_their_old_rights():
    """Новые группы ничего не отнимают и не добавляют в прежних группах."""
    from app.core.roles import DEFAULT_MATRIX, Role
    old_engineerless = {r for r in Role if r not in (Role.ENGINEER, Role.PAYROLL)}   # новые роли (D-181, D-205) — без файлов сделок
    for r in old_engineerless:
        assert DEFAULT_MATRIX[r].get("file_download") == 1
        assert DEFAULT_MATRIX[r].get("assistant_use") == 1


def test_engineer_sees_no_deals_prices_or_costs(client):  # noqa: F811
    me = _ok(client.get("/api/auth/me", headers=H(ENG)))
    assert [r["code"] for r in me["roles"]] == ["engineer"]
    perms = me.get("perms") or {}
    for g in ("cost", "price", "finance_analytics", "finance_section", "client_price_history", "factory_price_history", "payment_terms", "file_download"):
        assert not perms.get(g), g
    cl = _ok(client.get("/api/refs/clients", headers=H(MGR)))[0]
    did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "Карьер"}), 201)["id"]
    assert _ok(client.get("/api/deals", headers=H(ENG))) == []
    r = client.get(f"/api/deals/{did}", headers=H(ENG))
    assert r.status_code in (403, 404)
    for path in ("/api/finance/obligations", "/api/summary/overview", "/api/changes"):
        assert client.get(path, headers=H(ENG)).status_code in (403, 404), path
    assert _ok(client.get("/api/legal/contracts", headers=H(ENG)))["items"] == []   # реестр отдаётся пустым: договоров инженер не видит


def test_change_events_written_by_audit_and_append_only(client):  # noqa: F811
    from app.db import SessionLocal, engine
    from app.services.change_events import settled
    from app.services.deal_service import audit
    with SessionLocal() as db:
        _, start = settled(db, after=0, limit=2000)
        while True:
            evs, nxt = settled(db, after=start, limit=2000)
            if nxt == start:
                break
            start = nxt
        audit(db, None, None, "probe", "p-1", "update", before={"secret": 1}, after={"secret": 2})
        db.commit()
        evs, cur = settled(db, after=start)
        mine = [e for e in evs if e.entity == "probe"]
        assert len(mine) == 1 and mine[0].entity_id == "p-1" and mine[0].action == "update" and cur >= mine[0].seq
        seq = mine[0].seq
    # в событии нет значений полей
    with engine.connect() as c:
        cols = {r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='change_events'"))}
    assert not cols & {"before", "after", "payload"}
    for sql in (f"UPDATE change_events SET action='x' WHERE seq={seq}", f"DELETE FROM change_events WHERE seq={seq}", "TRUNCATE change_events"):
        with engine.connect() as c:
            with pytest.raises(Exception, match="только добавление"):
                c.execute(text(sql))
            c.rollback()


def test_change_events_stop_before_open_transaction(client):  # noqa: F811
    """Событие незавершённой транзакции и всё после него не отдаются, курсор не перескакивает."""
    from app.db import SessionLocal
    from app.services.change_events import record, settled
    with SessionLocal() as db:
        _, start = settled(db, after=0, limit=2000)
        while True:
            _, nxt = settled(db, after=start, limit=2000)
            if nxt == start:
                break
            start = nxt
    slow = SessionLocal()
    try:
        record(slow, "probe_slow", "s-1", "create")
        slow.flush()                      # seq выдан, транзакция открыта
        with SessionLocal() as fast:
            record(fast, "probe_fast", "f-1", "create")
            fast.commit()
        with SessionLocal() as reader:
            evs, cur = settled(reader, after=start)
            assert not [e for e in evs if e.entity in ("probe_slow", "probe_fast")]
            assert cur == start
        slow.commit()
        with SessionLocal() as reader:
            evs, cur = settled(reader, after=start)
            assert [e.entity for e in evs if e.entity.startswith("probe_")] == ["probe_slow", "probe_fast"]
    finally:
        slow.close()


def test_changes_api_for_gd(client):  # noqa: F811
    body = _ok(client.get("/api/changes?after=0&limit=5", headers=H(GD)))
    assert set(body) == {"cursor", "events"} and len(body["events"]) <= 5
    assert client.get("/api/changes", headers=H(MGR)).status_code == 403


def test_engineer_parts_catalog_without_prices(client):  # noqa: F811
    import uuid
    art = f"ENG-{uuid.uuid4().hex[:6]}"
    p = _ok(client.post("/api/parts/catalog", headers=H("cd@example.com"), json={"article": art, "name_ru": "Фильтр", "sale_price": 1234.5, "sale_currency": "RUB"}), 201)
    mgr = _ok(client.get(f"/api/parts/catalog?q={art}", headers=H(MGR)))["items"][0]
    assert mgr["sale_price"] == 1234.5                      # прочим ролям — как раньше
    eng = _ok(client.get(f"/api/parts/catalog?q={art}", headers=H(ENG)))["items"][0]
    assert eng["article"] == art and "sale_price" not in eng and "has_client_prices" not in eng
    card = _ok(client.get(f"/api/parts/catalog/{p['id']}", headers=H(ENG)))
    assert not {"sale_price", "client_prices", "min_price", "purchase_price"} & set(card)
