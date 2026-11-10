"""«Дебиторка и кредиторка» (docs/finance/01, раздел 7; 05, раздел 9; D-143).

Строки — открытые активные обязательства реестра (тот же `obligations.query`, те же права): остаток = сумма − подтверждённая оплата;
временно разнесённое показано отдельно и остаток не уменьшает. Просрочка и ageing — только от юридического срока.
Обязательства без юридического и прогнозного срока, а также запчасти без согласованной даты — отдельным блоком, не в задолженности.
Суммы — по валютам; RUB-концентрация — только для сравнения, по курсу ЦБ на дату отчёта.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from .money import ZERO, dec, s
from .obligations import ObligationFilter, query

BUCKETS = [("current", "не просрочено", 0, 0), ("d1_7", "1–7 дн.", 1, 7), ("d8_30", "8–30 дн.", 8, 30), ("d31_60", "31–60 дн.", 31, 60),
           ("d61_90", "61–90 дн.", 61, 90), ("d90_plus", "более 90 дн.", 91, 10 ** 6)]


def bucket_of(days_overdue: int) -> str:
    for code, _, lo, hi in BUCKETS:
        if lo <= days_overdue <= hi:
            return code
    return "d90_plus"


def build(db, user, vis, as_of: Optional[date] = None, direction: Optional[str] = None, group_by: str = "counterparty", currency: Optional[str] = None,
          legal_entity_id: Optional[str] = None) -> dict:
    as_of = as_of or date.today()
    reg = query(db, vis, ObligationFilter(as_of=as_of, direction=direction, currency=currency, legal_entity_id=legal_entity_id, status="open", limit=10_000))
    from .liquidity import fx_rate
    rates: dict[str, Optional[dict]] = {}

    def rub(amount: Decimal, cur: str) -> Optional[Decimal]:
        if cur not in rates:
            rates[cur] = fx_rate(db, cur, as_of)
        r = rates[cur]
        return None if r is None else dec(amount * Decimal(r["rate"]))

    dated, undated = [], []
    for r in reg["items"]:
        if Decimal(r["remaining"]) <= 0:
            continue
        if r["status"] == "no_date":
            undated.append(r)
        else:
            r = {**r, "bucket": bucket_of(int(r["days_overdue"] or 0))}
            dated.append(r)
    # итоги по направлению и валюте
    tot: dict[str, dict[str, dict]] = {"in": {}, "out": {}}
    for r in dated:
        t = tot[r["direction"]].setdefault(r["currency"], {"amount": ZERO, "confirmed": ZERO, "provisional": ZERO, "remaining": ZERO, "overdue": ZERO,
                                                             **{b[0]: ZERO for b in BUCKETS}, "count": 0})
        rem = Decimal(r["remaining"])
        t["amount"] += Decimal(r["amount"])
        t["confirmed"] += Decimal(r["confirmed_paid"])
        t["provisional"] += Decimal(r["provisional_paid"])
        t["remaining"] += rem
        t[r["bucket"]] += rem
        if r["bucket"] != "current":
            t["overdue"] += rem
        t["count"] += 1
    totals = {d: {c: {k: (s(v) if isinstance(v, Decimal) else v) for k, v in t.items()} for c, t in sorted(cur.items())} for d, cur in tot.items()}
    # группы: контрагент или сделка; внутри — строки
    groups: dict[tuple, dict] = {}
    for r in dated:
        if group_by == "deal":
            key = ("deal", (r["deal"] or {}).get("id") or "")
            title = (r["deal"] or {}).get("number") or "без сделки"
        else:
            key = ("cp", r["counterparty"]["name"] or "—")
            title = r["counterparty"]["name"] or "—"
        g = groups.setdefault((r["direction"], *key), {"direction": r["direction"], "title": title, "by_currency": defaultdict(lambda: {"remaining": ZERO, "overdue": ZERO}),
                                                       "rows": [], "max_days_overdue": 0, "rub": ZERO, "rub_complete": True})
        g["rows"].append(r)
        rem = Decimal(r["remaining"])
        g["by_currency"][r["currency"]]["remaining"] += rem
        if r["bucket"] != "current":
            g["by_currency"][r["currency"]]["overdue"] += rem
        g["max_days_overdue"] = max(g["max_days_overdue"], int(r["days_overdue"] or 0))
        x = rub(rem, r["currency"])
        if x is None:
            g["rub_complete"] = False
        else:
            g["rub"] += x
    glist = sorted(groups.values(), key=lambda g: (g["direction"], -g["max_days_overdue"], -g["rub"]))
    rub_total = {d: sum((g["rub"] for g in glist if g["direction"] == d), ZERO) for d in ("in", "out")}
    out_groups = []
    for g in glist:
        share = (g["rub"] / rub_total[g["direction"]] * 100) if rub_total[g["direction"]] else None
        out_groups.append({"direction": g["direction"], "title": g["title"], "count": len(g["rows"]), "max_days_overdue": g["max_days_overdue"],
                           "by_currency": {c: {k: s(v) for k, v in t.items()} for c, t in sorted(g["by_currency"].items())},
                           "rub": s(g["rub"]), "rub_complete": g["rub_complete"], "share_pct": None if share is None else round(float(share), 1),
                           "rows": sorted(g["rows"], key=lambda r: (-(r["days_overdue"] or 0), r["legal_due_on"] or r["forecast_on"] or "9999"))})
    from .sources import parts_undated
    parts = parts_undated(db, user)["items"] if direction in (None, "in") else []
    return {"as_of": as_of.isoformat(), "direction": direction, "group_by": group_by, "buckets": [{"code": c, "title": t} for c, t, _, _ in BUCKETS],
            "totals": totals, "groups": out_groups, "undated": undated, "parts_undated": parts,
            "rates": {c: r for c, r in sorted(rates.items()) if c != "RUB"}, "rates_missing": sorted(c for c, r in rates.items() if r is None),
            "notes": ["Остаток уменьшается только подтверждёнными разнесениями; временные показаны отдельно.",
                      "Просрочка и ageing — от юридического срока; ожидаемая дата просрочку не отменяет и не создаёт.",
                      "Без согласованной даты — отдельный блок, в задолженность не входит.",
                      "Доля в концентрации — по курсу ЦБ на дату отчёта, только для сравнения."]}
