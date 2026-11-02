"""Модель себестоимости и маржинальности (раздел 7 ТЗ).

Себестоимость позиции C = Σ_{i=1..14} c_i, c_i^RUB = c_i^FX · K_сценария.
Маржа от выручки M% = (P − C)/P·100, наценка N% = (P − C)/C·100.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .calendar import WorkCalendar
from .cashflow import Flow, build_cashflow
from .fx_forecast import FxHistory, ForecastResult, forecast_rate
from .terms import client_delivery_term, production_end
from .types import (
    AllocationMethod, ApprovalNeed, BatchInput, COST_KEYS, DealCalcInput, DealCalcResult, FxComponent,
    Money, PaymentEvent, PaymentLine, PositionCost, PositionInput, ScenarioName, ScenarioParams, Sensitivity,
    VariantInput, VariantScenarioResult,
)


# ------------------------------------------------------------------ вспомогательное

class _FxCache:
    """Кэш прогнозов по (валюта, дата, фикс-курс) + список компонентов для листа «Курсы»."""

    def __init__(self, inp: DealCalcInput):
        self.inp = inp
        self.hist = {c: FxHistory(p) for c, p in inp.fx_history.items()}
        self._cache: dict[tuple, ForecastResult] = {}
        self.components: dict[tuple, FxComponent] = {}

    def get(self, currency: str, on: date, ref: str, fixed: Optional[float] = None) -> ForecastResult:
        key = (currency, on, fixed)
        if key not in self._cache:
            fr = forecast_rate(currency, on, self.inp.fx, self.inp.fx_settings,
                               self.hist.get(currency), self.inp.scenarios, fixed)
            self._cache[key] = fr
            if currency != "RUB" and not ref.startswith("_"):
                self.components[key] = FxComponent(
                    payment_ref=ref, currency=currency, on=on, t_days=fr.t_days, k_cbr=fr.k_cbr,
                    comp_a_forward=round(fr.a, 4), comp_b_trend=round(fr.b, 4), comp_c_season=round(fr.c, 4),
                    season_spread=(round(fr.c_spread[0], 4), round(fr.c_spread[1], 4)),
                    comp_g_expert=fr.g, weights=tuple(round(w, 3) for w in fr.weights),
                    forecast=round(fr.forecast, 4), dk_pct=fr.dk_pct,
                    rate_by_scenario={k: round(v, 4) for k, v in fr.rate_by_scenario.items()}, fixed=fr.fixed,
                )
        return self._cache[key]

    def rate(self, currency: str, on: date, scenario: ScenarioName, ref: str,
             fixed: Optional[float] = None, mult: float = 1.0) -> float:
        if currency == "RUB":
            return 1.0
        return self.get(currency, on, ref, fixed).rate_by_scenario[scenario.value] * mult


def _calendar(inp: DealCalcInput) -> WorkCalendar:
    return WorkCalendar(holidays=inp.rf_holidays)


def _advance_offset(lines: list[PaymentLine], events: tuple[PaymentEvent, ...], cal: WorkCalendar, base: date) -> date:
    """Дата первого платежа с указанным событием-основанием (аванс клиента / предоплата заводу)."""
    for l in lines:
        if l.event in events:
            return cal.add(base, l.offset_days, l.offset_kind)
    return base


def deal_events(inp: DealCalcInput, variant: Optional[VariantInput], cal: WorkCalendar) -> dict[PaymentEvent, date]:
    """События уровня сделки: контракт с клиентом, аванс клиента, аккредитив, контракт с заводом."""
    act = inp.deal_actual_events
    contract = act.get("contract") or inp.contract_date
    lines = variant.client_payments if variant else []
    client_prepayment = act.get("client_prepayment") or _advance_offset(lines, (PaymentEvent.CONTRACT, PaymentEvent.CLIENT_PREPAYMENT), cal, contract)
    lc_opened = act.get("lc_opened") or (contract + timedelta(days=inp.lc_opening_days))
    factory_contract = act.get("factory_contract") or inp.factory_contract_date or (contract + timedelta(days=inp.factory_contract_lag_days))
    return {PaymentEvent.CONTRACT: contract, PaymentEvent.CLIENT_PREPAYMENT: client_prepayment,
            PaymentEvent.LC_OPENED: lc_opened, PaymentEvent.FACTORY_CONTRACT: factory_contract}


def _events(pos: PositionInput, batch: Optional[BatchInput], inp: DealCalcInput, dt_days: int,
            base: dict[PaymentEvent, date], cal: WorkCalendar) -> dict[PaymentEvent, date]:
    """Плановые даты событий позиции в сценарии (Δt удлиняет производство); фактические даты имеют приоритет
    и сдвигают последующие события (задача 2)."""
    pa = {PaymentEvent(k): v for k, v in pos.actual_events.items() if k in PaymentEvent._value2member_map_}
    ba = {PaymentEvent(k): v for k, v in (batch.actual_events if batch else {}).items() if k in PaymentEvent._value2member_map_}
    ev: dict[PaymentEvent, date] = dict(base)
    ev[PaymentEvent.FACTORY_PREPAYMENT] = pa.get(PaymentEvent.FACTORY_PREPAYMENT) or _advance_offset(
        pos.factory_payments, (PaymentEvent.FACTORY_CONTRACT, PaymentEvent.CONTRACT, PaymentEvent.FACTORY_PREPAYMENT), cal, ev[PaymentEvent.FACTORY_CONTRACT])
    start_event = PaymentEvent.FACTORY_PREPAYMENT if (pos.production_term and pos.production_term.start_event == "factory_prepayment") else PaymentEvent.FACTORY_CONTRACT
    start = ev[start_event]
    ev[PaymentEvent.READINESS] = pa.get(PaymentEvent.READINESS) or production_end(pos.production_term, pos.production_days, start, cal, dt_days)
    transit = batch.transit_days if batch else 14
    customs = batch.customs_days if batch else 5
    rf_days = batch.rf_delivery_days if batch else 5
    ev[PaymentEvent.SHIPMENT] = pa.get(PaymentEvent.SHIPMENT) or ba.get(PaymentEvent.SHIPMENT) or (ev[PaymentEvent.READINESS] + timedelta(days=pos.delivery_days))
    ev[PaymentEvent.BORDER] = (pa.get(PaymentEvent.BORDER) or ba.get(PaymentEvent.BORDER)
                               or (batch.border_planned if batch and batch.border_planned and batch.border_planned >= ev[PaymentEvent.SHIPMENT] else None)
                               or (ev[PaymentEvent.SHIPMENT] + timedelta(days=transit)))
    ev[PaymentEvent.DECLARATION] = pa.get(PaymentEvent.DECLARATION) or ba.get(PaymentEvent.DECLARATION) or (ev[PaymentEvent.BORDER] + timedelta(days=customs))
    install_before = sum(o.install_days for o in pos.local_options if o.needs_install and o.install_stage == "before_handover")
    ev[PaymentEvent.DELIVERY] = pa.get(PaymentEvent.DELIVERY) or ba.get(PaymentEvent.DELIVERY) or (ev[PaymentEvent.DECLARATION] + timedelta(days=rf_days + install_before))
    ev[PaymentEvent.COMMISSIONING] = pa.get(PaymentEvent.COMMISSIONING) or (ev[PaymentEvent.DELIVERY] + timedelta(days=inp.commissioning_days))
    ev[PaymentEvent.RUNTIME] = ev[PaymentEvent.COMMISSIONING]
    return ev


def _payment_date(line: PaymentLine, events: dict[PaymentEvent, date], inp: DealCalcInput, cal: WorkCalendar) -> date:
    base = events[line.event]
    if line.event == PaymentEvent.RUNTIME and line.runtime_hours:
        months = line.runtime_hours / max(inp.runtime_hours_per_month, 1)
        base = base + timedelta(days=int(months * 30.44))
    return cal.add(base, line.offset_days, line.offset_kind)


ADVANCE_EVENTS = {PaymentEvent.CONTRACT, PaymentEvent.CLIENT_PREPAYMENT, PaymentEvent.LC_OPENED}


def _line_amount(line: PaymentLine, total: float) -> float:
    if line.amount is not None:
        return line.amount
    return total * (line.share_pct or 0.0) / 100.0


def _alloc_weights(positions: list[PositionInput], method: AllocationMethod, fx_rate: dict[str, float]) -> dict[str, float]:
    if method == AllocationMethod.WEIGHT:
        raw = {p.id: p.weight_kg for p in positions}
    elif method == AllocationMethod.VOLUME:
        raw = {p.id: p.volume_m3 for p in positions}
    else:
        raw = {p.id: (p.unit_price * p.qty + p.options_price) * fx_rate.get(p.currency, 1.0) for p in positions}
    total = sum(raw.values())
    if total <= 0:
        n = len(positions) or 1
        return {p.id: 1.0 / n for p in positions}
    return {k: v / total for k, v in raw.items()}


def _overhead_pct(direct_rub: float, inp: DealCalcInput) -> float:
    for step in inp.overhead_scale:
        if step.up_to_rub is None or direct_rub <= step.up_to_rub:
            return step.pct
    return inp.overhead_scale[-1].pct if inp.overhead_scale else 0.0


# ------------------------------------------------------------------ расчёт варианта в сценарии

def _variant_scenario(inp: DealCalcInput, variant: VariantInput, scen: ScenarioParams, fx: _FxCache,
                      price_override: Optional[float] = None, fx_mult: float = 1.0) -> VariantScenarioResult:
    s = scen.name
    pos_by_id = {p.id: p for p in inp.positions}
    batches = {b.id: b for b in inp.batches}
    positions = [pos_by_id[pid] for pid in variant.position_ids]
    price = variant.price_rub if price_override is None else price_override

    # распределение общих статей партий
    batch_members: dict[str, list[PositionInput]] = {}
    for p in positions:
        if p.batch_id:
            batch_members.setdefault(p.batch_id, []).append(p)

    flows: list[Flow] = []
    pos_costs: list[PositionCost] = []
    items_fx_all: dict[str, dict[str, Money]] = {}
    delivery_dates: list[date] = []
    events_by_pos: dict[str, dict[PaymentEvent, date]] = {}
    cal = _calendar(inp)
    base_ev = deal_events(inp, variant, cal)

    for p in positions:
        batch = batches.get(p.batch_id) if p.batch_id else None
        ev = _events(p, batch, inp, scen.dt_days, base_ev, cal)
        events_by_pos[p.id] = ev
        delivery_dates.append(ev[PaymentEvent.DELIVERY])
        items: dict[str, float] = {k: 0.0 for k in COST_KEYS}
        items_fx: dict[str, Money] = {}

        # 1. Цена завода — по графику платежей заводу, курс прогнозный на дату каждого платежа
        total_fx = p.unit_price * p.qty + p.options_price
        items_fx["factory_price"] = Money(amount=total_fx, currency=p.currency)
        for line in p.factory_payments:
            on = _payment_date(line, ev, inp, cal)
            amt = _line_amount(line, total_fx)
            k = fx.rate(p.currency, on, s, f"{p.id}/завод №{line.no}", line.fixed_rate, fx_mult)
            rub = amt * k
            items["factory_price"] += rub
            flows.append(Flow(on=on, amount_rub=-rub, kind="factory", ref=f"{p.id}/{line.no}", fx_amount=amt, currency=p.currency))

        ship = ev[PaymentEvent.SHIPMENT]
        # 2–3. Логистика позиции (оплата при отгрузке)
        for key, m in (("domestic_logistics", p.domestic_logistics), ("export_formalities", p.export_formalities)):
            items_fx[key] = m
            k = fx.rate(m.currency, ship, s, f"{p.id}/{key}", None, fx_mult)
            items[key] = m.amount * k
            if m.amount:
                flows.append(Flow(on=ship, amount_rub=-items[key], kind="logistics", ref=key, fx_amount=m.amount, currency=m.currency))

        # 4–5, 8, 10. Статьи партии → доля позиции
        share = 1.0
        if batch:
            k0 = {c: fx.rate(c, ship, s, "_alloc", None, fx_mult) for c in ("CNY", "USD", "EUR", "RUB")}
            share = _alloc_weights(batch_members[batch.id], batch.allocation, k0)[p.id]
            fr = batch.intl_freight
            items_fx["intl_freight"] = Money(amount=fr.amount * share, currency=fr.currency)
            k = fx.rate(fr.currency, ship, s, f"{batch.id}/freight", None, fx_mult)
            items["intl_freight"] = fr.amount * share * k
            if batch.insurance_pct is not None:
                ins_fx = total_fx * batch.insurance_pct / 100.0
                items_fx["insurance"] = Money(amount=ins_fx, currency=p.currency)
                items["insurance"] = ins_fx * fx.rate(p.currency, ship, s, f"{batch.id}/ins", None, fx_mult)
            else:
                ia = batch.insurance_amount
                items_fx["insurance"] = Money(amount=ia.amount * share, currency=ia.currency)
                items["insurance"] = ia.amount * share * fx.rate(ia.currency, ship, s, f"{batch.id}/ins", None, fx_mult)
            items["broker_util"] = batch.broker_rub * share
            items["rf_delivery"] = batch.rf_delivery_rub * share
            for key in ("intl_freight", "insurance"):
                m = items_fx[key]
                if m.amount:
                    flows.append(Flow(on=ship, amount_rub=-items[key], kind="logistics", ref=key, fx_amount=m.amount, currency=m.currency))
        items["broker_util"] += p.util_fee_rub

        # 6–7. Таможня от таможенной стоимости (п.1–5 в RUB на дату оформления)
        decl = ev[PaymentEvent.DECLARATION]
        customs_value = 0.0
        for key in ("factory_price", "domestic_logistics", "export_formalities", "intl_freight", "insurance"):
            m = items_fx.get(key)
            if m and m.amount:
                customs_value += m.amount * fx.rate(m.currency, decl, s, f"{p.id}/таможенная стоимость", None, fx_mult)
        duty = customs_value * p.duty_pct / 100.0
        vat = (customs_value + duty) * p.vat_pct / 100.0
        items["customs_duty"] = duty
        items["import_vat"] = vat if inp.finance.import_vat_in_cost else 0.0
        flows.append(Flow(on=decl, amount_rub=-(duty + items["broker_util"]), kind="customs", ref=f"{p.id}/duty+broker"))
        if vat:
            flows.append(Flow(on=decl, amount_rub=-vat, kind="customs", ref=f"{p.id}/vat"))
            if not inp.finance.import_vat_in_cost:
                flows.append(Flow(on=decl + timedelta(days=inp.finance.vat_offset_days), amount_rub=vat, kind="vat_refund", ref=f"{p.id}/vat"))

        # 9. Сертификация (до ввоза), 10. Доставка по РФ (при поставке)
        items["certification"] = p.certification_rub
        if p.certification_rub:
            flows.append(Flow(on=ev[PaymentEvent.READINESS], amount_rub=-p.certification_rub, kind="other", ref="cert"))
        if items["rf_delivery"]:
            flows.append(Flow(on=ev[PaymentEvent.DELIVERY], amount_rub=-items["rf_delivery"], kind="logistics", ref="rf"))

        # 12. Сервис до наработки
        if p.service:
            sv = p.service
            k = fx.rate(sv.cost_fx.currency, ev[PaymentEvent.DELIVERY], s, f"{p.id}/service", None, fx_mult)
            items_fx["service"] = sv.cost_fx
            items["service"] = sv.cost_fx.amount * k + sv.cost_rub
            if items["service"]:
                flows.append(Flow(on=ev[PaymentEvent.COMMISSIONING], amount_rub=-items["service"], kind="service", ref=p.id,
                                  fx_amount=sv.cost_fx.amount, currency=sv.cost_fx.currency))

        # 13. Гарантийный резерв — % от п.1
        items["warranty_reserve"] = items["factory_price"] * p.warranty_reserve_pct / 100.0

        # 15. Доукомплектование в РФ (задача 6): закупка + монтаж; отток на плановую дату заказа
        for o in p.local_options:
            needed_by = ev[PaymentEvent.DECLARATION] if o.install_stage == "before_handover" else ev[PaymentEvent.COMMISSIONING]
            order_on = needed_by - timedelta(days=o.supplier_lead_days + inp.finance.local_options_order_reserve_days)
            order_on = max(order_on, ev[PaymentEvent.CONTRACT])
            k = fx.rate(o.currency, order_on, s, f"{p.id}/опция РФ {o.name}", None, fx_mult)
            purchase = o.cost * o.qty * k
            items["local_completion"] += purchase + o.install_cost_rub
            if purchase:
                flows.append(Flow(on=order_on, amount_rub=-purchase, kind="other", ref=f"{p.id}/local/{o.id}",
                                  fx_amount=o.cost * o.qty if o.currency != "RUB" else 0.0, currency=o.currency))
            if o.install_cost_rub:
                flows.append(Flow(on=needed_by, amount_rub=-o.install_cost_rub, kind="other", ref=f"{p.id}/install/{o.id}"))

        # срок поставки клиенту (задача 3): от точки отсчёта клиента до поставки, с резервом и округлением
        start_map = {"client_contract": PaymentEvent.CONTRACT, "client_prepayment": PaymentEvent.CLIENT_PREPAYMENT, "lc_opened": PaymentEvent.LC_OPENED}
        install_before = sum(o.install_days for o in p.local_options if o.needs_install and o.install_stage == "before_handover")
        dterm = client_delivery_term(p.delivery_term, ev[start_map[p.delivery_term.start_event]], ev[PaymentEvent.DELIVERY],
                                     inp.delivery_reserve_pct, cal, install_before)

        pos_costs.append(PositionCost(position_id=p.id, scenario=s, items_rub=items, items_fx=items_fx,
                                      customs_value_rub=round(customs_value, 2), total_rub=0.0,
                                      events={e.value: d for e, d in ev.items()}, delivery_term=dterm))
        items_fx_all[p.id] = items_fx

    # --- уровень варианта: притоки клиента, стоимость денег, накладные
    direct = {k: sum(pc.items_rub[k] for pc in pos_costs) for k in COST_KEYS}
    var_events = {
        e: max(ev[e] for ev in events_by_pos.values()) for e in PaymentEvent
    } if events_by_pos else {}
    guarantee_cost = 0.0
    payment_dates: list[dict] = []
    if price is not None and var_events:
        ag = inp.finance.advance_guarantee
        for line in variant.client_payments:
            on = _payment_date(line, var_events, inp, cal)
            amt = _line_amount(line, price)
            flows.append(Flow(on=on, amount_rub=amt, kind="client", ref=f"client/{line.no}"))
            payment_dates.append({"no": line.no, "event": line.event.value, "on": on, "amount_rub": round(amt, 2), "via": line.via,
                                  "default": variant.client_payments_default})
            is_advance = line.event in ADVANCE_EVENTS or on < var_events[PaymentEvent.SHIPMENT]
            if line.guarantee.value != "none" and line.guarantee_pct:
                # явная гарантия в строке графика (раздел 9): % годовых × срок гарантии
                guarantee_cost += amt * line.guarantee_pct / 100.0 * inp.finance.guarantee_rate_pct / 100.0 * max(line.guarantee_months, 1) / 12.0
            elif ag.enabled and is_advance and amt > 0:
                # БГ на аванс (задача 1): разовый %, годовой % × срок до поставки клиенту, либо фиксированная сумма
                if ag.mode == "one_time_pct":
                    guarantee_cost += amt * ag.value / 100.0
                elif ag.mode == "annual_pct":
                    days = max((var_events[PaymentEvent.DELIVERY] - on).days, 0)
                    guarantee_cost += amt * ag.value / 100.0 * days / 365.0
                else:
                    guarantee_cost += ag.value
    if guarantee_cost and var_events:
        flows.append(Flow(on=var_events[PaymentEvent.CONTRACT], amount_rub=-guarantee_cost, kind="other", ref="guarantee"))

    cf = build_cashflow(flows, inp.finance, guarantee_cost)
    direct["finance"] = cf.total_finance_rub
    direct_sum_1_13 = sum(direct[k] for k in COST_KEYS if k != "overhead")
    oh_pct = variant.overhead_pct_override if variant.overhead_pct_override is not None else _overhead_pct(direct_sum_1_13, inp)
    direct["overhead"] = direct_sum_1_13 * oh_pct / 100.0
    cost_total = sum(direct.values())

    # распределение статей 11 и 14 и цены по позициям пропорционально прямой себестоимости
    pos_direct = {pc.position_id: sum(v for k, v in pc.items_rub.items() if k not in ("finance", "overhead")) for pc in pos_costs}
    pd_total = sum(pos_direct.values()) or 1.0
    for pc in pos_costs:
        w = pos_direct[pc.position_id] / pd_total
        pc.items_rub["finance"] = direct["finance"] * w
        pc.items_rub["overhead"] = direct["overhead"] * w
        pc.items_rub = {k: round(v, 2) for k, v in pc.items_rub.items()}
        pc.total_rub = round(sum(pc.items_rub.values()), 2)
        pc.price_rub = round(price * w, 2) if price is not None else None

    margin = markup = profit = profit_bf = None
    if price:
        profit = price - cost_total
        profit_bf = profit + direct["finance"]
        margin = profit / price * 100.0
        markup = profit / cost_total * 100.0 if cost_total else None

    base_k = fx.rate(inp.base_currency, inp.fx.as_of, s, "_base", None, fx_mult) if inp.base_currency != "RUB" else 1.0
    return VariantScenarioResult(
        variant_id=variant.id, scenario=s,
        items_rub={k: round(v, 2) for k, v in direct.items()},
        cost_total_rub=round(cost_total, 2),
        cost_total_base_ccy=round(cost_total / base_k, 2),
        price_rub=price, margin_pct=None if margin is None else round(margin, 3),
        markup_pct=None if markup is None else round(markup, 3),
        profit_rub=None if profit is None else round(profit, 2),
        profit_before_finance_rub=None if profit_bf is None else round(profit_bf, 2),
        cash_gap=cf.gap, cashflow=cf.points, finance_breakdown=cf.breakdown(),
        delivery_days=(max(delivery_dates) - inp.contract_date).days if delivery_dates else 0,
        positions=pos_costs, payment_dates=payment_dates, guarantee_cost_rub=round(guarantee_cost, 2),
        defaults_used={"client_payments": variant.client_payments_default,
                       "factory_payments": any(p.factory_payments_default for p in positions)},
    )


def _sensitivity(inp: DealCalcInput, variant: VariantInput, scen: ScenarioParams, fx: _FxCache,
                 base: VariantScenarioResult) -> Optional[Sensitivity]:
    if not base.price_rub:
        return None
    up = _variant_scenario(inp, variant, scen, fx, fx_mult=1.01)
    d_margin = (up.margin_pct or 0) - (base.margin_pct or 0)
    d_rub = (up.profit_rub or 0) - (base.profit_rub or 0)
    # линейная экстраполяция: при каком множителе курса маржа = порог
    thr = inp.thresholds.margin_threshold_pct
    factor = None
    if abs(d_margin) > 1e-9:
        factor = 1.0 + ((thr - (base.margin_pct or 0)) / d_margin) * 0.01
    k_base = inp.fx.rate(inp.base_currency) if inp.base_currency != "RUB" else 1.0
    return Sensitivity(
        margin_delta_pct_per_1pct_fx=round(d_margin, 4),
        margin_delta_rub_per_1pct_fx=round(d_rub, 2),
        breakeven_fx_factor=None if factor is None else round(factor, 4),
        breakeven_rate_base_ccy=None if factor is None else round(k_base * factor, 4),
    )


# ------------------------------------------------------------------ публичный API ядра

def calc_deal(inp: DealCalcInput) -> DealCalcResult:
    fx = _FxCache(inp)
    results: dict[str, dict[str, VariantScenarioResult]] = {}
    warnings: list[str] = []
    approval = ApprovalNeed()
    thr = inp.thresholds
    if inp.fx.source == "cbr_stale":
        warnings.append(f"Курс ЦБ недоступен: использован последний загруженный курс ({inp.fx.note}).")
    if inp.fx.source == "manual":
        warnings.append("Используется курс, введённый вручную администратором.")

    for v in inp.variants:
        results[v.id] = {}
        for scen in inp.scenarios:
            r = _variant_scenario(inp, v, scen, fx)
            if scen.name == ScenarioName.BASE:
                r.sensitivity = _sensitivity(inp, v, scen, fx, r)
            results[v.id][scen.name.value] = r
        base = results[v.id][ScenarioName.BASE.value]
        pess = results[v.id].get(ScenarioName.PESSIMISTIC.value)
        if base.margin_pct is not None and base.margin_pct < thr.margin_threshold_pct:
            approval.financial = True
            approval.reasons.append(f"Вариант «{v.name}»: маржа базового сценария {base.margin_pct:.2f} % ниже порога {thr.margin_threshold_pct} %.")
        if pess and pess.margin_pct is not None and pess.margin_pct < 0:
            warnings.append(f"Вариант «{v.name}»: маржа пессимистичного сценария отрицательна ({pess.margin_pct:.2f} %).")
        if thr.cash_gap_limit_rub is not None and -base.cash_gap.amount_rub > thr.cash_gap_limit_rub:
            approval.financial = True
            approval.reasons.append(f"Вариант «{v.name}»: кассовый разрыв {-base.cash_gap.amount_rub:,.0f} RUB выше лимита ФД.")
        if thr.gd_deal_limit_rub is not None and base.price_rub and base.price_rub > thr.gd_deal_limit_rub:
            approval.general = True
            approval.reasons.append(f"Вариант «{v.name}»: сумма сделки выше лимита ГД.")
        if v.overhead_pct_override is not None:
            approval.general = True
            approval.reasons.append(f"Вариант «{v.name}»: КД переопределил процент накладных ({v.overhead_pct_override} %).")

    return DealCalcResult(
        deal_no=inp.deal_no, calc_date=inp.calc_date, base_currency=inp.base_currency, fx=inp.fx,
        fx_components=sorted(fx.components.values(), key=lambda c: (c.currency, c.on)),
        scenarios=inp.scenarios, variants=results, approval=approval, warnings=warnings,
    )


def solve_price(inp: DealCalcInput, variant_id: str, target_margin_pct: Optional[float] = None,
                target_markup_pct: Optional[float] = None, iterations: int = 8) -> float:
    """Цена по целевой марже или наценке (базовый сценарий).

    Стоимость денег зависит от графика притоков, поэтому решается итерационно (сходится за 3–5 шагов).
    """
    v = next(x for x in inp.variants if x.id == variant_id)
    scen = next(s for s in inp.scenarios if s.name == ScenarioName.BASE)
    fx = _FxCache(inp)
    cost = _variant_scenario(inp, v, scen, fx, price_override=0.0).cost_total_rub
    price = cost
    for _ in range(iterations):
        if target_margin_pct is not None:
            price = cost / (1 - target_margin_pct / 100.0)
        elif target_markup_pct is not None:
            price = cost * (1 + target_markup_pct / 100.0)
        cost = _variant_scenario(inp, v, scen, fx, price_override=price).cost_total_rub
    return round(price, 2)
