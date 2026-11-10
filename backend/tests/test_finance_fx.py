"""«Финансы», этап 4: двухногая конвертация валют и комиссия (D-025, D-131)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

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
from tests.test_finance_liquidity import _entity_with_accounts, _ob  # noqa: E402

H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
CD, FD = "cd@example.com", "fd@example.com"
NOW = lambda: datetime.now(timezone.utc).isoformat()   # noqa: E731


def _bal(c, aid):
    return next(a for a in c.get("/api/finance/accounts", headers=H(FD)).json()["items"] if a["id"] == aid)["balance"]


def test_fx_conversion_two_legs_fee_reverse_and_linking():
    seed()
    run = uuid.uuid4().hex[:10]
    with TestClient(app) as c:
        eid, acc = _entity_with_accounts(c, run, ("RUB", "CNY"))
        rub, cny = acc["RUB"]["id"], acc["CNY"]["id"]
        for a, amt in ((rub, "1000000.00"), (cny, "0.00")):
            c.post(f"/api/finance/accounts/{a}/balance-snapshots", headers=H(FD, f"s-{a}"), json={"as_of": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "amount": amt})
        body = {"sell_account_id": rub, "buy_account_id": cny, "sell_amount": "128182.00", "buy_amount": "10000.00", "rate": "12.8182", "rate_source": "банк, курс сделки",
                "executed_at": NOW(), "external_ref": f"FX-{run}", "fee_amount": "500.00"}
        assert c.post("/api/finance/fx-operations", headers=H(CD, "x"), json=body).status_code == 403
        assert c.post("/api/finance/fx-operations", headers=H(FD), json=body).status_code == 400
        assert c.post("/api/finance/fx-operations", headers=H(FD, "bad1"), json={**body, "buy_amount": "11000.00"}).status_code == 422   # не согласуется с курсом
        assert c.post("/api/finance/fx-operations", headers=H(FD, "bad2"), json={**body, "buy_account_id": rub}).status_code == 422     # та же валюта
        r = c.post("/api/finance/fx-operations", headers=H(FD, f"fx-{run}"), json=body)
        assert r.status_code == 201, r.text
        op = r.json()
        assert op["sell"]["currency"] == "RUB" and op["buy"]["currency"] == "CNY" and op["fee"] == "500.00" and op["status"] == "posted"
        assert c.post("/api/finance/fx-operations", headers=H(FD, f"fx-{run}"), json=body).json()["id"] == op["id"]               # идемпотентно
        assert _bal(c, rub) == "871318.00" and _bal(c, cny) == "10000.00"                                                         # −128 182 −500; +10 000
        # ноги — проведены, «не разнесены» не считаются и на обязательства не разносятся
        leg = c.get(f"/api/finance/transactions/{op['buy']['transaction_id']}", headers=H(FD)).json()
        assert leg["status"] == "posted" and leg["origin_type"] == "fx" and leg["fx_leg"] is True and leg["unallocated"] == "0.00"
        ob = _ob(eid, "in", "10000", "CNY", None, run, "cny-in")
        assert c.post(f"/api/finance/transactions/{leg['id']}/allocations", headers=H(FD, f"a-{run}"), json={"items": [{"obligation_id": ob, "amount_tx": "10000.00"}]}).status_code == 409
        liq = c.get(f"/api/finance/liquidity?legal_entity_id={eid}&group_by=day&horizon_days=30", headers=H(FD)).json()
        assert next(x for x in liq["currencies"] if x["currency"] == "CNY")["unallocated"]["count"] == 0
        # сторно конвертации: обе ноги и комиссия сторнированы, остатки вернулись
        assert c.post(f"/api/finance/fx-operations/{op['id']}/reverse", headers=H(FD, f"r-{run}"), json={"reason": ""}).status_code == 422
        rv = c.post(f"/api/finance/fx-operations/{op['id']}/reverse", headers=H(FD, f"r-{run}"), json={"reason": "неверный курс"}).json()
        assert rv["status"] == "reversed" and _bal(c, rub) == "1000000.00" and _bal(c, cny) == "0.00"

        # связать уже проведённые операции (например, из выписки): нога — не более чем в одной конвертации
        def posted(acc_id, direction, amount, k):
            t = c.post("/api/finance/transactions", headers=H(FD, f"{k}-{run}"), json={"account_id": acc_id, "direction": direction, "amount": amount, "currency": "RUB" if acc_id == rub else "CNY",
                                                                                     "booked_at": NOW()}).json()
            c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"p{k}-{run}"))
            return t["id"]
        s_id, b_id = posted(rub, "out", "64091.00", "s"), posted(cny, "in", "5000.00", "b")
        link = {**body, "sell_amount": "64091.00", "buy_amount": "5000.00", "fee_amount": None, "sell_transaction_id": s_id, "buy_transaction_id": b_id}
        assert c.post("/api/finance/fx-operations", headers=H(FD, f"l0-{run}"), json={**link, "sell_transaction_id": b_id}).status_code == 422   # нога не того направления
        r = c.post("/api/finance/fx-operations", headers=H(FD, f"l1-{run}"), json=link)
        assert r.status_code == 201, r.text
        assert r.json()["sell"]["transaction_id"] == s_id and _bal(c, cny) == "5000.00"
        assert c.post("/api/finance/fx-operations", headers=H(FD, f"l2-{run}"), json=link).status_code == 409                    # повторное использование ноги
        # операция, уже разнесённая на обязательство, ногой стать не может
        b2 = posted(cny, "in", "100.00", "b2")
        ob2 = _ob(eid, "in", "100", "CNY", None, run, "cny-in2")
        c.post(f"/api/finance/transactions/{b2}/allocations", headers=H(FD, f"a2-{run}"), json={"items": [{"obligation_id": ob2, "amount_tx": "100.00"}]})
        s2 = posted(rub, "out", "1281.82", "s2")
        assert c.post("/api/finance/fx-operations", headers=H(FD, f"l3-{run}"), json={**link, "sell_amount": "1281.82", "buy_amount": "100.00", "sell_transaction_id": s2,
                                                                                    "buy_transaction_id": b2}).status_code == 409
        lst = c.get(f"/api/finance/fx-operations?account_id={cny}", headers=H(FD)).json()["items"]
        assert {o["status"] for o in lst} == {"posted", "reversed"} and len(lst) == 2
        assert c.get("/api/finance/fx-operations", headers=H(CD)).status_code == 403
