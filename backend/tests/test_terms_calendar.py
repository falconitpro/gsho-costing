"""Задачи 1, 3: производственный календарь, сроки производства и поставки, события платежей, БГ на аванс."""
from datetime import date

import pytest

from app.calc import calc_deal
from app.calc.calendar import WorkCalendar, add_business_days
from app.calc.terms import client_delivery_term, production_end
from app.calc.types import AdvanceGuarantee, DeliveryTerm, LocalOptionInput, PaymentEvent, PaymentLine, ProductionTerm
from tests.fixtures import sample_deal


def test_calendar_skips_weekends_and_holidays():
    cal = WorkCalendar()
    assert add_business_days(date(2026, 12, 30), 2) == date(2027, 1, 12)      # 31.12 — перенос, каникулы 1–8 января, 9–10 — выходные
    assert cal.add_working_days(date(2026, 3, 6), 1) == date(2026, 3, 10)     # 7–9 марта (пн 9.03 — перенос)
    assert cal.working_days_between(date(2026, 11, 2), date(2026, 11, 9)) == 4  # 4 ноября — праздник
    cal2 = WorkCalendar(holidays=[date(2026, 11, 3)])
    assert cal2.working_days_between(date(2026, 11, 2), date(2026, 11, 9)) == 3


def test_production_term_units():
    cal = WorkCalendar()
    start = date(2026, 11, 2)
    assert production_end(ProductionTerm(value=10, unit="weeks"), 90, start, cal) == start.fromordinal(start.toordinal() + 70)
    assert production_end(ProductionTerm(value=20, unit="working_days"), 90, start, cal) == date(2026, 12, 1)
    assert production_end(ProductionTerm(unit="date", fixed_date=date(2027, 3, 1)), 90, start, cal) == date(2027, 3, 1)
    assert production_end(None, 90, start, cal, dt_days=30) == start.fromordinal(start.toordinal() + 120)


def test_delivery_term_reserve_and_rounding():
    cal = WorkCalendar()
    start, delivery = date(2026, 11, 2), date(2027, 1, 31)   # 90 календарных дней
    r = client_delivery_term(DeliveryTerm(unit="calendar_days", start_event="lc_opened"), start, delivery, 10.0, cal)
    assert r.calc_days == 90 and r.with_reserve == 99 and r.promised == 105      # 99 → кратно 7 вверх
    assert r.text == "105 календарных дней с даты открытия аккредитива"
    w = client_delivery_term(DeliveryTerm(unit="weeks", start_event="client_contract"), start, delivery, 10.0, cal)
    assert w.promised == 15                                                        # 12.86 нед × 1.1 = 14.1 → 15
    wd = client_delivery_term(DeliveryTerm(unit="working_days", start_event="client_prepayment"), start, delivery, 10.0, cal)
    assert wd.promised % 5 == 0 and wd.promised >= wd.with_reserve


def test_payment_events_border_lc_and_advance_guarantee():
    inp = sample_deal(price_a=125_000_000)
    v = inp.variants[0]
    v.client_payments = [PaymentLine(no=1, share_pct=30, event=PaymentEvent.CONTRACT, offset_days=0),
                         PaymentLine(no=2, share_pct=70, event=PaymentEvent.BORDER, offset_days=5, offset_kind="working", via="lc")]
    inp.finance.advance_guarantee = AdvanceGuarantee(enabled=True, mode="annual_pct", value=3.0)
    res = calc_deal(inp)
    base = res.variants["V1"]["base"]
    pays = {p["no"]: p for p in base.payment_dates}
    pos = base.positions[0]
    assert pays[1]["on"] == inp.contract_date and pays[2]["via"] == "lc"
    assert pays[2]["on"] > max(pc.events["border"] for pc in base.positions)       # 5 рабочих дней после CMR
    # БГ на аванс: 30 % × 3 % годовых × срок до поставки
    adv = pays[1]["amount_rub"]
    days = (max(pc.events["delivery"] for pc in base.positions) - inp.contract_date).days
    assert base.guarantee_cost_rub == pytest.approx(adv * 0.03 * days / 365, rel=1e-3)
    assert base.finance_breakdown["guarantees"] == pytest.approx(base.guarantee_cost_rub, abs=0.01)
    # разовый % и фиксированная сумма
    inp.finance.advance_guarantee = AdvanceGuarantee(mode="one_time_pct", value=1.0)
    assert calc_deal(inp).variants["V1"]["base"].guarantee_cost_rub == pytest.approx(adv * 0.01, rel=1e-6)
    inp.finance.advance_guarantee = AdvanceGuarantee(mode="fixed", value=50_000)
    assert calc_deal(inp).variants["V1"]["base"].guarantee_cost_rub == 50_000
    inp.finance.advance_guarantee = AdvanceGuarantee(enabled=False)
    assert calc_deal(inp).variants["V1"]["base"].guarantee_cost_rub == 0
    # срок поставки клиенту по позициям
    assert pos.delivery_term is not None and pos.delivery_term.promised % 7 == 0 and "с даты подписания контракта" in pos.delivery_term.text


def test_actual_dates_shift_following_events():
    inp = sample_deal(price_a=125_000_000)
    base = calc_deal(inp).variants["V1"]["base"].positions[0]
    inp.positions[0].actual_events = {"readiness": base.events["readiness"].fromordinal(base.events["readiness"].toordinal() + 20)}
    shifted = calc_deal(inp).variants["V1"]["base"].positions[0]
    assert shifted.events["border"] == base.events["border"].fromordinal(base.events["border"].toordinal() + 20)
    assert shifted.events["delivery"] > base.events["delivery"]
    inp.deal_actual_events = {"contract": date(2027, 1, 10)}
    later = calc_deal(inp).variants["V1"]["base"]
    assert later.positions[1].events["factory_contract"] == date(2027, 1, 10).fromordinal(date(2027, 1, 10).toordinal() + inp.factory_contract_lag_days)


def test_local_options_cost_item_and_term(monkeypatch):
    inp = sample_deal(price_a=125_000_000)
    p = inp.positions[1]
    p.local_options = [LocalOptionInput(id="o1", name="Кондиционер", qty=1, cost=300_000, currency="RUB", supplier_lead_days=30,
                                        needs_install=True, install_stage="before_handover", install_days=7, install_cost_rub=50_000)]
    res = calc_deal(inp).variants["V1"]["base"]
    pc = next(x for x in res.positions if x.position_id == "P2")
    assert pc.items_rub["local_completion"] == pytest.approx(350_000, abs=1)
    assert res.items_rub["local_completion"] == pytest.approx(350_000, abs=1)
    base = calc_deal(sample_deal(price_a=125_000_000)).variants["V1"]["base"]
    pc0 = next(x for x in base.positions if x.position_id == "P2")
    assert (pc.events["delivery"] - pc0.events["delivery"]).days == 7           # установка до передачи удлиняет поставку
    assert pc.delivery_term.install_days == 7
    p.local_options[0].install_stage = "during_commissioning"
    pc2 = next(x for x in calc_deal(inp).variants["V1"]["base"].positions if x.position_id == "P2")
    assert pc2.events["delivery"] == pc0.events["delivery"]                      # при ПНР срок не меняется
