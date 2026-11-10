"""Прогноз ликвидности (docs/finance/02, раздел 8; 01, раздел 8).

остаток на конец периода = подтверждённый входящий остаток счетов (снимок + проведённые операции)
                         + прогнозные поступления − прогнозные списания (остатки активных обязательств по дате).

- кривая строится по каждой валюте отдельно; RUB-консолидация — только по явному курсу (дата, источник);
- входящий остаток неизвестен → `opening=None`, `balance_status="unknown"`, баланс точек None — не фиктивный ноль (D-023);
- просроченные и ожидаемые сегодня суммы — в первой точке (с пометкой), не теряются;
- без даты, записи «требует сверки» и неразнесённые проведённые — отдельно, вне кривой;
- уже поступившие на счёт деньги не повторяются прогнозом: остаток обязательства уменьшается на разнесения проведённых операций;
- кассовый разрыв — период, когда прогнозный остаток ниже порога (по умолчанию 0).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import FinanceAccount, FinanceAllocation, FinanceObligation, FinanceTransaction, FxRate
from . import accounts as acc
from .money import ZERO, dec, s
from .obligations import effective_on

STALE_DAYS = 7   # подтверждённый остаток старше недели — предупреждение об устаревании


def fx_rate(db: Session, currency: str, on: date) -> Optional[dict]:
    """Курс RUB за единицу валюты на дату: последний известный ≤ даты (ЦБ РФ, затем ручной). Не подставляет текущий курс для прошлого."""
    if currency == "RUB":
        return {"currency": "RUB", "rate": "1", "on": on.isoformat(), "source": "—"}
    for src in ("cbr", "manual"):
        r = db.scalar(select(FxRate).where(FxRate.currency == currency, FxRate.source == src, FxRate.on <= on).order_by(FxRate.on.desc()).limit(1))
        if r is not None:
            return {"currency": currency, "rate": str(Decimal(str(r.rate))), "on": r.on.isoformat(), "source": "ЦБ РФ" if src == "cbr" else "ручной курс"}
    return None


def _bucket(d: date, group_by: str) -> date:
    return d - timedelta(days=d.weekday()) if group_by == "week" else d


def _paid_in_balance(db: Session, ids: list[str]) -> dict[str, Decimal]:
    """Сумма активных разнесений по ПРОВЕДЁННЫМ операциям: эти деньги уже в остатке счёта и прогнозом не повторяются."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if not ids:
        return out
    q = (select(FinanceAllocation.obligation_id, func.sum(FinanceAllocation.amount_obligation))
         .join(FinanceTransaction, FinanceTransaction.id == FinanceAllocation.transaction_id)
         .where(FinanceAllocation.obligation_id.in_(ids), FinanceAllocation.status.in_(("provisional", "confirmed")),
                FinanceTransaction.status.in_(("posted", "reversed")))
         .group_by(FinanceAllocation.obligation_id))
    for oid, v in db.execute(q).all():
        out[oid] = dec(v) or ZERO
    return out


