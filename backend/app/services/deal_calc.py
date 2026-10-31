"""Сборка входа расчётного ядра из карточки сделки и настроек; запуск расчёта; метаданные для Excel."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc import calc_deal
from ..calc.types import (
    AllocationMethod, BatchInput, DealCalcInput, ExpertPoint, FinanceSettings, FxForecastSettings, FxRates, Money,
    OverheadStep, PaymentLine, PositionInput, ScenarioName, ScenarioParams, ServiceSummary, Thresholds, VariantInput,
)
from ..models import Deal, FxRate
from . import settings_service


def load_fx(db: Session, on: date) -> tuple[FxRates, dict[str, list[tuple[date, float]]]]:
    """Курс на дату (ручной имеет приоритет) и история по валютам за 5 лет."""
    rows = db.scalars(select(FxRate).where(FxRate.on <= on).order_by(FxRate.on.desc())).all()
    rates: dict[str, float] = {"RUB": 1.0}
    source = "cbr"
    note = ""
    latest_on: date | None = None
    for r in rows:
        if r.currency in rates:
            continue
        if r.source == "manual" and r.on == on:
            rates[r.currency] = r.rate
            source = "manual"
        elif r.currency not in rates:
            rates[r.currency] = r.rate
            latest_on = max(latest_on or r.on, r.on)
    if latest_on and latest_on < on and source != "manual":
        source, note = "cbr_stale", f"курс от {latest_on:%d.%m.%Y}"
    hist_rows = db.scalars(select(FxRate).where(FxRate.on >= on - timedelta(days=365 * 5), FxRate.source == "cbr")).all()
    history: dict[str, list[tuple[date, float]]] = {}
    for r in hist_rows:
        history.setdefault(r.currency, []).append((r.on, r.rate))
    return FxRates(as_of=on, rates=rates, source=source, note=note), history


def _num(v, default: float = 0.0) -> float:
    """JSON-поля могут содержать null — в ядро передаём число."""
    return default if v is None else float(v)


def _lines(raw: list[dict]) -> list[PaymentLine]:
    return [PaymentLine(**l) for l in raw] if raw else []


def build_calc_input(db: Session, deal: Deal, calc_date: date | None = None) -> DealCalcInput:
    s = settings_service.get_all(db)
    calc_date = calc_date or date.today()
    fx, history = load_fx(db, calc_date)
    ov = deal.scenario_override or {}
    dk, dt = dict(s["scenario_dk"]), dict(s["scenario_dt"])
    dk.update(ov.get("dk", {}))
    dt.update(ov.get("dt", {}))
    scenarios = [
        ScenarioParams(name=ScenarioName.PESSIMISTIC, dk_pct=dk["pessimistic"], dt_days=dt["pessimistic"]),
        ScenarioParams(name=ScenarioName.BASE, dk_pct=0.0, dt_days=0),
        ScenarioParams(name=ScenarioName.OPTIMISTIC, dk_pct=dk["optimistic"], dt_days=dt["optimistic"]),
    ]
    fx_settings = FxForecastSettings(
        key_rate_pct=s["key_rate_pct"], pboc_rate_pct=s["pboc_rate_pct"], foreign_rate_pct=s["foreign_rates_pct"],
        history_years=s["fx_history_years"], weight_forward=s["fx_weights"]["forward"], weight_trend=s["fx_weights"]["trend"],
        weight_expert=s["fx_weights"]["expert"],
        expert_points={c: [ExpertPoint(on=date.fromisoformat(p["on"]), rate=p["rate"]) for p in pts] for c, pts in s["fx_expert_points"].items()},
        use_historical_volatility=ov.get("use_historical_volatility", s["use_historical_volatility"]),
        vol_multiplier_pessimistic=s["vol_multipliers"]["pessimistic"], vol_multiplier_optimistic=s["vol_multipliers"]["optimistic"],
    )
    finance = FinanceSettings(
        financing_rate_pct=s["financing_rate_pct"], deposit_rate_pct=s["deposit_rate_pct"], conversion_fee_pct=s["conversion_fee_pct"],
        transfer_fee_rub=s["transfer_fee_rub"], guarantee_rate_pct=s["guarantee_rate_pct"], vat_offset_days=s["vat_offset_days"],
        import_vat_in_cost=s["import_vat_in_cost"],
    )
    thresholds = Thresholds(margin_threshold_pct=s["margin_threshold_pct"], cash_gap_limit_rub=s["cash_gap_limit_rub"],
                            gd_deal_limit_rub=s["gd_deal_limit_rub"], factory_price_deviation_pct=s["price_deviation_pct"],
                            logistics_deviation_pct=s["logistics_deviation_pct"], plan_fact_deviation_pct=s["plan_fact_deviation_pct"])
    wr = s["warranty_reserve_pct_by_type"]

    positions = []
    for p in deal.positions:
        lg = p.logistics or {}
        sc = p.service_calc or {}
        service = None
        if deal.service_flag and sc.get("result"):
            res = sc["result"]
            service = ServiceSummary(cost_fx=Money(**res["total_fx_part"]), cost_rub=res["total_rub_part"], show_in_kp=sc.get("show_in_kp", False))
        positions.append(PositionInput(
            id=p.id, factory=p.factory_id, model=p.model, machine_type=p.machine_type or "", qty=p.qty, unit_price=p.unit_price,
            options_price=_num(p.options_price), currency=p.currency, production_days=p.production_days, delivery_days=p.delivery_days,
            factory_payments=_lines(p.factory_payments) or PositionInput.model_fields["factory_payments"].default_factory(),
            domestic_logistics=Money(**lg.get("domestic_logistics", {"amount": 0, "currency": "CNY"})),
            export_formalities=Money(**lg.get("export_formalities", {"amount": 0, "currency": "CNY"})),
            hs_code=lg.get("hs_code") or "", duty_pct=_num(lg.get("duty_pct")), vat_pct=_num(lg.get("vat_pct"), 20.0),
            util_fee_rub=_num(lg.get("util_fee_rub")), certification_rub=_num(lg.get("certification_rub")),
            weight_kg=_num(((p.logistics_params or {}).get("1.15") or (p.logistics_params or {}).get("1.14") or {}).get("value")) * p.qty,
            volume_m3=_num(lg.get("volume_m3")),
            warranty_reserve_pct=p.warranty_reserve_pct if p.warranty_reserve_pct is not None else wr.get(p.machine_type, wr["default"]),
            service=service, batch_id=p.batch_id,
        ))
    batches = []
    for b in deal.batches:
        it = b.items or {}
        batches.append(BatchInput(
            id=b.id, route=b.route, transport_units=b.transport_units,
            intl_freight=Money(**it.get("intl_freight", {"amount": 0, "currency": "CNY"})),
            insurance_pct=it.get("insurance_pct"), insurance_amount=Money(**it.get("insurance_amount", {"amount": 0, "currency": "CNY"})),
            broker_rub=_num(it.get("broker_rub")), rf_delivery_rub=_num(it.get("rf_delivery_rub")),
            allocation=AllocationMethod(b.allocation), transit_days=b.transit_days, customs_days=b.customs_days,
        ))
    variants = [VariantInput(
        id=v.id, name=v.name, position_ids=v.position_ids, price_rub=v.price_rub, service_in_kp=v.service_in_kp,
        client_payments=_lines(v.client_payments or deal.client_payments) or VariantInput.model_fields["client_payments"].default_factory(),
        overhead_pct_override=v.overhead_pct_override, overhead_override_comment=v.overhead_override_comment or "",
    ) for v in deal.variants]

    return DealCalcInput(
        deal_no=deal.number, calc_date=calc_date, contract_date=deal.kp_due or (calc_date + timedelta(days=14)),
        base_currency=deal.base_currency, fx=fx, fx_history=history, fx_settings=fx_settings, finance=finance,
        overhead_scale=[OverheadStep(**o) for o in s["overhead_scale"]], thresholds=thresholds, scenarios=scenarios,
        positions=positions, batches=batches, variants=variants, commissioning_days=s["commissioning_days"],
        runtime_hours_per_month=s["hours_per_month_by_type"].get("default", 400),
    )


def run_calc(db: Session, deal: Deal, calc_date: date | None = None):
    inp = build_calc_input(db, deal, calc_date)
    return inp, calc_deal(inp)


def approval_flags(result) -> dict[str, bool]:
    ap = result.approval
    return {
        "margin_below_threshold": any("ниже порога" in r for r in ap.reasons),
        "cash_gap_above_limit": any("разрыв" in r for r in ap.reasons),
        "deal_above_gd_limit": any("лимита ГД" in r for r in ap.reasons),
        "overhead_override": any("накладных" in r for r in ap.reasons),
    }


def excel_meta(deal: Deal, version_no: int, approvals: list[dict]) -> dict[str, Any]:
    meta: dict[str, Any] = {"version_no": version_no, "approvals": approvals, "target_hours": deal.target_hours or 10000}
    meta["rfq_sources"] = {p.id: (p.tech_params or {}).get("_source_file", "") for p in deal.positions}
    meta["rfq_fields"] = {p.id: {**(p.tech_params or {}), **(p.logistics_params or {})} for p in deal.positions}
    meta["rfq_fields"] = {pid: {k: v for k, v in f.items() if not k.startswith("_")} for pid, f in meta["rfq_fields"].items()}
    meta["spare_parts"] = {p.id: (p.service_calc or {}).get("spare_parts", []) for p in deal.positions}
    meta["price_references"] = [{"position_id": p.id, "factory": p.factory_id, "model": p.model, "unit_price": p.unit_price,
                                 **(p.price_reference or {})} for p in deal.positions]
    comments = []
    for p in deal.positions:
        for item, chk in ((p.logistics or {}).get("checks") or {}).items():
            comments.append({"item": item, "ref": p.id, **chk})
    for b in deal.batches:
        for item, chk in ((b.items or {}).get("checks") or {}).items():
            comments.append({"item": item, "ref": b.name, **chk})
    meta["logistics_comments"] = comments
    for p in deal.positions:
        sc = p.service_calc or {}
        if sc.get("result"):
            meta["service_rows"] = sc["result"]["rows"]
            meta["service_totals"] = {k: sc["result"][k] if not isinstance(sc["result"][k], dict) else sc["result"][k]["amount"]
                                      for k in ("commissioning_rub", "training_rub", "reserve_fx", "total_fx_part", "total_rub_part")}
            break
    return meta
