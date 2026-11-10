"""«Финансы», этап 2: прогноз по утверждённому расчёту и замещение договором (D-126, D-127; 02 раздел 6 «Замещение плана договором»)."""
from __future__ import annotations

import os
import tempfile
import uuid

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
from app.models import Deal, DealVersion, Factory, Position, Variant  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, FD, LAW = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "lawyer@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")
RUN = uuid.uuid4().hex[:8]


def _calc(variant_id: str, position_id: str, client_amounts=(3_000_000, 7_000_000)) -> dict:
    return {"variants": {variant_id: {"base": {
        "payment_dates": [{"no": 1, "event": "contract", "on": "2026-12-01", "amount_rub": client_amounts[0], "via": "transfer"},
                          {"no": 2, "event": "border", "on": "2027-05-20", "amount_rub": client_amounts[1], "via": "lc"}],
        "positions": [{"position_id": position_id, "factory_payment_dates": [
            {"no": 1, "event": "factory_contract", "on": "2026-12-10", "amount": 150000, "currency": "CNY", "amount_rub": 1900000},
            {"no": 2, "event": "readiness", "on": "2027-04-01", "amount": 350000, "currency": "CNY", "amount_rub": 4400000}]}]},
        "pessimistic": {"payment_dates": [{"no": 1, "on": "2026-12-20", "amount_rub": 1}]}}}}