def build(db: Session, vis, as_of: date, horizon_days: int = 90, group_by: str = "week", currency: Optional[str] = None,
          legal_entity_id: Optional[str] = None, account_id: Optional[str] = None, threshold: Decimal = ZERO) -> dict:
    horizon = as_of + timedelta(days=horizon_days)
    now = datetime.now(timezone.utc)
    at = min(now, datetime.combine(as_of, time.max, tzinfo=timezone.utc))
    # --- входящий остаток по валютам
    accounts = db.scalars(select(FinanceAccount).where(FinanceAccount.status == "active")).all()
    if legal_entity_id:
        accounts = [a for a in accounts if a.legal_entity_id == legal_entity_id]
    if account_id:
        accounts = [a for a in accounts if a.id == account_id]
    opening: dict[str, dict] = {}
    for a in accounts:
        b = acc.balance(db, a, at)
        o = opening.setdefault(a.currency, {"value": ZERO, "accounts": [], "unknown": 0})
        snap = b["snapshot"]
        stale = bool(snap) and (now - datetime.fromisoformat(snap["as_of"])).days > STALE_DAYS
        o["accounts"].append({"id": a.id, "title": a.title, "balance": s(b["value"]), "balance_status": b["status"],
                              "snapshot_as_of": snap["as_of"] if snap else None, "stale": stale})
        if b["value"] is None:
            o["unknown"] += 1
        else:
            o["value"] += b["value"]
    # --- прогнозные движения: остатки активных обязательств
    stmt = select(FinanceObligation).where(FinanceObligation.is_active.is_(True))
    if legal_entity_id:
        stmt = stmt.where(FinanceObligation.legal_entity_id == legal_entity_id)
    obs = [o for o in db.scalars(stmt).all() if vis.obligation(o)]
    paid = _paid_in_balance(db, [o.id for o in obs])
    flows: dict[str, dict[date, dict]] = defaultdict(lambda: defaultdict(lambda: {"in": ZERO, "out": ZERO, "items": []}))
    undated: dict[str, dict] = defaultdict(lambda: {"in": ZERO, "out": ZERO, "count": 0})
    beyond: dict[str, dict] = defaultdict(lambda: {"in": ZERO, "out": ZERO, "count": 0})
    for o in obs:
        rem = o.amount - paid[o.id]
        if rem <= 0 or o.currency == "XXX":
            continue
        on = effective_on(o)
        if on is None:
            u = undated[o.currency]
            u[o.direction] += rem
            u["count"] += 1
            continue
        if on > horizon:
            bz = beyond[o.currency]
            bz[o.direction] += rem
            bz["count"] += 1
            continue
        late = on < as_of
        key = _bucket(as_of if late else on, group_by)
        f = flows[o.currency][key]
        f[o.direction] += rem
        f["items"].append({"id": o.id, "title": o.title, "direction": o.direction, "amount": s(rem), "on": on.isoformat(), "late": late,
                           "nature": o.nature, "source": o.source_type})
    # --- вне кривой: «требует сверки» (не банковский факт) и неразнесённые проведённые
    pending = defaultdict(lambda: {"in": ZERO, "out": ZERO, "count": 0})
    unalloc = defaultdict(lambda: {"in": ZERO, "out": ZERO, "count": 0})
    txs = db.scalars(select(FinanceTransaction).where(FinanceTransaction.withdrawn_at.is_(None), FinanceTransaction.origin_type != "reversal",
                                                      FinanceTransaction.status.in_(("needs_reconciliation", "draft", "posted")))).all()
    from .transactions import allocated
    used = allocated(db, [t.id for t in txs])
    for t in txs:
        if t.status in ("needs_reconciliation", "draft"):
            p = pending[t.currency]
            p[t.direction] += t.amount
            p["count"] += 1
        elif t.amount - used[t.id] > 0:
            u = unalloc[t.currency]
            u[t.direction] += t.amount - used[t.id]
            u["count"] += 1
    # --- кривые
    currencies = sorted((set(opening) | set(flows) | set(undated)) - {"XXX"})
    if currency:
        currencies = [c for c in currencies if c == currency.upper()]
    out_cur = []
    for c in currencies:
        op = opening.get(c)
        known = op is not None and op["unknown"] == 0
        partial = op is not None and 0 < op["unknown"] < len(op["accounts"])
        bal: Optional[Decimal] = op["value"] if (op and (known or partial)) else None
        status = "confirmed" if known else ("partial" if partial else "unknown")
        points = []
        keys = []
        d = _bucket(as_of, group_by)
        step = timedelta(days=7 if group_by == "week" else 1)
        while d <= horizon:
            keys.append(d)
            d += step
        minimum, min_on, gaps, gap = None, None, [], None
        if bal is not None:   # минимум и разрыв считаются и от входящего остатка: просроченные поступления могут не прийти
            minimum, min_on = bal, as_of
            if bal < threshold:
                gap = {"from": as_of.isoformat(), "to": as_of.isoformat(), "min": bal, "min_on": as_of.isoformat()}
        for k in keys:
            f = flows[c].get(k, {"in": ZERO, "out": ZERO, "items": []})
            if bal is not None:
                bal = bal + f["in"] - f["out"]
                if minimum is None or bal < minimum:
                    minimum, min_on = bal, k
                if bal < threshold:
                    gap = gap or {"from": k.isoformat(), "min": bal, "min_on": k.isoformat()}
                    if bal < gap["min"]:
                        gap["min"], gap["min_on"] = bal, k.isoformat()
                    gap["to"] = k.isoformat()
                elif gap:
                    gaps.append(gap)
                    gap = None
            points.append({"on": k.isoformat(), "inflow": s(f["in"]), "outflow": s(f["out"]), "balance": s(bal),
                           "late_in_point": any(i["late"] for i in f["items"]), "count": len(f["items"])})
        if gap:
            gaps.append(gap)
        rate = fx_rate(db, c, as_of)
        out_cur.append({
            "currency": c, "opening_balance": s(op["value"]) if op and (known or partial) else None, "balance_status": status,
            "accounts": op["accounts"] if op else [], "points": points, "minimum": s(minimum), "minimum_on": min_on.isoformat() if min_on else None,
            "threshold": s(threshold), "cash_gaps": [{**g, "min": s(g["min"])} for g in gaps],
            "undated": {k: (s(v) if k != "count" else v) for k, v in undated[c].items()},
            "beyond_horizon": {k: (s(v) if k != "count" else v) for k, v in beyond[c].items()},
            "needs_reconciliation": {k: (s(v) if k != "count" else v) for k, v in pending[c].items()},
            "unallocated": {k: (s(v) if k != "count" else v) for k, v in unalloc[c].items()},
            "fx": rate,
        })
    # --- RUB-консолидация: только валюты с известным остатком и явным курсом
    cons_points: dict[str, Decimal] = defaultdict(lambda: ZERO)
    included, excluded, fx_used = [], [], []
    for cur in out_cur:
        if cur["balance_status"] == "unknown":
            excluded.append({"currency": cur["currency"], "reason": "входящий остаток не подтверждён"})
            continue
        if cur["fx"] is None:
            excluded.append({"currency": cur["currency"], "reason": f"нет курса ЦБ РФ на {as_of.isoformat()}"})
            continue
        r = Decimal(cur["fx"]["rate"])
        fx_used.append(cur["fx"])
        included.append(cur["currency"])
        for p in cur["points"]:
            cons_points[p["on"]] += Decimal(p["balance"]) * r
    rub = {"included": included, "excluded": excluded, "fx": fx_used,
           "points": [{"on": k, "balance": s(v)} for k, v in sorted(cons_points.items())] if included else [],
           "note": "RUB-эквивалент — по курсу на дату отчёта; для будущих дат это оценка, не прогноз курса."}
    return {"as_of": as_of.isoformat(), "horizon_days": horizon_days, "group_by": group_by, "currencies": out_cur, "rub": rub,
            "formula": "остаток на конец периода = подтверждённый входящий остаток + прогнозные поступления − прогнозные списания; "
                       "просроченное и ожидаемое сегодня — в первой точке; без даты, «требует сверки» и неразнесённое — вне кривой"}


