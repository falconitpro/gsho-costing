"""Срок производства (D-065): единицы, единое поле, правка КД / ЗГД с историей, защита от перезаписи."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.calc.calendar import WorkCalendar
from app.calc.terms import add_months, production_end
from app.calc.types import ProductionTerm
from app.services import production_term as pt


def test_months_and_units():
    cal = WorkCalendar([])
    start = date(2027, 1, 31)
    assert add_months(start, 1) == date(2027, 2, 28)
    assert production_end(ProductionTerm(value=3, unit="months"), 90, date(2027, 1, 15), cal) == date(2027, 4, 15)
    assert production_end(ProductionTerm(value=12, unit="weeks"), 90, date(2027, 1, 15), cal) == date(2027, 4, 9)
    assert pt.calendar_days({"value": 3, "unit": "months"}) == 91 and pt.calendar_days({"value": 10, "unit": "weeks"}) == 70
    assert pt.text({"value": 4, "unit": "months", "start_event": "factory_prepayment"}) == "4 месяцев с предоплаты заводу"
    assert pt.normalize({"value": 0}) == {} and pt.normalize({"unit": "date"}) == {}


def test_fixed_term_not_overwritten():
    pos = SimpleNamespace(id=None, deal_id=None, production_term={}, production_days=90)
    assert pt.set_term(None, pos, {"value": 120, "unit": "calendar_days"}, "u1", "manager")
    assert pos.production_days == 120 and pos.production_term["source"] == "manager" and not pos.production_term["fixed"]
    assert pt.set_term(None, pos, {"value": 4, "unit": "months"}, "cd1", "cd", "завод подтвердил письмом")
    assert pos.production_term["fixed"] and pos.production_days == 122
    assert not pt.set_term(None, pos, {"value": 60, "unit": "calendar_days"}, "u1", "offer")       # ответ завода не перезаписывает
    assert not pt.set_term(None, pos, {}, "u1", "catalog")                                        # пустое не стирает
    with pytest.raises(pt.FixedTermError):
        pt.set_term(None, pos, {"value": 100, "unit": "calendar_days"}, "u1", "manager")
    assert pos.production_term["value"] == 4 and pos.production_term["unit"] == "months"
    assert not pt.set_term(None, pos, {"value": 4, "unit": "months"}, "u1", "manager")            # то же значение — не ошибка


def test_cd_edits_term_with_history_and_calc_uses_it():
    import os
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("нужна БД")
    from tests.test_api_flow import _ok, H, MGR, LOG, CD   # noqa: F401
    from fastapi.testclient import TestClient
    from app.main import app
    from scripts.seed import run as seed
    seed()
    with TestClient(app) as client:
        clients = _ok(client.get("/api/refs/clients", headers=H(MGR)))
        factories = _ok(client.get("/api/refs/factories", headers=H(MGR)))
        did = _ok(client.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Срок"}), 201)["id"]
        pos = _ok(client.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"factory_id": factories[0]["id"], "model": "PT-1", "machine_type": "ПДМ",
                  "qty": 1, "unit_price": 1000000, "currency": "CNY", "production_term": {"value": 90, "unit": "calendar_days"}}), 201)
        assert pos["production_days"] == 90 and pos["production_term"]["source"] == "manager"
        # менеджер меняет срок полем 14.1 (production_days) — одно и то же поле
        upd = _ok(client.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"production_days": 100}))
        assert upd["production_term"]["value"] == 100 and upd["production_days"] == 100
        # менеджер не может править через эндпоинт КД; без причины — 422
        url = f"/api/deals/{did}/positions/{pos['id']}/production-term"
        assert client.put(url, headers=H(MGR), json={"value": 3, "unit": "months"}).status_code == 403
        assert client.put(url, headers=H(CD), json={"value": 3, "unit": "months"}).status_code == 422
        r = _ok(client.put(url, headers=H(CD), json={"value": 3, "unit": "months", "comment": "завод подтвердил 3 месяца"}))
        assert r["changed"] and r["production"]["fixed"] and r["production"]["text"].startswith("3 месяцев")
        # после фиксации менеджер изменить не может
        r = client.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"production_days": 60})
        assert r.status_code == 409 and "зафиксирован" in r.json()["detail"]
        hist = _ok(client.get(f"{url}/history", headers=H(MGR)))["history"]
        assert [h["after"] for h in hist][:2] == ["3 месяцев с подписания контракта с заводом", "100 календарных дней с подписания контракта с заводом"]
        assert "завод подтвердил" in hist[0]["comment"]
        # расчёт: готовность = контракт с заводом + 3 месяца
        _ok(client.post(f"/api/deals/{did}/send-to-calc", headers=H(MGR)))
        _ok(client.put(f"/api/deals/{did}/logistics/positions/{pos['id']}", headers=H(LOG), json={"hs_code": "8429519000", "util_applies": False}))
        _ok(client.post(f"/api/deals/{did}/logistics/complete", headers=H(LOG), json={"no_delivery_cost": True}))
        calc = _ok(client.get(f"/api/deals/{did}/calc", headers=H(CD)))
        assert calc["production"][pos["id"]]["fixed"] and calc["can_edit_production"]
        fc = calc["input"]["positions"][0]
        assert fc["production_term"]["unit"] == "months" and fc["production_term"]["value"] == 3
        ev = next(iter(calc["terms"].values()))[0]["events"]
        assert date.fromisoformat(ev["readiness"]) == add_months(date.fromisoformat(ev["factory_contract"]), 3)   # к готовности прибавляются остальные сроки
        assert date.fromisoformat(ev["border"]) > date.fromisoformat(ev["readiness"]) and date.fromisoformat(ev["delivery"]) > date.fromisoformat(ev["border"])
