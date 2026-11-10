"""«Финансы», этап 5: признание выручки/расхода и экономика сделки (FIN-PNL-001…006, FIN-SEC-005; D-133…D-137)."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.workflow import DealStatus, TransitionResult  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal, DealVersion, Factory, FinanceRecognitionEvent, PlanFactEntry, Position, Variant  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email, key=None: {"X-Dev-User": email, **({"Idempotency-Key": key} if key else {})}   # noqa: E731
MGR, CD, GD, FD, LOG = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "logist@example.com"
RUN = datetime.now(timezone.utc).strftime("%H%M%S%f")
TODAY = date.today()


def _scen(price, items, gap):
    cost = sum(items.values())
    return {"price_rub": price, "cost_total_rub": cost, "profit_rub": price - cost, "margin_pct": round((price - cost) / price * 100, 3), "markup_pct": None,
            "items_rub": items, "delivery_days": 150, "cash_gap": {"amount_rub": gap, "starts_on": "2026-12-10", "days": 160}, "finance_breakdown": {"money_cost": items.get("finance", 0)},
            "sensitivity": {"margin_delta_pct_per_1pct_fx": -0.4, "margin_delta_rub_per_1pct_fx": -60000, "breakeven_fx_factor": 1.2, "breakeven_rate_base_ccy": 15.1}}


def _calc(var_id, pos_id):
    base = _scen(10_000_000, {"factory_price": 6_000_000, "intl_freight": 1_500_000, "insurance": 100_000, "finance": 200_000}, -1_900_000)
    base.update({"payment_dates": [{"no": 1, "event": "contract", "on": (TODAY - timedelta(days=8)).isoformat(), "amount_rub": 3_000_000, "via": "transfer"},
                                   {"no": 2, "event": "border", "on": (TODAY + timedelta(days=60)).isoformat(), "amount_rub": 7_000_000, "via": "transfer"}],
                 "positions": [{"position_id": pos_id, "factory_payment_dates": [
                     {"no": 1, "event": "factory_contract", "on": (TODAY - timedelta(days=12)).isoformat(), "amount": 1_900_000, "currency": "RUB", "amount_rub": 1_900_000},
                     {"no": 2, "event": "readiness", "on": (TODAY + timedelta(days=30)).isoformat(), "amount": 4_100_000, "currency": "RUB", "amount_rub": 4_100_000}]}]})
    pess = _scen(10_000_000, {"factory_price": 6_600_000, "intl_freight": 1_700_000, "insurance": 110_000, "finance": 260_000}, -2_400_000)
    opt = _scen(10_000_000, {"factory_price": 5_700_000, "intl_freight": 1_400_000, "insurance": 90_000, "finance": 180_000}, -1_600_000)
    return {"variants": {var_id: {"base": base, "pessimistic": pess, "optimistic": opt}}, "fx_components": []}


def _deal(c):
    cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
    d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"экономика-{RUN}"}).json()
    with SessionLocal() as db:
        deal = db.get(Deal, d["id"])
        fac = db.scalars(select(Factory)).first()
        pos = Position(deal_id=deal.id, factory_id=fac.id, model=f"ECO-{RUN}", machine_type="Самосвал", qty=1, unit_price=1, currency="RUB")
        db.add(pos)
        db.flush()
        var = Variant(deal_id=deal.id, name="Основной", position_ids=[pos.id], is_default=True)
        db.add(var)
        db.flush()
        v1 = DealVersion(deal_id=deal.id, version_no=1, reason="approval", calc_input={}, calc_result=_calc(var.id, pos.id))
        db.add(v1)
        db.flush()
        deal.approved_version_id, deal.won_variant_id = v1.id, var.id
        from app.services.deal_service import apply_transition
        apply_transition(db, deal, TransitionResult(status=DealStatus.CONTRACT, audit="тест: выиграна"), None)
        db.commit()
        return deal.id, var.id, pos.id


def _account(c):
    ent_id = c.get("/api/legal/contracts", headers=H("lawyer@example.com")).json()["entities"][0]["id"]
    own = c.get("/api/legal/own", headers=H(GD)).json()["items"]
    prof = next(e for e in own if e["id"] == ent_id)["profiles"][0]
    bank = c.post(f"/api/legal/profiles/{prof['id']}/banks", headers=H(GD), json={"title": f"Эконом RUB {RUN}", "currency": "RUB", "bank_name": "ПАО Банк",
                                                                                   "account": f"40702810{RUN[-12:]:0>12}", "bik": "044525225"})
    assert bank.status_code == 201, bank.text
    r = c.post("/api/finance/accounts", headers=H(FD), json={"legal_entity_id": ent_id, "bank_profile_id": bank.json()["id"], "title": f"Эконом {RUN}"})
    assert r.status_code == 201, r.text
    acct = r.json()
    at = datetime.combine(TODAY - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc)
    assert c.post(f"/api/finance/accounts/{acct['id']}/balance-snapshots", headers=H(FD, f"eco-snap-{RUN}"), json={"as_of": at.isoformat(), "amount": "50000000.00"}).status_code == 201
    return acct["id"]


def _pay(c, acct, direction, amount, days_ago, obligation_id, tag):
    at = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
    t = c.post("/api/finance/transactions", headers=H(FD, f"eco-{tag}-{RUN}"), json={"account_id": acct, "direction": direction, "amount": amount, "currency": "RUB",
                                                                                   "booked_at": at.isoformat(), "external_ref": f"ECO-{tag}-{RUN}"}).json()
    assert c.post(f"/api/finance/transactions/{t['id']}/post", headers=H(FD, f"eco-post-{tag}-{RUN}")).json()["status"] == "posted"
    r = c.post(f"/api/finance/transactions/{t['id']}/allocations", headers=H(FD, f"eco-al-{tag}-{RUN}"),
               json={"items": [{"obligation_id": obligation_id, "amount_tx": amount}], "status": "confirmed"})
    assert r.status_code == 201, r.text


def test_recognition_and_deal_economy():
    seed()
    with TestClient(app) as c:
        did, var_id, pos_id = _deal(c)
        eco = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        assert eco["scenarios"] == ["base", "pessimistic", "optimistic"] and len(eco["items"]) == 18
        assert eco["plan"]["base"]["revenue"] == 10_000_000 and eco["plan"]["optimistic"]["cash_gap"]["amount_rub"] == -1_600_000
        assert eco["pnl_fact"]["revenue"] is None and eco["cash_fact"]["status"] == "insufficient" and eco["cash_gap"]["fact"] is None
        codes = {g["code"] for g in eco["data_gaps"]}
        assert {"no_confirmed_cash", "no_revenue_recognition"} <= codes
        nc = next(g for g in eco["data_gaps"] if g["code"] == "no_confirmed_cash")
        assert any(r["amount"] == "3000000.00" for r in nc.get("rows", []))       # что разнести: просроченные строки
        assert eco["forecast"]["revenue"] == 10_000_000.0 and eco["forecast"]["revenue_source"] == "obligations"
        assert eco["cash_gap"]["forecast"]["amount_rub"] == "-3000000.00"                                    # +3 − 1,9 сегодня, −4,1 через 30 дней, +7 через 60

        # --- права (FIN-SEC-005): без «Себестоимости» экономики нет; КД видит отчёт сделки, но не список раздела
        assert c.get(f"/api/finance/deals/{did}/economy", headers=H(MGR)).status_code == 403
        assert c.get(f"/api/finance/deals/{did}/economy", headers=H(LOG)).status_code == 403
        assert c.get(f"/api/finance/deals/{did}/economy", headers=H(CD)).status_code == 200
        assert c.get("/api/finance/economy/deals", headers=H(CD)).status_code == 403
        assert any(d["id"] == did for d in c.get("/api/finance/economy/deals", headers=H(FD)).json()["deals"])
        assert c.get("/api/finance/deals/00000000-0000-0000-0000-000000000000/economy", headers=H(FD)).status_code == 404

        # --- legacy cost item: старая запись без события учитывается один раз, переносится один раз (FIN-PNL-004)
        with SessionLocal() as db:
            old = PlanFactEntry(deal_id=did, kind="cost_item", cost_item_code="factory_price", amount=6_100_000, currency="RUB", on=TODAY - timedelta(days=3), entered_by=None)
            db.add(old)
            db.commit()
            old_id = old.id
        e1 = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        fp = next(i for i in e1["items"] if i["code"] == "factory_price")
        assert fp["fact"] == 6_100_000 and e1["pnl_fact"]["legacy_not_migrated"] == 1 and fp["forecast"] == 6_100_000   # перерасход — прогноз = факт
        dry = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()
        assert dry["recognition_migrated"] >= 1
        with SessionLocal() as db:
            assert db.scalar(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.source_id == old_id)) is None   # dry-run ничего не записал
        assert c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()["recognition_migrated"] >= 1
        assert c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()["recognition_migrated"] == 0
        e2 = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        assert next(i for i in e2["items"] if i["code"] == "factory_price")["fact"] == 6_100_000 and e2["pnl_fact"]["legacy_not_migrated"] == 0
        with SessionLocal() as db:
            ev = db.scalar(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.source_id == old_id))
            assert ev.basis_type == "legacy_business_event" and ev.document_number == "" and ev.kind == "expense"

        # --- новый факт по статье через план/факт → одно событие; удаление записи → сторно события
        r = c.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 1_600_000, "on": TODAY.isoformat()})
        assert r.status_code == 201, r.text
        eid = r.json()["entry"]["id"]
        evs = c.get(f"/api/deals/{did}/finance/recognition-events", headers=H(FD)).json()
        fr = [e for e in evs["events"] if e["cost_item_code"] == "intl_freight"]
        assert len(fr) == 1 and fr[0]["source_type"] == "planfact" and fr[0]["amount_rub"] == "1600000.00"
        assert c.post(f"/api/finance/recognition-events/{fr[0]['id']}/reverse", headers=H(FD), json={"reason": "ошибка"}).status_code == 409   # правится в план/факте
        pf = c.get(f"/api/deals/{did}/plan-fact", headers=H(FD)).json()
        assert next(x for x in pf["rows"] if x["code"] == "intl_freight")["fact"] == 1_600_000
        tmp = c.post(f"/api/deals/{did}/fact", headers=H(LOG), json={"kind": "cost_item", "cost_item_code": "intl_freight", "amount": 5, "on": TODAY.isoformat()}).json()
        c.delete(f"/api/deals/{did}/fact/{tmp['entry']['id']}", headers=H(LOG))
        ev_tmp = [e for e in c.get(f"/api/deals/{did}/finance/recognition-events", headers=H(FD)).json()["events"] if e["amount"] == "5.00"]
        assert len(ev_tmp) == 1 and ev_tmp[0]["status"] == "reversed"
        assert next(x for x in c.get(f"/api/deals/{did}/plan-fact", headers=H(FD)).json()["rows"] if x["code"] == "intl_freight")["fact"] == 1_600_000

        # --- платёж — не выручка (FIN-PNL-001), delivery — не признание (FIN-PNL-006)
        assert c.post(f"/api/deals/{did}/fact", headers=H(FD), json={"kind": "payment_in", "amount": 3_000_000, "on": TODAY.isoformat()}).status_code == 201
        assert c.post(f"/api/deals/{did}/fact", headers=H(FD), json={"kind": "delivery", "amount": 120, "on": TODAY.isoformat()}).status_code == 201
        pf = c.get(f"/api/deals/{did}/plan-fact", headers=H(FD)).json()
        assert pf["totals"]["revenue"]["fact"] is None and pf["money"]["legacy_in_rub"] == 3_000_000 and pf["money"]["revenue_events"] == 0
        assert len(c.get(f"/api/deals/{did}/finance/recognition-events", headers=H(FD)).json()["events"]) == 3

        # --- признание выручки: только ФД/ЗГД, с основанием и ключом (FIN-PNL-002)
        url = f"/api/deals/{did}/finance/recognition-events"
        rev = {"kind": "revenue", "amount": "10000000.00", "recognized_on": TODAY.isoformat(), "basis_type": "primary_document",
               "document_type": "УПД", "document_number": f"У-{RUN}", "document_date": TODAY.isoformat()}
        assert c.post(url, headers=H(LOG, f"r0-{RUN}"), json=rev).status_code == 403
        assert c.post(url, headers=H(CD, f"r0c-{RUN}"), json=rev).status_code == 403
        assert c.post(url, headers=H(FD), json=rev).status_code == 400
        assert c.post(url, headers=H(FD, f"r1x-{RUN}"), json={**rev, "document_number": ""}).status_code == 422
        assert c.post(url, headers=H(FD, f"r1y-{RUN}"), json={**rev, "basis_type": "business_event", "comment": ""}).status_code == 422
        r1 = c.post(url, headers=H(FD, f"r1-{RUN}"), json=rev)
        assert r1.status_code == 201, r1.text
        assert c.post(url, headers=H(FD, f"r1-{RUN}"), json=rev).json()["id"] == r1.json()["id"]
        bal_before = [a["balance"] for a in c.get("/api/finance/accounts", headers=H(FD)).json()["items"]]

        # --- расход по статье с валютой: курс, дата, источник обязательны (FIN-PNL-003)
        exp = {"kind": "expense", "cost_item_code": "insurance", "amount": "10000.00", "currency": "CNY", "basis_type": "primary_document",
               "document_type": "акт", "document_number": f"А-{RUN}", "document_date": TODAY.isoformat()}
        assert c.post(url, headers=H(LOG, f"x0-{RUN}"), json={**exp, "cost_item_code": "service"}).status_code == 403
        assert c.post(url, headers=H(LOG, f"x1-{RUN}"), json=exp).status_code == 422
        x = c.post(url, headers=H(LOG, f"x2-{RUN}"), json={**exp, "fx_rate": "12.5", "fx_on": TODAY.isoformat(), "fx_source": "курс банка"})
        assert x.status_code == 201 and x.json()["amount_rub"] == "125000.00", x.text
        assert c.get(url, headers=H(MGR)).json() == {"events": [], "all": False}                        # суммы расходов — только с правом «Себестоимость»
        eco = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        assert eco["pnl_fact"]["revenue"] == 10_000_000 and eco["pnl_fact"]["cost"] == 6_100_000 + 1_600_000 + 125_000
        assert eco["pnl_fact"]["profit"] == 10_000_000 - 7_825_000 and next(i for i in eco["items"] if i["code"] == "insurance")["fact"] == 125_000
        assert eco["cash_fact"]["status"] == "insufficient"                                              # признание денег не двигает
        assert "costs_incomplete" in {g["code"] for g in eco["data_gaps"]}                              # прибыль по факту — предварительная
        assert [a["balance"] for a in c.get("/api/finance/accounts", headers=H(FD)).json()["items"]] == bal_before
        # сторно: автор/ФД, с причиной; итог возвращается
        assert c.post(f"/api/finance/recognition-events/{x.json()['id']}/reverse", headers=H(MGR), json={"reason": "дубль"}).status_code == 404
        assert c.post(f"/api/finance/recognition-events/{x.json()['id']}/reverse", headers=H(FD), json={"reason": ""}).status_code == 422
        assert c.post(f"/api/finance/recognition-events/{x.json()['id']}/reverse", headers=H(FD), json={"reason": "дубль акта"}).json()["status"] == "reversed"
        assert c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()["pnl_fact"]["cost"] == 7_700_000

        # --- факт движения денег и фактический кассовый разрыв по подтверждённым операциям
        obs = {o["source"]["line_key"]: o for o in c.get(f"/api/finance/obligations?deal_id={did}&limit=100", headers=H(FD)).json()["items"]}
        acct = _account(c)
        _pay(c, acct, "out", "1900000.00", 10, obs[f"factory:{pos_id}:1"]["id"], "out1")
        assert "provisional" in {g["code"] for g in c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()["data_gaps"]}  # старая отметка ФД — ждёт сверки
        _pay(c, acct, "in", "3000000.00", 5, obs[f"client:{var_id}:2"]["id"], "in1")
        eco = c.get(f"/api/finance/deals/{did}/economy", headers=H(FD)).json()
        cf = eco["cash_fact"]
        assert cf["status"] == "ok" and cf["in_rub"] == "3000000.00" and cf["out_rub"] == "1900000.00" and cf["net_rub"] == "1100000.00"
        assert eco["cash_gap"]["fact"]["amount_rub"] == "-1900000.00" and eco["cash_gap"]["fact"]["days"] == 5
        assert eco["pnl_fact"]["revenue"] == 10_000_000                                                  # поступление не добавило выручку
        assert eco["cash_gap"]["forecast"]["amount_rub"] == "-1900000.00"                                # дальше остатки не уходят ниже факта
        pf = c.get(f"/api/deals/{did}/plan-fact", headers=H(FD)).json()
        assert pf["totals"]["cash_gap"]["fact"]["amount_rub"] == -1_900_000 and pf["money"]["in_rub"] == 3_000_000
        assert pf["totals"]["revenue"]["fact"] == 10_000_000 and pf["economy_url"].endswith(did)

        # закрытый план/факт: ввод признания — только ФД
        assert c.post(f"/api/deals/{did}/plan-fact/close", headers=H(FD)).json()["closed"] is True
        assert c.post(url, headers=H(LOG, f"x3-{RUN}"), json={**exp, "cost_item_code": "intl_freight", "currency": "RUB"}).status_code == 403
        assert c.post(url, headers=H(FD, f"x4-{RUN}"), json={**exp, "cost_item_code": "finance", "currency": "RUB"}).status_code == 201
        with SessionLocal() as db:
            v = db.get(DealVersion, db.get(Deal, did).approved_version_id)
            assert v.calc_result["variants"][var_id]["base"]["price_rub"] == 10_000_000                 # утверждённая версия не пересчитана (FIN-PNL-007)
