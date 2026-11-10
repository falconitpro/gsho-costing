"""Единый денежный DTO для старых экранов: «Сводка → Деньги» (`/api/summary/money`) и блок «Денежный поток» рабочего места
(docs/finance/03, разделы 7–8; D-033, D-130).

Обе точки больше не держат свою кассовую формулу: строки — из реестра обязательств, кривая — из прогноза ликвидности.
Ключи старого ответа сохранены; изменение семантики (задокументировано в отчёте этапа):
- `curve[].cum` — прогнозный остаток RUB-счетов от подтверждённого входящего остатка (а не накопленный поток с нуля);
  неизвестный остаток → `cum=None`, `balance_status="unknown"`, `totals.min_cum=None`;
- строки — все источники (договоры, заказы, партии, прогноз расчёта) и все валюты; `amount` — остаток к оплате;
- запчасти без даты — по-прежнему отдельно и вне кривой.
Право — `finance_analytics` (как раньше): КД видит сводку, хотя раздела «Финансы» у него по умолчанию нет (D-116, D-130).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Deal, User
from . import liquidity
from .access import Visibility
from .obligations import ObligationFilter, query

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _f(v: Optional[str]) -> Optional[float]:
    return None if v is None else float(Decimal(v))


def rows(db: Session, user, today: date, horizon: date) -> list[dict]:
    """Строки к оплате до горизонта (включая просроченные) — в прежнем формате сводки + новые поля аддитивно."""
    vis = Visibility(db, user)
    reg = query(db, vis, ObligationFilter(as_of=today, status="open", limit=100_000))
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_([r["deal"]["id"] for r in reg["items"] if r["deal"]] or ["-"]))).all()}
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out = []
    for r in reg["items"]:
        on = r["legal_due_on"] or r["forecast_on"]
        if not on or on > horizon.isoformat():
            continue
        d = deals.get(r["deal"]["id"]) if r["deal"] else None
        on_d = date.fromisoformat(on)
        out.append({"deal_id": d.id if d else None, "deal_number": d.number if d else (r["contract"]["number"] if r["contract"] else r["source"]["number"]),
                    "client": r["counterparty"]["name"], "manager": names.get(d.manager_id, "") if d else "", "title": r["title"], "direction": r["direction"],
                    "on": on_d, "amount": _f(r["remaining"]), "currency": r["currency"], "overdue_days": r["days_overdue"],
                    "url": r["source"]["url"] or (f"/deals/{d.id}?tab=planfact" if d else None), "source": r["source"]["type"],
                    # новое (аддитивно)
                    "obligation_id": r["id"], "nature": r["nature"], "status": r["status"], "amount_total": _f(r["amount"]), "expected_only": r["legal_due_on"] is None,
                    "flags": r["flags"], "provisional_paid": _f(r["provisional_paid"])})
    out.sort(key=lambda x: x["on"])
    return out


def curve(db: Session, user, today: date, days: int) -> dict:
    """Недельная кривая RUB от подтверждённого остатка + группы по остальным валютам."""
    liq = liquidity.build(db, Visibility(db, user), today, days, "week")
    rub = next((c for c in liq["currencies"] if c["currency"] == "RUB"), None)
    pts = []
    if rub:
        for p in rub["points"]:
            pts.append({"week": p["on"], "label": f"{int(p['on'][8:10])} {MONTHS[int(p['on'][5:7]) - 1]}", "in": _f(p["inflow"]) or 0.0, "out": _f(p["outflow"]) or 0.0,
                        "net": (_f(p["inflow"]) or 0.0) - (_f(p["outflow"]) or 0.0), "cum": _f(p["balance"])})
    by_ccy = {c["currency"]: {"opening_balance": c["opening_balance"], "balance_status": c["balance_status"], "minimum": c["minimum"], "minimum_on": c["minimum_on"],
                              "cash_gaps": c["cash_gaps"]} for c in liq["currencies"]}
    return {"points": pts, "balance_status": rub["balance_status"] if rub else "unknown", "opening_balance": _f(rub["opening_balance"]) if rub else None,
            "minimum": _f(rub["minimum"]) if rub else None, "minimum_on": rub["minimum_on"] if rub else None, "by_currency": by_ccy, "rub_consolidated": liq["rub"]}


def summary_money(db: Session, user, days: int = 90) -> dict:
    today = date.today()
    horizon = today + timedelta(days=days)
    rs = rows(db, user, today, horizon)
    receivables = [r for r in rs if r["direction"] == "in" and r["overdue_days"] > 0]
    cv = curve(db, user, today, days)
    from .sources import parts_undated
    pu = parts_undated(db, user)
    parts = [{"id": p["id"], "number": p["number"], "client": p["client"], "manager": p["manager"], "amount": float(p["amount"]), "lines": p["lines"], "urgent": p["urgent"], "url": p["url"]}
             for p in pu["items"]]
    by_ccy: dict[str, dict[str, float]] = defaultdict(lambda: {"in": 0.0, "out": 0.0})
    for r in rs:
        by_ccy[r["currency"]][r["direction"]] += r["amount"] or 0.0
    return {"rows": rs, "receivables": receivables,
            "curve": [{"week": p["week"], "in": p["in"], "out": p["out"], "cum": p["cum"]} for p in cv["points"] if p["in"] or p["out"] or p is cv["points"][0]],
            "parts": {"rows": parts, "sum_rub": float(pu["total"])},
            "totals": {"in_rub": round(by_ccy["RUB"]["in"], 2), "out_rub": round(by_ccy["RUB"]["out"], 2), "receivable_count": len(receivables),
                       "min_cum": cv["minimum"], "balance_status": cv["balance_status"], "opening_rub": cv["opening_balance"],
                       "by_currency": {k: {"in": round(v["in"], 2), "out": round(v["out"], 2)} for k, v in sorted(by_ccy.items())}},
            "liquidity": {"by_currency": cv["by_currency"], "rub_consolidated": cv["rub_consolidated"]},
            "semantics": "cum — прогнозный остаток RUB-счетов от подтверждённого входящего остатка; null — остаток не подтверждён"}


def workplace_money(db: Session, user, today: date, monday: date) -> dict:
    """Блок рабочего места: 8 недель RUB, просроченная дебиторка, минимум прогнозного остатка — из того же сервиса."""
    cv = curve(db, user, today, 56)
    by_week = {p["week"]: p for p in cv["points"]}
    pts = []
    for i in range(8):
        wk = (monday + timedelta(days=7 * i)).isoformat()
        p = by_week.get(wk) or {"week": wk, "label": f"{int(wk[8:10])} {MONTHS[int(wk[5:7]) - 1]}", "in": 0.0, "out": 0.0, "net": 0.0, "cum": None}
        pts.append({**p, "in": round(p["in"]), "out": round(p["out"]), "net": round(p["net"]), "cum": None if p["cum"] is None else round(p["cum"])})
    rs = rows(db, user, today, today + timedelta(days=56))
    rec = [r for r in rs if r["direction"] == "in" and r["overdue_days"] > 0]
    known = [p for p in pts if p["cum"] is not None]
    low = min(known, key=lambda p: p["cum"]) if known else None
    from .access import can_read
    return {"curve": pts, "receivable_sum": round(sum(r["amount"] or 0 for r in rec if r["currency"] == "RUB")), "receivable_count": len(rec),
            "min_cum": low["cum"] if low else None, "min_week": low["label"] if low else "", "balance_status": cv["balance_status"],
            "url": "/finance?tab=liquidity" if can_read(user) else "/summary?kind=money"}
