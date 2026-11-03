from datetime import date

import pytest

from app.calc import calc_deal, calc_service, solve_price
from app.calc.cashflow import Flow, build_cashflow
from app.calc.fx_forecast import FxHistory, forecast_rate
from app.calc.service import MaintenanceRegulation, ServiceInput, ServiceOperation, SparePartRow
from app.calc.types import (
    COST_KEYS, DEFAULT_SCENARIOS, FinanceSettings, FxForecastSettings, FxRates,
)
from tests.fixtures import sample_deal, synthetic_cny_history


# ------------------------------------------------------------------ прогноз курса

def test_history_trend_and_seasonality():
    h = FxHistory(synthetic_cny_history(date(2026, 10, 31)))
    trend = h.annual_trend(date(2026, 10, 31), 3)
    assert 0.03 < trend < 0.05           # заложен дрейф +4 %/год
    season = h.seasonality(date(2026, 10, 31), 3)
    assert season[6][0] > 1.0 > season[12][0]   # лето выше тренда, декабрь ниже (по синтетике)
    vol = h.horizon_volatility_pct(date(2026, 10, 31), 3, 90)
    assert vol is not None and 0 < vol < 10


def test_forecast_components_and_weights():
    fx = FxRates(as_of=date(2026, 10, 31), rates={"CNY": 12.5})
    settings = FxForecastSettings(key_rate_pct=16, pboc_rate_pct=3, foreign_rate_pct={"CNY": 3})
    h = FxHistory(synthetic_cny_history(date(2026, 10, 31)))
    r = forecast_rate("CNY", date(2027, 4, 30), fx, settings, h, DEFAULT_SCENARIOS)
    assert r.t_days == 181
    # форвард: 12.5 × (1 + 0.13 × 181/365)
    assert r.a == pytest.approx(12.5 * (1 + 0.13 * 181 / 365), rel=1e-6)
    assert r.g is None and r.weights[2] == 0        # без прогноза ФД вес Г уходит на A и Б×В
    assert r.weights[0] == pytest.approx(4 / 7)
    assert r.rate_by_scenario["base"] == pytest.approx(r.forecast)
    assert r.rate_by_scenario["pessimistic"] > r.forecast > r.rate_by_scenario["optimistic"]
    assert r.dk_pct["pessimistic"] > 0 > r.dk_pct["optimistic"]
    assert abs(r.dk_pct["optimistic"]) == pytest.approx(0.6 * r.dk_pct["pessimistic"], rel=1e-3)


def test_forecast_fixed_rate_and_expert():
    from app.calc.types import ExpertPoint
    fx = FxRates(as_of=date(2026, 10, 31), rates={"CNY": 12.5})
    settings = FxForecastSettings(use_historical_volatility=False,
                                  expert_points={"CNY": [ExpertPoint(on=date(2027, 1, 1), rate=13.0),
                                                         ExpertPoint(on=date(2027, 7, 1), rate=14.0)]})
    r = forecast_rate("CNY", date(2027, 4, 1), fx, settings, None, DEFAULT_SCENARIOS)
    assert r.g == pytest.approx(13.5, abs=0.02)
    assert r.weights == (0.4, 0.3, 0.3)
    assert r.dk_pct == {"pessimistic": 5.0, "base": 0.0, "optimistic": -3.0}
    fixed = forecast_rate("CNY", date(2027, 4, 1), fx, settings, None, DEFAULT_SCENARIOS, fixed_rate=12.8)
    assert fixed.fixed and all(v == 12.8 for v in fixed.rate_by_scenario.values())


# ------------------------------------------------------------------ денежный поток

def test_cashflow_gap_and_money_cost():
    fin = FinanceSettings(financing_rate_pct=18.25, conversion_fee_pct=1.0, transfer_fee_rub=1000, cashflow_bucket_days=7)
    flows = [
        Flow(on=date(2026, 1, 1), amount_rub=-1_000_000, kind="factory", fx_amount=80_000, currency="CNY"),
        Flow(on=date(2026, 2, 12), amount_rub=1_500_000, kind="client"),
    ]
    r = build_cashflow(flows, fin)
    assert r.gap.amount_rub == -1_000_000
    assert r.gap.starts_on == date(2026, 1, 1)
    assert r.gap.days == 42
    assert r.finance_cost_rub == pytest.approx(1_000_000 * 0.1825 / 365 * 42, rel=1e-6)
    assert r.conversion_fees_rub == 10_000 and r.transfer_fees_rub == 1000
    assert r.points[-1].balance == 500_000


# ------------------------------------------------------------------ себестоимость