def explain_point(db: Session, vis, as_of: date, currency: str, on: date, group_by: str = "week", horizon_days: int = 90) -> dict:
    """Состав одной точки кривой: какие строки обязательств вошли и откуда входящий остаток."""
    data = build(db, vis, as_of, horizon_days, group_by, currency)
    cur = next((c for c in data["currencies"] if c["currency"] == currency.upper()), None)
    if cur is None:
        return {"currency": currency.upper(), "items": [], "opening": None}
    key = _bucket(on, group_by)
    stmt = select(FinanceObligation).where(FinanceObligation.is_active.is_(True), FinanceObligation.currency == currency.upper())
    obs = [o for o in db.scalars(stmt).all() if vis.obligation(o)]
    paid = _paid_in_balance(db, [o.id for o in obs])
    items = []
    for o in obs:
        rem = o.amount - paid[o.id]
        d = effective_on(o)
        if rem <= 0 or d is None or d > as_of + timedelta(days=horizon_days):
            continue
        if _bucket(as_of if d < as_of else d, group_by) == key:
            items.append({"id": o.id, "title": o.title, "direction": o.direction, "amount": s(rem), "on": d.isoformat(), "late": d < as_of,
                          "nature": o.nature, "source": o.source_type, "counterparty": o.counterparty_name_snapshot})
    point = next((p for p in cur["points"] if p["on"] == key.isoformat()), None)
    return {"currency": cur["currency"], "on": key.isoformat(), "point": point, "opening_balance": cur["opening_balance"],
            "balance_status": cur["balance_status"], "accounts": cur["accounts"], "items": sorted(items, key=lambda x: (x["on"], x["direction"]))}
