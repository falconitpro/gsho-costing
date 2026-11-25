"""План / факт по месяцам и сравнение с прошлым периодом (D-167, ТЗ UX/UI пакет C; решение владельца 25.11).

Определения (одинаковые для таблицы, графика, сравнения и расшифровки):
- **План периода** — сумма обязательств (активных, видимых пользователю), чей срок попадает в месяц. Срок — по договору,
  а если он ещё не определён — ожидаемая дата (`effective_on`, то же правило, что у фильтра дат платёжного календаря).
  Обязательства-прогнозы по расчёту (`nature = forecast`) — отдельной колонкой «прогноз», в план не входят.
- **Факт периода** — сумма разнесений оплат на эти обязательства (в валюте обязательства) по дате операции:
  дата валютирования, а если её нет — дата проводки по Москве. Подтверждённые и временные разнесения — раздельно.
  Неразнесённые поступления в факт не входят (они видны в «Ликвидности»).
- **Отклонение** = факт (подтверждённый + временный) − план. Считается здесь; интерфейс ничего не пересчитывает.
- Разные валюты не суммируются: всё — по валюте и направлению.
- **Сравнение с прошлым периодом** — факт с 1-го числа текущего месяца по дату расчёта против того же числа дней
  прошлого месяца (сопоставимые отрезки, одна методика).
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import FinanceAllocation, FinanceObligation, FinanceTransaction
from .money import ZERO, s
from .obligations import effective_on

MSK = ZoneInfo("Europe/Moscow")
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
FACT_STATUSES = ("provisional", "confirmed")


def _key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _shift(y: int, m: int, k: int) -> tuple[int, int]:
    n = y * 12 + (m - 1) + k
    return n // 12, n % 12 + 1


def _bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def tx_date(t: FinanceTransaction) -> date:
    if t.value_date:
        return t.value_date
    b = t.booked_at
    return (b if b.tzinfo else b.replace(tzinfo=MSK)).astimezone(MSK).date()


def _visible(db: Session, vis, currency: Optional[str], direction: Optional[str]) -> list[FinanceObligation]:
    stmt = select(FinanceObligation).where(FinanceObligation.is_active.is_(True))
    if currency:
        stmt = stmt.where(FinanceObligation.currency == currency)
    if direction:
        stmt = stmt.where(FinanceObligation.direction == direction)
    return [o for o in db.scalars(stmt).all() if vis.obligation(o)]


def _facts(db: Session, ids: list[str]):
    if not ids:
        return []
    return db.execute(select(FinanceAllocation, FinanceTransaction).join(FinanceTransaction, FinanceTransaction.id == FinanceAllocation.transaction_id)
                      .where(FinanceAllocation.obligation_id.in_(ids), FinanceAllocation.status.in_(FACT_STATUSES))).all()


def build(db: Session, vis, as_of: date, back: int = 5, forward: int = 2, currency: Optional[str] = None, direction: Optional[str] = None) -> dict:
    periods = []
    for k in range(-back, forward + 1):
        y, m = _shift(as_of.year, as_of.month, k)
        a, b = _bounds(y, m)
        periods.append({"key": _key(a), "title": f"{MONTHS[m - 1]} {y}", "from": a.isoformat(), "to": b.isoformat(),
                        "state": "past" if b < as_of else ("current" if a <= as_of else "future")})
    keys = {p["key"] for p in periods}
    obls = _visible(db, vis, currency, direction)
    by_id = {o.id: o for o in obls}
    z = lambda: {"plan": ZERO, "forecast": ZERO, "fact": ZERO, "fact_provisional": ZERO, "plan_count": 0, "fact_count": 0}  # noqa: E731
    agg: dict[tuple[str, str, str], dict] = defaultdict(z)
    for o in obls:
        on = effective_on(o)
        if not on or _key(on) not in keys:
            continue
        r = agg[(o.currency, o.direction, _key(on))]
        r["forecast" if o.nature == "forecast" else "plan"] += o.amount
        r["plan_count"] += 1
    # сравнение: с 1-го числа по дату расчёта против того же числа дней прошлого месяца
    cur_from = as_of.replace(day=1)
    py, pm = _shift(as_of.year, as_of.month, -1)
    prev_from = date(py, pm, 1)
    prev_to = date(py, pm, min(as_of.day, calendar.monthrange(py, pm)[1]))
    cmp: dict[tuple[str, str], dict] = defaultdict(lambda: {"current": ZERO, "previous": ZERO})
    for al, t in _facts(db, list(by_id)):
        o = by_id[al.obligation_id]
        d = tx_date(t)
        if _key(d) in keys and d <= as_of:
            r = agg[(o.currency, o.direction, _key(d))]
            r["fact" if al.status == "confirmed" else "fact_provisional"] += al.amount_obligation
            r["fact_count"] += 1
        if cur_from <= d <= as_of:
            cmp[(o.currency, o.direction)]["current"] += al.amount_obligation
        elif prev_from <= d <= prev_to:
            cmp[(o.currency, o.direction)]["previous"] += al.amount_obligation
    series = []
    for c, dr in sorted({(c, dr) for c, dr, _ in agg} | set(cmp)):
        rows = []
        for p in periods:
            r = agg.get((c, dr, p["key"])) or z()
            has_fact = p["state"] != "future"
            fact_total = r["fact"] + r["fact_provisional"]
            rows.append({"period": p["key"], "plan": s(r["plan"]), "forecast": s(r["forecast"]),
                         "fact": s(r["fact"]) if has_fact else None, "fact_provisional": s(r["fact_provisional"]) if has_fact else None,
                         "deviation": s(fact_total - r["plan"]) if has_fact else None, "plan_count": r["plan_count"], "fact_count": r["fact_count"]})
        cm = cmp.get((c, dr)) or {"current": ZERO, "previous": ZERO}
        delta = cm["current"] - cm["previous"]
        pct = None if cm["previous"] == 0 else s((delta / cm["previous"] * 100).quantize(Decimal("0.1")))
        series.append({"currency": c, "direction": dr, "rows": rows,
                       "compare": {"current": s(cm["current"]), "previous": s(cm["previous"]), "delta": s(delta), "delta_pct": pct}})
    return {"as_of": as_of.isoformat(), "periods": periods, "series": series,
            "compare_window": {"from": cur_from.isoformat(), "to": as_of.isoformat(), "prev_from": prev_from.isoformat(), "prev_to": prev_to.isoformat()},
            "definitions": {
                "plan": "Сумма обязательств со сроком в месяце (срок по договору, а если его нет — ожидаемая дата); прогнозы по расчёту — отдельно.",
                "fact": "Оплаты, разнесённые на эти обязательства, по дате операции; временные разнесения — отдельно. Неразнесённые поступления не входят.",
                "deviation": "Факт (с временными) минус план.",
                "compare": "Факт с 1-го числа по дату расчёта против того же числа дней прошлого месяца."}}


def items(db: Session, vis, as_of: date, period: str, currency: str, direction: str, kind: str) -> dict:
    """Расшифровка ячейки: обязательства плана или разнесения факта за месяц — с переходом к обязательству и сделке."""
    y, m = int(period[:4]), int(period[5:7])
    a, b = _bounds(y, m)
    obls = _visible(db, vis, currency, direction)
    out = []
    if kind == "plan":
        for o in obls:
            on = effective_on(o)
            if on and a <= on <= b:
                out.append({"obligation_id": o.id, "date": on.isoformat(), "title": o.title, "counterparty": o.counterparty_name_snapshot,
                            "amount": s(o.amount), "currency": o.currency, "forecast": o.nature == "forecast", "deal_id": o.deal_id})
    else:
        by_id = {o.id: o for o in obls}
        for al, t in _facts(db, list(by_id)):
            d = tx_date(t)
            if a <= d <= min(b, as_of):
                o = by_id[al.obligation_id]
                out.append({"obligation_id": o.id, "transaction_id": t.id, "date": d.isoformat(), "title": o.title,
                            "counterparty": t.counterparty_name or o.counterparty_name_snapshot, "amount": s(al.amount_obligation),
                            "currency": o.currency, "provisional": al.status != "confirmed", "deal_id": o.deal_id})
    out.sort(key=lambda r: (r["date"], r["title"]))
    from ...models import Deal
    deals = {d.id: d.number for d in db.scalars(select(Deal).where(Deal.id.in_({r["deal_id"] for r in out if r["deal_id"]} or {"-"}))).all()}
    for r in out:
        r["deal_number"] = deals.get(r["deal_id"] or "")
    total = sum((Decimal(r["amount"]) for r in out), ZERO)
    return {"period": period, "currency": currency, "direction": direction, "kind": kind, "items": out[:500], "count": len(out), "total": s(total)}