def test_deal_costing_structure():
    res = calc_deal(sample_deal())
    v1 = res.variants["V1"]
    assert set(v1) == {"pessimistic", "base", "optimistic"}
    base = v1["base"]
    assert set(base.items_rub) == set(COST_KEYS)
    assert base.cost_total_rub == pytest.approx(sum(base.items_rub.values()), abs=0.05)
    assert base.cost_total_rub == pytest.approx(sum(p.total_rub for p in base.positions), abs=1.0)
    # статья 6 от таможенной стоимости, НДС не в себестоимости по умолчанию (зачёт), но в потоке
    p1 = base.positions[0]
    assert p1.items_rub["customs_duty"] == pytest.approx(p1.customs_value_rub * 0.05, abs=1.0)
    assert p1.items_rub["import_vat"] == 0.0
    assert any(pt.inflow > 0 for pt in base.cashflow)   # возврат НДС и платежи клиента
    # сценарии упорядочены по стоимости
    assert v1["pessimistic"].cost_total_rub > base.cost_total_rub > v1["optimistic"].cost_total_rub
    assert v1["pessimistic"].delivery_days == base.delivery_days + 30
    # накладные по шкале: прямая себестоимость в диапазоне 50–200 млн → 4 %
    direct = sum(v for k, v in base.items_rub.items() if k != "overhead")
    assert 50e6 < direct < 200e6
    assert base.items_rub["overhead"] == pytest.approx(direct * 0.04, abs=1.0)
    # маржа и наценка
    assert base.margin_pct == pytest.approx((base.price_rub - base.cost_total_rub) / base.price_rub * 100, abs=1e-3)
    assert base.markup_pct > base.margin_pct
    assert base.sensitivity is not None and base.sensitivity.margin_delta_pct_per_1pct_fx < 0
    assert base.sensitivity.breakeven_rate_base_ccy > 12.5
    # лист «Курсы»: по каждому платёжу — компоненты
    assert res.fx_components and all(c.currency == "CNY" for c in res.fx_components)
    assert all(c.weights[2] == 0 for c in res.fx_components)


def test_margin_threshold_triggers_fd_visa():
    inp = sample_deal(price_a=95_000_000)
    res = calc_deal(inp)
    base = res.variants["V1"]["base"]
    if base.margin_pct >= 3:
        inp = sample_deal(price_a=base.cost_total_rub * 1.02)
        res = calc_deal(inp)
    assert res.approval.financial and res.approval.reasons
    ok = sample_deal(price_a=res.variants["V1"]["base"].cost_total_rub * 1.15)
    assert not calc_deal(ok).approval.financial


def test_solve_price_by_margin_and_markup():
    inp = sample_deal(price_a=None)
    p = solve_price(inp, "V1", target_margin_pct=8.0)
    inp2 = sample_deal(price_a=p)
    assert calc_deal(inp2).variants["V1"]["base"].margin_pct == pytest.approx(8.0, abs=0.01)
    p2 = solve_price(inp, "V1", target_markup_pct=10.0)
    assert calc_deal(sample_deal(price_a=p2)).variants["V1"]["base"].markup_pct == pytest.approx(10.0, abs=0.01)


def test_full_prepayment_reduces_cash_gap_but_adds_guarantee_cost():
    p = 95_000_000
    res = calc_deal(sample_deal(price_a=p, price_b=p))
    v1, v2 = res.variants["V1"]["base"], res.variants["V2"]["base"]
    assert v2.cash_gap.amount_rub > v1.cash_gap.amount_rub      # разрыв меньше (ближе к нулю)
    # БГ на аванс (задача 1): есть у обоих вариантов (30 % и 100 % предоплаты), у 100 % — больше
    assert v2.finance_breakdown["guarantees"] > v1.finance_breakdown["guarantees"] > 0
    assert v2.finance_breakdown["money_cost"] < v1.finance_breakdown["money_cost"]   # стоимость денег ниже при полной предоплате


def test_overhead_override_requires_gd_visa():
    inp = sample_deal()
    inp.variants[0].overhead_pct_override = 2.0
    res = calc_deal(inp)
    assert res.approval.general
    assert res.variants["V1"]["base"].items_rub["overhead"] < calc_deal(sample_deal()).variants["V1"]["base"].items_rub["overhead"]


# ------------------------------------------------------------------ сервис

def test_service_calc():
    inp = ServiceInput(
        machine_type="Шахтный самосвал", model="XDR80T", qty=2, target_hours=10_000, hours_per_month=500,
        regulations=[
            MaintenanceRegulation(name="ТО-1", interval_hours=250, operations=[ServiceOperation(name="Осмотр, смазка", hours=2, qualification="mechanic")]),
            MaintenanceRegulation(name="ТО-2", interval_hours=500, operations=[ServiceOperation(name="Замена масла и фильтров", hours=4, qualification="engineer_2", spare_part_rows=[1, 2])]),
        ],
        spare_parts=[SparePartRow(row=1, system="Двигатель", name="Масло моторное", unit_price=1200, qty_per_service=1),
                     SparePartRow(row=2, system="Двигатель", name="Фильтр масляный", unit_price=180, qty_per_service=2)],
    )
    r = calc_service(inp)
    assert r.executions == {"ТО-1": 40, "ТО-2": 20}
    assert r.labour_rub == 40 * 2 * 2 * 2000 + 20 * 4 * 2 * 2800
    assert r.spare_parts_fx.amount == 20 * (1200 + 2 * 180) * 2
    assert r.reserve_fx.amount == pytest.approx(r.spare_parts_fx.amount * 0.05)
    assert len(r.rows) == 20 and sum(row.visits for row in r.rows) == 20
    assert r.total_rub_part == r.labour_rub + r.visits_rub