def _contract(c, deal_id, kind, amount, terms, cp_id=None, cp_kind=None):
    ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
    body = {"kind": kind, "own_entity_id": ent["id"], "cp_name": f"Контрагент {RUN}", "subject": "поставка", "amount": amount,
            "currency": "RUB" if kind == "client_supply" else "CNY", "deal_id": deal_id}
    if cp_id:
        body.update({"cp_id": cp_id, "cp_kind": cp_kind})
    k = c.post("/api/legal/contracts", headers=H(LAW), json=body)
    assert k.status_code == 201, k.text
    k = k.json()
    c.put(f"/api/legal/contracts/{k['id']}", headers=H(LAW), json={"payment_terms": terms, "signatory": {"name": "П. П.", "basis": "Устава"}, "law": {"claim_days": 30},
                                                                  "signed_on": "2026-11-05"})
    c.post(f"/api/legal/contracts/{k['id']}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
    r = c.post(f"/api/legal/contracts/{k['id']}/status", headers=H(LAW), json={"status": "active"})
    assert r.status_code == 200, r.text
    return r.json()


def _forecast(c, deal_id, inactive=True):
    rows = c.get(f"/api/finance/obligations?deal_id={deal_id}&source_type=calculation&limit=500{'&include_inactive=true' if inactive else ''}", headers=H(FD)).json()["items"]
    return {o["source"]["line_key"]: o for o in rows}


def test_forecast_from_approved_calculation_and_supersession_by_contract():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"прогноз-{RUN}"}).json()
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            fac = db.scalars(select(Factory)).first()
            pos = Position(deal_id=deal.id, factory_id=fac.id, model=f"XDR-{RUN}", machine_type="Самосвал", qty=1, unit_price=500000, currency="CNY")
            db.add(pos)
            db.flush()
            var = Variant(deal_id=deal.id, name="Основной", position_ids=[pos.id], is_default=True)
            db.add(var)
            db.flush()
            v1 = DealVersion(deal_id=deal.id, version_no=1, reason="approval", calc_input={}, calc_result=_calc(var.id, pos.id))
            db.add(v1)
            db.flush()
            deal.approved_version_id = v1.id
            db.commit()
            deal_id, var_id, pos_id, fac_id, v1_id = deal.id, var.id, pos.id, fac.id, v1.id

        # до выигрыша прогноза нет (сделка может быть проиграна)
        c.post("/api/finance/sync?dry_run=false", headers=H(FD))
        assert _forecast(c, deal_id) == {}
        # выиграли — смена статуса через общий переход сразу строит прогноз (без ручной пересборки)
        with SessionLocal() as db:
            from app.services.deal_service import apply_transition
            deal = db.get(Deal, deal_id)
            deal.won_variant_id = var_id
            apply_transition(db, deal, TransitionResult(status=DealStatus.CONTRACT, audit="тест: выиграна"), None)
            db.commit()
        f = _forecast(c, deal_id)
        assert set(f) == {f"client:{var_id}:1", f"client:{var_id}:2", f"factory:{pos_id}:1", f"factory:{pos_id}:2"}
        cl1 = f[f"client:{var_id}:1"]
        assert cl1["nature"] == "forecast" and cl1["amount"] == "3000000.00" and cl1["currency"] == "RUB" and cl1["direction"] == "in"
        assert cl1["legal_due_on"] is None and cl1["forecast_on"] == "2026-12-01" and "прогноз по утверждённому расчёту" in cl1["legal_due_text"]
        assert f[f"factory:{pos_id}:2"]["currency"] == "CNY" and f[f"factory:{pos_id}:2"]["amount"] == "350000.00" and f[f"factory:{pos_id}:2"]["direction"] == "out"
        assert cl1["source"]["number"] == "версия 1" and cl1["source"]["url"] == f"/deals/{deal_id}?tab=versions"
        # пессимистичный сценарий в реестр не попадает; прогноз завода — только с правом на себестоимость
        assert all(o["amount"] != "1.00" for o in f.values())

        # подписан договор с клиентом: строки 1 и 2 договора однозначно замещают прогноз — без двойного итога
        k = _contract(c, deal_id, "client_supply", 10_000_000, [{"share_pct": 30, "event": "contract", "offset_days": 5, "offset_kind": "working"},
                                                                {"share_pct": 70, "event": "act_signed", "offset_days": 10, "offset_kind": "calendar"}])
        f = _forecast(c, deal_id)
        k_rows = {o["source"]["line_no"]: o for o in c.get(f"/api/finance/obligations?contract_id={k['id']}", headers=H(FD)).json()["items"]}
        for n in (1, 2):
            o = c.get(f"/api/finance/obligations/{f[f'client:{var_id}:{n}']['id']}", headers=H(FD)).json()
            assert o["is_active"] is False and o["status"] == "superseded" and k["number"] in o["excluded_reason"]
        with SessionLocal() as db:
            from app.models import FinanceObligation
            assert db.get(FinanceObligation, f[f"client:{var_id}:1"]["id"]).superseded_by_id == k_rows[1]["id"]
        assert f[f"factory:{pos_id}:1"]["is_active"] is True                                      # договора с заводом ещё нет
        active_in = c.get(f"/api/finance/obligations?deal_id={deal_id}&direction=in", headers=H(FD)).json()
        assert active_in["totals"]["RUB"]["in"] == "10000000.00"                                   # только договор, прогноз не суммируется

        # договор с заводом: построчно (1 позиция, совпадение номера) — замещение; прогноз остаётся в истории
        _contract(c, deal_id, "supplier", 500_000, [{"share_pct": 30, "event": "contract", "offset_days": 5, "offset_kind": "calendar"},
                                                    {"share_pct": 70, "event": "readiness", "offset_days": 0, "offset_kind": "calendar"}], cp_id=fac_id, cp_kind="factory")
        f = _forecast(c, deal_id)
        assert all(o["is_active"] is False for o in f.values())
        assert not _forecast(c, deal_id, inactive=False)

        # новая утверждённая версия: прежняя проекция — история («заменено утверждённой версией 2»), новая — тоже замещена договорами
        with SessionLocal() as db:
            deal = db.get(Deal, deal_id)
            v2 = DealVersion(deal_id=deal_id, version_no=2, reason="contract_change", calc_input={}, calc_result=_calc(var_id, pos_id, (4_000_000, 6_000_000)))
            db.add(v2)
            db.flush()
            deal.approved_version_id = v2.id
            db.commit()
        rep = c.post("/api/finance/sync?dry_run=false", headers=H(FD)).json()
        assert rep["created"] >= 4
        allf = c.get(f"/api/finance/obligations?deal_id={deal_id}&source_type=calculation&include_inactive=true&limit=500", headers=H(FD)).json()["items"]
        old = [o for o in allf if o["source"]["id"] == v1_id]
        assert old and all(not o["is_active"] for o in old)
        new = [o for o in allf if o["source"]["id"] != v1_id]
        assert len(new) == 4 and all(not o["is_active"] for o in new)                             # договоры подписаны → прогноз v2 сразу замещён
        dry = c.post("/api/finance/sync?dry_run=true", headers=H(FD)).json()
        assert dry["created"] == 0 and dry["updated"] == 0, dry
        # КД без раздела — 403; менеджер — тоже
        assert c.get(f"/api/finance/obligations?deal_id={deal_id}", headers=H(CD)).status_code == 403


