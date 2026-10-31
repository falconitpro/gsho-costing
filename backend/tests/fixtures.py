"""Тестовая сделка: 2 завода, 2 позиции, 1 партия, 2 варианта, синтетическая история курса CNY/RUB."""
from __future__ import annotations

import math
from datetime import date, timedelta

from app.calc.types import (
    BatchInput, DealCalcInput, FxRates, GuaranteeKind, Money, PaymentEvent, PaymentLine, PositionInput,
    ServiceSummary, VariantInput,
)


def synthetic_cny_history(end: date, years: int = 4, start_rate: float = 11.0, drift: float = 0.04) -> list[tuple[date, float]]:
    """Детерминированная история: тренд +4 %/год, сезонность ±2 %, «шум» синусами."""
    pts = []
    start = date(end.year - years, end.month, end.day)
    d = start
    i = 0
    while d <= end:
        years_passed = (d - start).days / 365.25
        trend = start_rate * (1 + drift) ** years_passed
        season = 1 + 0.02 * math.sin(2 * math.pi * (d.month - 3) / 12)
        noise = 1 + 0.01 * math.sin(i / 7.0) + 0.005 * math.cos(i / 23.0)
        if d.weekday() < 5:
            pts.append((d, round(trend * season * noise, 4)))
        d += timedelta(days=1)
        i += 1
    return pts


def sample_deal(price_a: float | None = 125_000_000.0, price_b: float | None = None) -> DealCalcInput:
    calc_date = date(2026, 10, 31)
    return DealCalcInput(
        deal_no="D-2026-0042",
        calc_date=calc_date,
        contract_date=date(2026, 11, 15),
        base_currency="CNY",
        fx=FxRates(as_of=calc_date, rates={"CNY": 12.5, "USD": 90.0, "EUR": 98.0, "RUB": 1.0}),
        fx_history={"CNY": synthetic_cny_history(calc_date)},
        positions=[
            PositionInput(
                id="P1", factory="Xuzhou XCMG Mining", model="XDR80T", machine_type="Шахтный самосвал", qty=2,
                unit_price=2_300_000, options_price=120_000, currency="CNY", production_days=120, delivery_days=7,
                domestic_logistics=Money(amount=48_000, currency="CNY"), export_formalities=Money(amount=6_000, currency="CNY"),
                hs_code="8704101090", duty_pct=5.0, vat_pct=20.0, util_fee_rub=1_200_000, certification_rub=850_000,
                weight_kg=2 * 38_000, volume_m3=2 * 95, warranty_reserve_pct=2.0, batch_id="B1",
                service=ServiceSummary(cost_fx=Money(amount=420_000, currency="CNY"), cost_rub=2_900_000, show_in_kp=True),
            ),
            PositionInput(
                id="P2", factory="Shandong Tiangong", model="TG-1000 LHD", machine_type="ПДМ", qty=1,
                unit_price=1_650_000, currency="CNY", production_days=90, delivery_days=5,
                factory_payments=[PaymentLine(no=1, share_pct=40, event=PaymentEvent.CONTRACT, offset_days=7),
                                  PaymentLine(no=2, share_pct=60, event=PaymentEvent.READINESS)],
                domestic_logistics=Money(amount=22_000, currency="CNY"), export_formalities=Money(amount=4_000, currency="CNY"),
                hs_code="8429519000", duty_pct=0.0, vat_pct=20.0, util_fee_rub=600_000, certification_rub=400_000,
                weight_kg=24_000, volume_m3=60, warranty_reserve_pct=1.5, batch_id="B1",
            ),
        ],
        batches=[BatchInput(id="B1", transport_units=3, intl_freight=Money(amount=210_000, currency="CNY"), insurance_pct=0.3,
                            broker_rub=450_000, rf_delivery_rub=1_900_000, transit_days=18, customs_days=6)],
        variants=[
            VariantInput(id="V1", name="Самосвалы + ПДМ, 30/70", position_ids=["P1", "P2"], price_rub=price_a, service_in_kp=True),
            VariantInput(id="V2", name="Самосвалы + ПДМ, 100 % предоплата", position_ids=["P1", "P2"], price_rub=price_b,
                         client_payments=[PaymentLine(no=1, share_pct=100, event=PaymentEvent.CONTRACT, offset_days=10,
                                                      guarantee=GuaranteeKind.ADVANCE_RETURN, guarantee_pct=100, guarantee_months=6)]),
        ],
    )
