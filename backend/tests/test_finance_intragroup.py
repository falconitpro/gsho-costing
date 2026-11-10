"""Внутригрупповые расчёты (D-162, D-163): отчёт «кто кому должен» и сводная ликвидность группы без внутригрупповых."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Contract, Factory, FinanceObligation, LegalEntity  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, FD = "manager@example.com", "fd@example.com"
TODAY = date.today()


def _ob(eid, direction, amount, on, run, key, cp_kind=None, cp_id=None, deal_id=None, due=None):
    with SessionLocal() as db:
        o = FinanceObligation(source_type="contract", source_id=f"ig-{run}", source_line_key=key, direction=direction, amount=Decimal(amount), currency="KGS",
                              legal_due_on=due or on, forecast_on=on, legal_entity_id=eid, title=f"внутригруппа {key}", nature="contractual", is_active=True,
                              sync_status="ok", counterparty_kind=cp_kind or "other", counterparty_id=cp_id, counterparty_name_snapshot=key, deal_id=deal_id)
        db.add(o)
        db.commit()
        return o.id


def _kgs_out(liq) -> Decimal:
    cur = next((x for x in liq["currencies"] if x["currency"] == "KGS"), None)
    return sum((Decimal(p["outflow"]) for p in cur["points"]), Decimal(0)) if cur else Decimal(0)


def test_intragroup_report_and_consolidated_liquidity():
    seed()
    run = uuid.uuid4().hex[:8]
    with TestClient(app) as c:
        with SessionLocal() as db:
            ru = LegalEntity(code=f"ru{run}", name=f"Берг-Хаус {run}", country="RU", currency="RUB")
            cn = LegalEntity(code=f"cn{run}", name=f"Альянс-Импекс {run}", country="CN", currency="CNY")
            db.add_all([ru, cn])
            db.flush()
            ai = Factory(name=f"Альянс-Импекс фабрика {run}", country="CN", supplier_status="group_company", legal_entity_id=cn.id)
            x = Factory(name=f"Внешний завод {run}", country="CN")
            db.add_all([ai, x])
            db.commit()
            ru_id, cn_id, ai_id, x_id = ru.id, cn.id, ai.id, x.id
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        deal = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"внутригруппа-{run}"}).json()
        _ob(ru_id, "out", "1000", TODAY + timedelta(days=5), run, "ru-ai-1", "factory", ai_id, deal["id"])
        _ob(ru_id, "out", "500", TODAY + timedelta(days=12), run, "ru-ai-2", "factory", ai_id, deal["id"], due=TODAY - timedelta(days=3))
        _ob(cn_id, "out", "1300", TODAY + timedelta(days=8), run, "cn-x", "factory", x_id, deal["id"])        # внешний — не внутригрупповой
        _ob(cn_id, "in", "1000", TODAY + timedelta(days=5), run, "cn-ru", "other", ru_id)                    # тот же долг в книгах получателя

        assert c.get("/api/finance/intragroup", headers=H(MGR)).status_code == 403
        rep = c.get("/api/finance/intragroup?currency=KGS", headers=H(FD)).json()
        pairs = {(p["payer_id"], p["receiver_id"]): p for p in rep["pairs"]}
        p = pairs[(ru_id, cn_id)]
        assert p["payer"] == f"Берг-Хаус {run}" and p["receiver"] == f"Альянс-Импекс {run}" and p["count"] == 2 and p["books"] == "payer"
        assert Decimal(p["amount"]) == 1500 and Decimal(p["remaining"]) == 1500 and Decimal(p["overdue"]) == 500
        assert [r["title"] for r in p["other_side_rows"]] == ["внутригруппа cn-ru"]                          # книги получателя — для сверки, не в итогах
        assert (cn_id, ru_id) not in pairs and all(x_id != (r.get("deal") or {}).get("id") for r in p["rows"])
        assert not any(k[1] == x_id for k in pairs)
        bal = next(b for b in rep["balances"] if {b["debtor_id"], b["creditor_id"]} == {ru_id, cn_id})
        assert bal["debtor_id"] == ru_id and Decimal(bal["net"]) == 1500
        assert any(deal["number"] in w for w in rep["warnings"])                                              # нет подписанного договора Альянс-Импекс ↔ X
        with SessionLocal() as db:
            db.add(Contract(doc_type="contract", kind="supplier", direction="expense", number=f"П-CN-{run}", status="active", deal_id=deal["id"], deal_ids=[deal["id"]],
                            own_entity_id=cn_id, cp_kind="factory", cp_id=x_id, mirror_kind="via_group", currency="KGS"))
            db.commit()
        assert not any(deal["number"] in w for w in c.get("/api/finance/intragroup?currency=KGS", headers=H(FD)).json()["warnings"])

        # сводная ликвидность: без внутригрупповых исчезают платежи Берг-Хауса Альянс-Импекс, внешняя закупка остаётся
        base = "/api/finance/liquidity?group_by=day&horizon_days=30&currency=KGS"
        full = c.get(base, headers=H(FD)).json()
        net = c.get(base + "&exclude_intragroup=true", headers=H(FD)).json()
        with SessionLocal() as db:                                     # все внутригрупповые расходы KGS в горизонте (в т. ч. от прошлых прогонов теста)
            from app.services.finance.intragroup import group_map, is_intragroup
            gm = group_map(db)
            expect = sum((o.amount for o in db.query(FinanceObligation).filter(FinanceObligation.currency == "KGS", FinanceObligation.direction == "out",
                                                                              FinanceObligation.is_active.is_(True)).all()
                          if is_intragroup(gm, o) and o.forecast_on and o.forecast_on <= TODAY + timedelta(days=30)), Decimal(0))
        assert expect >= 1500 and _kgs_out(full) - _kgs_out(net) == expect
        assert net["intragroup_excluded"]["KGS"]["count"] >= 3          # два расхода Берг-Хауса и поступление Альянс-Импекс and full["intragroup_excluded"] is None
        own = c.get(f"/api/finance/liquidity?group_by=day&horizon_days=30&currency=KGS&legal_entity_id={ru_id}&exclude_intragroup=true", headers=H(FD)).json()
        assert own["intragroup_excluded"] is None and _kgs_out(own) == 1500                                   # по одной компании — не исключаются
        on = (TODAY + timedelta(days=5)).isoformat()
        ex_full = c.get(f"/api/finance/liquidity/explanation?currency=KGS&on={on}&group_by=day&horizon_days=30", headers=H(FD)).json()
        ex_net = c.get(f"/api/finance/liquidity/explanation?currency=KGS&on={on}&group_by=day&horizon_days=30&exclude_intragroup=true", headers=H(FD)).json()
        assert any(i["title"] == "внутригруппа ru-ai-1" for i in ex_full["items"]) and not any(i["title"] == "внутригруппа ru-ai-1" for i in ex_net["items"])