def test_commissions_and_transfer_fee_modes():
    """Статьи 16–18: внешняя комиссия от цены клиента, комиссии менеджера и КД — от себестоимости (статьи 1–15);
    комиссия за перевод — фикс, процент или и то и другое."""
    inp = sample_deal()
    base0 = calc_deal(inp).variants["V1"]["base"]
    inp2 = sample_deal()
    inp2.external_commission_pct, inp2.manager_commission_pct, inp2.cd_commission_pct = 2.0, 1.0, 0.5
    b = calc_deal(inp2).variants["V1"]["base"]
    assert b.items_rub["external_commission"] == pytest.approx(b.price_rub * 0.02, abs=1.0)
    cost_1_15 = sum(v for k, v in b.items_rub.items() if k not in ("external_commission", "manager_commission", "cd_commission"))
    assert b.items_rub["manager_commission"] == pytest.approx(cost_1_15 * 0.01, abs=1.0)
    assert b.items_rub["cd_commission"] == pytest.approx(cost_1_15 * 0.005, abs=1.0)
    assert b.cost_total_rub > base0.cost_total_rub and b.margin_pct < base0.margin_pct
    assert b.cost_total_rub == pytest.approx(sum(p.total_rub for p in b.positions), abs=1.0)

    flows = [Flow(on=date(2027, 1, 10), amount_rub=-1_000_000, kind="factory", currency="CNY", fx_amount=80_000)]
    from app.calc.types import FinanceSettings
    fixed = build_cashflow(flows, FinanceSettings(transfer_fee_rub=3000, transfer_fee_mode="fixed"))
    pct = build_cashflow(flows, FinanceSettings(transfer_fee_rub=3000, transfer_fee_pct=0.1, transfer_fee_mode="pct"))
    both = build_cashflow(flows, FinanceSettings(transfer_fee_rub=3000, transfer_fee_pct=0.1, transfer_fee_mode="both"))
    assert fixed.transfer_fees_rub == pytest.approx(3000)
    assert pct.transfer_fees_rub == pytest.approx(1000)
    assert both.transfer_fees_rub == pytest.approx(4000)


def test_door_to_door_delivery_split():
    """Единая стоимость доставки завод → клиент: 10 % — до границы РФ (в таможенной стоимости), 90 % — от границы до клиента."""
    from app.calc.types import Money
    inp = sample_deal()
    inp.batches[0].door_to_door = Money(amount=1_000_000, currency="CNY")
    inp.batches[0].door_to_door_border_pct = 10.0
    b = calc_deal(inp).variants["V1"]["base"]
    members = [pc for pc in b.positions if any(p.id == pc.position_id and p.batch_id == inp.batches[0].id for p in inp.positions)]
    assert sum(pc.items_fx["intl_freight"].amount for pc in members) == pytest.approx(100_000, abs=1)
    assert all(pc.items_fx["intl_freight"].currency == "CNY" for pc in members)
    rf = sum(pc.items_rub["rf_delivery"] for pc in members)
    intl = sum(pc.items_rub["intl_freight"] for pc in members)
    assert rf == pytest.approx(intl * 9, rel=0.15)   # 90 / 10 с поправкой на курс на дату поставки


def test_conversion_fee_only_on_fx_part_of_service():
    """Комиссия за конвертацию и перевод — только с валютной части сервиса; рублёвая часть отдельным платежом."""
    from app.calc.types import Money, ServiceSummary
    inp = sample_deal()
    base = calc_deal(inp).variants["V1"]["base"].finance_breakdown
    inp2 = sample_deal()
    p1 = inp2.positions[0]
    p1.service = ServiceSummary(cost_fx=p1.service.cost_fx, cost_rub=p1.service.cost_rub + 10_000_000, show_in_kp=True)
    alt = calc_deal(inp2).variants["V1"]["base"].finance_breakdown
    assert alt["conversion_fees"] == base["conversion_fees"]          # рост рублёвой части не меняет конвертацию
    assert alt["transfer_fees"] == base["transfer_fees"]
    inp3 = sample_deal()
    p = inp3.positions[0]
    p.service = ServiceSummary(cost_fx=Money(amount=p.service.cost_fx.amount * 2, currency="CNY"), cost_rub=p.service.cost_rub, show_in_kp=True)
    assert calc_deal(inp3).variants["V1"]["base"].finance_breakdown["conversion_fees"] > base["conversion_fees"]