def test_forecast_inactive_when_deal_closed_and_conflict_when_paid_forecast_superseded():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"закрыта-{RUN}"}).json()
        with SessionLocal() as db:
            deal = db.get(Deal, d["id"])
            fac = db.scalars(select(Factory)).first()
            pos = Position(deal_id=deal.id, factory_id=fac.id, model="M", machine_type="Самосвал", qty=1, unit_price=1, currency="CNY")
            db.add(pos)
            db.flush()
            var = Variant(deal_id=deal.id, name="Основной", position_ids=[pos.id], is_default=True)
            db.add(var)
            db.flush()
            v = DealVersion(deal_id=deal.id, version_no=1, reason="approval", calc_input={}, calc_result=_calc(var.id, pos.id))
            db.add(v)
            db.flush()
            deal.approved_version_id, deal.won_variant_id, deal.status = v.id, var.id, DealStatus.EXECUTION
            db.commit()
            deal_id, var_id = deal.id, var.id
        c.post("/api/finance/sync?dry_run=false", headers=H(FD))
        f = _forecast(c, deal_id)
        assert f[f"client:{var_id}:1"]["is_active"] is True
        # по прогнозной строке разнесли деньги (без договора), потом подписали договор — прогноз замещён, разнесение → сверка
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        tx = c.post("/api/finance/transactions", headers={**H(FD), "Idempotency-Key": f"fc-{RUN}"},
                    json={"direction": "in", "amount": "1000.00", "currency": "RUB", "booked_at": "2026-11-09T10:00:00+03:00"}).json()
        r = c.post(f"/api/finance/transactions/{tx['id']}/allocations", headers={**H(FD), "Idempotency-Key": f"fa-{RUN}"},
                   json={"items": [{"obligation_id": f[f"client:{var_id}:1"]["id"], "amount_tx": "1000.00"}]})
        assert r.status_code == 201, r.text
        _contract(c, deal_id, "client_supply", 10_000_000, [{"share_pct": 100, "event": "contract", "offset_days": 5, "offset_kind": "working"}])
        o = c.get(f"/api/finance/obligations/{f[f'client:{var_id}:1']['id']}", headers=H(FD)).json()
        assert o["is_active"] is False and o["sync_status"] == "conflict" and "перенесите" in o["excluded_reason"]
        # закрытие сделки выводит оставшийся прогноз из итогов
        with SessionLocal() as db:
            from app.services.deal_service import apply_transition
            apply_transition(db, db.get(Deal, deal_id), TransitionResult(status=DealStatus.CLOSED, audit="тест: закрыта"), None)
            db.commit()
        factory = [o for k, o in _forecast(c, deal_id).items() if k.startswith("factory:")]
        assert factory and all(o["is_active"] is False and "сделка закрыта" in o["excluded_reason"] for o in factory)
