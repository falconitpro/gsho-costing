"""D-167: план/факт по месяцам и сравнение с прошлым периодом — суммы сходятся с расшифровкой, валюты не смешиваются,
будущий факт — «нет данных», а не ноль; права — как у обзора «Финансов»."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal

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
FD, CD, MGR = "fd@example.com", "cd@example.com", "manager@example.com"
D = lambda v: Decimal(v or "0")   # noqa: E731


def _scenario(c: TestClient) -> dict:
    """Договор RUB 1 000 000 (аванс 20 % до 10.11, 10 % до 12.11) и поступление 150 000 от 12.11, временно разнесённое 120 000 на первый аванс."""
    from tests.test_finance_api import RUN, _own_bank, _signed_client_contract
    k = _signed_client_contract(c, 1_000_000, [{"share_pct": 20, "event": "contract", "offset_days": 5, "offset_kind": "working"},
                                               {"share_pct": 10, "event": "contract", "offset_days": 10, "offset_kind": "calendar"},
                                               {"share_pct": 70, "event": "act_signed", "offset_days": 10, "offset_kind": "calendar"}])
    reg = c.get(f"/api/finance/obligations?contract_id={k['id']}&as_of=2026-11-10", headers=H(FD)).json()["items"]
    a1 = next(x for x in reg if x["amount"] == "200000.00")
    ent_id = c.get("/api/legal/contracts", headers=H("lawyer@example.com")).json()["entities"][0]["id"]
    acct = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": _own_bank(c, ent_id, "RUB"), "title": "PF RUB"}).json()
    key = lambda x: {**H(FD), "Idempotency-Key": f"pf-{x}-{RUN}"}   # noqa: E731
    t = c.post("/api/finance/transactions", headers=key("tx"), json={"account_id": acct["id"], "direction": "in", "amount": "150000.00", "currency": "RUB",
                                                                     "booked_at": "2026-11-12T10:00:00+03:00", "counterparty_name": "АО Рудник", "external_ref": f"PF-{RUN}"}).json()
    c.post(f"/api/finance/transactions/{t['id']}/post", headers=key("post"))
    r = c.post(f"/api/finance/transactions/{t['id']}/allocations", headers=key("al"), json={"items": [{"obligation_id": a1["id"], "amount_tx": "120000.00"}]})
    assert r.status_code == 201, r.text
    return {"contract": k, "a1": a1, "tx": t}


def test_planfact_scenario_month_values():
    seed()
    with TestClient(app) as c:
        sc = _scenario(c)
        d = c.get("/api/finance/planfact?as_of=2026-11-30&currency=RUB&direction=in", headers=H(FD)).json()
        nov = next(r for r in d["series"][0]["rows"] if r["period"] == "2026-11")
        plan = c.get("/api/finance/planfact/items?period=2026-11&currency=RUB&direction=in&kind=plan&as_of=2026-11-30", headers=H(FD)).json()["items"]
        mine = [i for i in plan if i["obligation_id"] in {x["id"] for x in c.get(f"/api/finance/obligations?contract_id={sc['contract']['id']}", headers=H(FD)).json()["items"]}]
        assert sorted(D(i["amount"]) for i in mine) == [Decimal("100000.00"), Decimal("200000.00")]   # 70 % без даты — не в плане месяца
        fact = c.get("/api/finance/planfact/items?period=2026-11&currency=RUB&direction=in&kind=fact&as_of=2026-11-30", headers=H(FD)).json()["items"]
        f1 = next(i for i in fact if i["transaction_id"] == sc["tx"]["id"])
        assert f1["amount"] == "120000.00" and f1["provisional"] is True and f1["date"] == "2026-11-12"
        assert D(nov["fact_provisional"]) >= Decimal("120000.00")
        # на 11.11 оплаты ещё нет — в факте её нет; сравнение за 1–11 ноября против 1–11 октября
        early = c.get("/api/finance/planfact/items?period=2026-11&currency=RUB&direction=in&kind=fact&as_of=2026-11-11", headers=H(FD)).json()["items"]
        assert all(i["transaction_id"] != sc["tx"]["id"] for i in early)
        w = c.get("/api/finance/planfact?as_of=2026-11-11", headers=H(FD)).json()["compare_window"]
        assert w == {"from": "2026-11-01", "to": "2026-11-11", "prev_from": "2026-10-01", "prev_to": "2026-10-11"}


def test_planfact_matches_items_and_keeps_currencies_apart():
    seed()
    with TestClient(app) as c:
        _scenario(c)
        A = "as_of=2026-11-30"       # дата расчёта фиксирована: тест не «протухает» со временем
        r = c.get(f"/api/finance/planfact?{A}", headers=H(FD))
        assert r.status_code == 200, r.text
        d = r.json()
        assert {"plan", "fact", "deviation", "compare"} <= set(d["definitions"])
        assert len(d["periods"]) == 8 and [p["state"] for p in d["periods"]].count("current") == 1
        cur = next(p for p in d["periods"] if p["state"] == "current")["key"]
        assert d["series"], "в демо-данных есть обязательства"
        seen = set()
        for s in d["series"]:
            assert (s["currency"], s["direction"]) not in seen
            seen.add((s["currency"], s["direction"]))
            for row, p in zip(s["rows"], d["periods"]):
                if p["state"] == "future":
                    assert row["fact"] is None and row["deviation"] is None     # факта ещё нет — не ноль
                    continue
                assert D(row["deviation"]) == D(row["fact"]) + D(row["fact_provisional"]) - D(row["plan"])
                if row["plan_count"]:
                    it = c.get(f"/api/finance/planfact/items?period={p['key']}&currency={s['currency']}&direction={s['direction']}&kind=plan&{A}", headers=H(FD)).json()
                    assert {i["currency"] for i in it["items"]} == {s["currency"]}
                    assert sum(D(i["amount"]) for i in it["items"] if not i["forecast"]) == D(row["plan"])
                    assert sum(D(i["amount"]) for i in it["items"] if i["forecast"]) == D(row["forecast"])
                if row["fact_count"]:
                    it = c.get(f"/api/finance/planfact/items?period={p['key']}&currency={s['currency']}&direction={s['direction']}&kind=fact&{A}", headers=H(FD)).json()
                    assert D(it["total"]) == D(row["fact"]) + D(row["fact_provisional"])
                    assert sum(D(i["amount"]) for i in it["items"] if i["provisional"]) == D(row["fact_provisional"])
            # сравнение: текущий отрезок = факт текущего месяца по дату расчёта
            cr = next(x for x in s["rows"] if x["period"] == cur)
            assert D(s["compare"]["current"]) == D(cr["fact"]) + D(cr["fact_provisional"])
            assert D(s["compare"]["delta"]) == D(s["compare"]["current"]) - D(s["compare"]["previous"])
            if D(s["compare"]["previous"]) == 0:
                assert s["compare"]["delta_pct"] is None
        # фильтр по валюте и направлению
        one = c.get(f"/api/finance/planfact?currency=rub&direction=in&{A}", headers=H(FD)).json()
        assert all(s["currency"] == "RUB" and s["direction"] == "in" for s in one["series"])
        # права: как у обзора «Финансов»
        assert c.get("/api/finance/planfact", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/planfact", headers=H(CD)).status_code == 403
        assert c.get("/api/finance/planfact/items?period=2026-11&currency=RUB&direction=in&kind=plan", headers=H(MGR)).status_code == 403
        assert c.get("/api/finance/planfact/items?period=11-2026&currency=RUB&direction=in&kind=plan", headers=H(FD)).status_code == 422


def test_tx_date_value_date_then_moscow_booking():
    from types import SimpleNamespace
    from app.services.finance.planfact import tx_date
    assert tx_date(SimpleNamespace(value_date=date(2026, 10, 31), booked_at=datetime(2026, 11, 2, tzinfo=timezone.utc))) == date(2026, 10, 31)
    # 22:30 UTC 31.10 = 01:30 МСК 01.11 — операция ноября
    assert tx_date(SimpleNamespace(value_date=None, booked_at=datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc))) == date(2026, 11, 1)
