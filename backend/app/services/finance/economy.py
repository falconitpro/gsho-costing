"""Экономика сделки (docs/finance/01, раздел 9; 02, раздел 9; 04 — GET /api/finance/deals/{id}/economy; D-134…D-137).

    План       = утверждённая DealVersion: базовый / пессимистичный / оптимистичный сценарии (расчётный движок не дублируется)
    Прогноз    = факт признания + непризнанный остаток плана; выручка — активные обязательства клиента; cash — факт + остатки обязательств
    Факт P&L   = posted FinanceRecognitionEvent (+ старые факты по статьям, ещё не перенесённые в события — без двойного учёта)
    Факт cash  = posted FinanceTransaction через confirmed FinanceAllocation на обязательства сделки

Поступление денег выручкой не является, оплата поставщику — расходом. Кассовый разрыв факта считается по датам проведённых операций;
если подтверждённых операций нет или их нельзя выразить в рублях — `status="insufficient"` и перечень, что разнести.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...calc.types import COST_ITEMS
from ...models import DealVersion, FinanceAllocation, FinanceObligation, FinanceRecognitionEvent, FinanceTransaction, PlanFactEntry
from .money import ZERO, dec, s

SCEN = ("base", "pessimistic", "optimistic")


def _f(v) -> Optional[float]:
    return None if v is None else float(v)


def _gap(points: list[tuple[date, Decimal]]) -> dict:
    """Минимум накопленного сальдо: сумма (≤ 0), дата начала, длительность в днях — как CashGap расчётного движка."""
    bal, low, low_on, start, longest, cur_start = ZERO, ZERO, None, None, 0, None
    pts = sorted(points, key=lambda p: p[0])
    days: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for on, v in pts:
        days[on] += v
    ordered = sorted(days.items())
    for i, (on, v) in enumerate(ordered):
        bal += v
        if bal < 0 and cur_start is None:
            cur_start = on
        if bal < low:
            low, low_on, start = bal, on, cur_start
        if bal >= 0 and cur_start is not None:
            longest = max(longest, (on - cur_start).days)
            cur_start = None
    if cur_start is not None and ordered:
        longest = max(longest, (ordered[-1][0] - cur_start).days)
    return {"amount_rub": s(low), "starts_on": start.isoformat() if start else None, "low_on": low_on.isoformat() if low_on else None, "days": longest}


def recognized(db: Session, deal_id: str) -> dict:
    """Факт P&L сделки: выручка и расход по статьям в RUB (только posted), плюс старые факты по статьям без события."""
    evs = db.scalars(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.deal_id == deal_id)).all()
    revenue, by_item, n_rev, n_exp = ZERO, defaultdict(lambda: ZERO), 0, 0
    linked = {e.source_id for e in evs if e.source_type == "planfact"}
    for e in evs:
        if e.status != "posted":
            continue
        if e.kind == "revenue":
            revenue += e.amount_rub
            n_rev += 1
        elif e.cost_item_code:
            by_item[e.cost_item_code] += e.amount_rub
            n_exp += 1
    pending_legacy = 0
    for p in db.scalars(select(PlanFactEntry).where(PlanFactEntry.deal_id == deal_id, PlanFactEntry.kind == "cost_item")).all():
        if p.id in linked or not p.cost_item_code or not p.amount:
            continue
        rub = dec(p.amount) if (p.currency or "RUB") == "RUB" else dec(Decimal(str(p.amount)) * Decimal(str(p.fx_rate or 1)))
        by_item[p.cost_item_code] += rub
        pending_legacy += 1
    return {"revenue_rub": revenue if n_rev else None, "items_rub": dict(by_item), "events": len(evs), "revenue_events": n_rev, "expense_events": n_exp,
            "legacy_not_migrated": pending_legacy}


def _rub_rate(db: Session, currency: str, on: date) -> Optional[Decimal]:
    from .liquidity import fx_rate
    r = fx_rate(db, currency, on)
    return Decimal(r["rate"]) if r else None


def cash(db: Session, vis, deal_id: str, today: date) -> dict:
    """Факт и прогноз движения денег по обязательствам сделки (только видимые пользователю обязательства)."""
    obs = [o for o in db.scalars(select(FinanceObligation).where(FinanceObligation.deal_id == deal_id)).all() if vis.obligation(o)]
    ids = [o.id for o in obs]
    by_id = {o.id: o for o in obs}
    allocs = db.execute(select(FinanceAllocation, FinanceTransaction).join(FinanceTransaction, FinanceTransaction.id == FinanceAllocation.transaction_id)
                        .where(FinanceAllocation.obligation_id.in_(ids), FinanceAllocation.status.in_(("confirmed", "provisional")))).all() if ids else []
    fact_pts: list[tuple[date, Decimal]] = []
    by_ccy: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"in": ZERO, "out": ZERO})
    rub_in, rub_out, provisional, no_rate = ZERO, ZERO, [], []
    confirmed_paid: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for a, tx in allocs:
        o = by_id[a.obligation_id]
        if a.status != "confirmed" or tx.status != "posted":
            provisional.append({"obligation_id": o.id, "title": o.title, "amount": s(a.amount_obligation), "currency": o.currency})
            continue
        confirmed_paid[o.id] += a.amount_obligation
        on = tx.value_date or tx.booked_at.date()
        by_ccy[tx.currency][tx.direction] += a.amount_tx
        if tx.currency == "RUB":
            rub = a.amount_tx
        elif o.currency == "RUB":
            rub = a.amount_obligation
        else:
            r = _rub_rate(db, tx.currency, on)
            if r is None:
                no_rate.append({"transaction_id": tx.id, "currency": tx.currency, "on": on.isoformat()})
                continue
            rub = dec(a.amount_tx * r)
        fact_pts.append((on, rub if tx.direction == "in" else -rub))
        if tx.direction == "in":
            rub_in += rub
        else:
            rub_out += rub
    gaps: list[dict] = []
    has_in = any(v > 0 for _, v in fact_pts)
    has_out = any(v < 0 for _, v in fact_pts)
    if not fact_pts:
        gaps.append({"code": "no_confirmed_cash", "text": "Нет подтверждённых операций по обязательствам сделки — разнесите банковские поступления и оплаты в «Финансы → Платёжный календарь»"})
    elif not has_out:
        gaps.append({"code": "no_outflows", "text": "Нет подтверждённых оплат поставщикам — разрыв по факту может быть занижен"})
    elif not has_in:
        gaps.append({"code": "no_inflows", "text": "Нет подтверждённых поступлений от клиента"})
    if provisional:
        gaps.append({"code": "provisional", "text": f"Ждут подтверждения разнесения: {len(provisional)} — в факт не включены", "rows": provisional[:20]})
    if no_rate:
        gaps.append({"code": "no_rate", "text": f"Нет курса ЦБ на дату операции: {len(no_rate)} — операции не выражены в рублях", "rows": no_rate[:20]})
    overdue_unpaid = [o for o in obs if o.is_active and (o.legal_due_on or o.forecast_on) and (o.legal_due_on or o.forecast_on) <= today
                      and confirmed_paid[o.id] < o.amount and o.nature != "penalty"]
    if overdue_unpaid and not fact_pts:
        gaps[0]["rows"] = [{"obligation_id": o.id, "title": o.title, "due_on": (o.legal_due_on or o.forecast_on).isoformat(), "amount": s(o.amount - confirmed_paid[o.id]), "currency": o.currency}
                           for o in overdue_unpaid[:20]]
    fact_status = "ok" if (fact_pts and not no_rate) else ("partial" if fact_pts else "insufficient")
    fact_gap = _gap(fact_pts) if fact_pts else None
    # текущий прогноз: факт + непогашенные остатки активных обязательств по их сроку (юридическому, иначе прогнозному)
    fc_pts = list(fact_pts)
    fc_missing = []
    for o in obs:
        if not o.is_active or o.nature == "penalty":
            continue
        rest = o.amount - confirmed_paid[o.id]
        if rest <= 0:
            continue
        on = o.legal_due_on or o.forecast_on
        if on is None:
            fc_missing.append(o.title)
            continue
        on = max(on, today)
        rub = rest if o.currency == "RUB" else (lambda r: None if r is None else dec(rest * r))(_rub_rate(db, o.currency, today))
        if rub is None:
            fc_missing.append(o.title)
            continue
        fc_pts.append((on, rub if o.direction == "in" else -rub))
    return {"fact": {"status": fact_status, "in_rub": s(rub_in) if fact_pts else None, "out_rub": s(rub_out) if fact_pts else None,
                     "net_rub": s(rub_in - rub_out) if fact_pts else None, "by_currency": {c: {"in": s(v["in"]), "out": s(v["out"])} for c, v in sorted(by_ccy.items())},
                     "gap": fact_gap},
            "forecast_gap": _gap(fc_pts) if fc_pts else None, "forecast_missing": fc_missing[:20], "data_gaps": gaps, "obligations": obs,
            "confirmed_paid": confirmed_paid}


def _revenue_forecast(db: Session, obs: list[FinanceObligation], today: date) -> tuple[Optional[Decimal], list[str]]:
    total, miss, n = ZERO, [], 0
    for o in obs:
        if not o.is_active or o.direction != "in" or o.nature == "penalty":
            continue
        if o.currency == "RUB":
            total += o.amount
        else:
            r = _rub_rate(db, o.currency, today)
            if r is None:
                miss.append(f"{o.title}: нет курса {o.currency}")
                continue
            total += dec(o.amount * r)
        n += 1
    return (total if n else None), miss


def build(db: Session, vis, deal, today: Optional[date] = None) -> dict:
    today = today or date.today()
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    if ver is None:
        raise HTTPException(409, "Нет утверждённой версии расчёта — экономика сделки строится от неё")
    from ..execution_service import won_variant
    v = won_variant(deal)
    if v is None or v.id not in (ver.calc_result or {}).get("variants", {}):
        raise HTTPException(409, "Нет выбранного варианта в утверждённой версии")
    res = ver.calc_result["variants"][v.id]
    plan = {sc: res.get(sc) for sc in SCEN}
    fact = recognized(db, deal.id)
    cv = cash(db, vis, deal.id, today)
    rev_fc, rev_miss = _revenue_forecast(db, cv["obligations"], today)

    items = []
    fc_cost = Decimal(0)
    for no, key, title in COST_ITEMS:
        pb = Decimal(str((plan["base"] or {}).get("items_rub", {}).get(key, 0) or 0))
        fa = fact["items_rub"].get(key)
        fc = (fa or ZERO) + max(pb - (fa or ZERO), ZERO)
        fc_cost += fc
        dev = None if fa is None else fa - pb
        items.append({"no": no, "code": key, "title": title,
                      "plan": {sc: _f((plan[sc] or {}).get("items_rub", {}).get(key)) if plan[sc] else None for sc in SCEN},
                      "forecast": float(dec(fc)), "fact": _f(fa), "deviation_rub": _f(dev),
                      "deviation_pct": None if dev is None or not pb else round(float(dev / pb * 100), 2)})
    cost_fact = sum(fact["items_rub"].values(), ZERO) if fact["items_rub"] else None
    rev_fact = fact["revenue_rub"]

    def kpi(sc):
        p = plan[sc]
        if not p:
            return None
        return {"revenue": p.get("price_rub"), "cost": p.get("cost_total_rub"), "profit": p.get("profit_rub"), "margin_pct": p.get("margin_pct"),
                "markup_pct": p.get("markup_pct"), "money_cost": (p.get("finance_breakdown") or {}).get("money_cost", (p.get("items_rub") or {}).get("finance")),
                "cash_gap": p.get("cash_gap")}

    rev_fc_v = rev_fc if rev_fc is not None else (Decimal(str(plan["base"]["price_rub"])) if plan["base"] and plan["base"].get("price_rub") else None)
    fc_profit = None if rev_fc_v is None else rev_fc_v - fc_cost
    forecast = {"revenue": _f(rev_fc_v), "revenue_source": "obligations" if rev_fc is not None else "plan", "cost": float(dec(fc_cost)),
                "profit": _f(fc_profit), "margin_pct": None if not rev_fc_v or fc_profit is None else round(float(fc_profit / rev_fc_v * 100), 2)}
    pnl_profit = None if (rev_fact is None or cost_fact is None) else rev_fact - cost_fact
    pnl = {"revenue": _f(rev_fact), "cost": _f(cost_fact), "profit": _f(pnl_profit),
           "margin_pct": None if not rev_fact or pnl_profit is None else round(float(pnl_profit / rev_fact * 100), 2),
           "revenue_events": fact["revenue_events"], "expense_events": fact["expense_events"], "legacy_not_migrated": fact["legacy_not_migrated"]}

    from ..legal_contracts import deal_penalties
    pen = deal_penalties(db, deal.id, today)
    gaps = list(cv["data_gaps"])
    if rev_fact is None:
        gaps.append({"code": "no_revenue_recognition", "text": "Выручка не признана: нет событий признания (УПД, акт). Поступления денег выручкой не считаются"})
    open_items = [i["title"] for i in items if (i["plan"].get("base") or 0) > 0 and i["fact"] is None]
    if rev_fact is not None and open_items:
        gaps.append({"code": "costs_incomplete", "text": f"Расход признан не по всем статьям плана ({len(open_items)}) — фактическая прибыль и маржа предварительные",
                     "rows": open_items})
    if fact["legacy_not_migrated"]:
        gaps.append({"code": "legacy_planfact", "text": f"Старых фактов по статьям без события признания: {fact['legacy_not_migrated']} — учтены в факте, перенос — «Финансы → Пересобрать»"})
    if rev_miss:
        gaps.append({"code": "forecast_rate", "text": "Прогноз выручки неполный: " + "; ".join(rev_miss[:5])})
    if cv["forecast_missing"]:
        gaps.append({"code": "forecast_no_date", "text": f"В прогноз кассового разрыва не вошли обязательства без даты или курса: {len(cv['forecast_missing'])}", "rows": cv["forecast_missing"]})

    def gap_plan(sc):
        g = (plan[sc] or {}).get("cash_gap") if plan[sc] else None
        return None if g is None else {"amount_rub": s(dec(g.get("amount_rub"))), "starts_on": g.get("starts_on"), "days": g.get("days")}

    from ...api.tasks_files_planfact import _weighted_fx   # тот же средний курс, что и в план/факте (без второго алгоритма)
    return {
        "deal": {"id": deal.id, "number": deal.number, "status": deal.status, "plan_fact_closed": bool(deal.plan_fact_closed)},
        "deal_version_id": ver.id, "version_no": ver.version_no, "variant": {"id": v.id, "name": v.name},
        "scenarios": [sc for sc in SCEN if plan[sc]], "plan": {sc: kpi(sc) for sc in SCEN}, "forecast": forecast, "pnl_fact": pnl,
        "cash_fact": cv["fact"], "items": items,
        "cash_gap": {"plan_base": gap_plan("base"), "plan_pess": gap_plan("pessimistic"), "plan_opt": gap_plan("optimistic"), "forecast": cv["forecast_gap"],
                     "fact": cv["fact"]["gap"], "fact_status": cv["fact"]["status"]},
        "fx": {"weighted_plan_base": _weighted_fx(ver.calc_result, "base"), "weighted_plan_pess": _weighted_fx(ver.calc_result, "pessimistic"),
               "sensitivity": (plan["base"] or {}).get("sensitivity")},
        "penalties": pen, "data_gaps": gaps,
        "links": {"calculation": f"/deals/{deal.id}?tab=pricing", "versions": f"/deals/{deal.id}?tab=versions", "plan_fact": f"/deals/{deal.id}?tab=planfact"},
        "source_revisions": {"deal_version_id": ver.id, "obligations": len(cv["obligations"]), "recognition_events": fact["events"]},
    }


def list_deals(db: Session, user, vis) -> list[dict]:
    """Сделки с утверждённой версией, видимые пользователю, — для выбора на вкладке «Экономика сделок»."""
    from ...models import Deal
    from ...core.workflow import POST_WIN_STATUSES
    out = []
    for d in db.scalars(select(Deal).where(Deal.approved_version_id.is_not(None), Deal.status.in_(tuple(POST_WIN_STATUSES))).order_by(Deal.number.desc())).all():
        if not vis.deal(d.id):
            continue
        out.append({"id": d.id, "number": d.number, "site": d.site or "", "status": d.status, "plan_fact_closed": bool(d.plan_fact_closed)})
    return out
