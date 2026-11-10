"""Обзор раздела «Финансы» (базовый объём этапа 2–3): доступно по валютам, ближайшие и просроченные платежи, неразнесённое, сверка.
Все показатели — из тех же сервисов, что и реестры (без копий формул)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import FinanceAccount, FinanceObligation, FinanceTransaction
from . import accounts as acc
from .money import ZERO, s
from .obligations import ObligationFilter, query
from .transactions import allocated


def build(db: Session, vis, as_of: date, horizon_days: int = 30) -> dict:
    # остатки по валютам: неизвестный остаток любого счёта делает итог валюты неполным, а не нулевым
    available: dict[str, dict] = {}
    stale = []
    for a in db.scalars(select(FinanceAccount).where(FinanceAccount.status == "active")).all():
        b = acc.balance(db, a)
        cur = available.setdefault(a.currency, {"currency": a.currency, "value": ZERO, "accounts": 0, "unknown": 0})
        cur["accounts"] += 1
        if b["value"] is None:
            cur["unknown"] += 1
            stale.append({"id": a.id, "title": a.title, "currency": a.currency})
        else:
            cur["value"] += b["value"]
    avail = [{"currency": c, "value": s(v["value"]) if v["unknown"] < v["accounts"] else None, "accounts": v["accounts"], "unknown": v["unknown"],
              "status": "confirmed" if v["unknown"] == 0 else ("unknown" if v["unknown"] == v["accounts"] else "partial")} for c, v in sorted(available.items())]

    reg = query(db, vis, ObligationFilter(as_of=as_of, status="open", limit=10_000))
    soon, overdue = defaultdict(lambda: {"in": ZERO, "out": ZERO}), defaultdict(lambda: {"in": ZERO, "out": ZERO})
    upcoming, late, no_date = [], [], 0
    horizon = as_of + timedelta(days=horizon_days)
    for r in reg["items"]:
        rem = Decimal(r["remaining"])
        on = r["legal_due_on"] or r["forecast_on"]
        if r["status"] == "overdue":
            overdue[r["currency"]][r["direction"]] += rem
            late.append(r)
        elif r["status"] == "no_date":
            no_date += 1
        elif on and on <= horizon.isoformat():
            soon[r["currency"]][r["direction"]] += rem
            upcoming.append(r)
    txs = db.scalars(select(FinanceTransaction).where(FinanceTransaction.status.in_(("posted", "draft", "needs_reconciliation")),
                                                      FinanceTransaction.origin_type != "reversal", FinanceTransaction.withdrawn_at.is_(None))).all()
    used = allocated(db, [t.id for t in txs])
    unalloc: dict[str, dict] = {}
    needs_rec = 0
    for t in txs:
        if t.status == "needs_reconciliation":
            needs_rec += 1
        left = t.amount - used[t.id]
        if t.status == "posted" and left > 0:
            u = unalloc.setdefault(t.currency, {"currency": t.currency, "count": 0, "amount": ZERO})
            u["count"] += 1
            u["amount"] += left
    conflicts = db.query(FinanceObligation).filter(FinanceObligation.sync_status == "conflict").count()
    provisional = sum(1 for r in reg["items"] if "needs_reconciliation" in r["flags"])
    return {
        "as_of": as_of.isoformat(), "horizon_days": horizon_days,
        "available": avail, "stale_accounts": stale,
        "upcoming": {"by_currency": {c: {k: s(v) for k, v in t.items()} for c, t in sorted(soon.items())}, "items": upcoming[:10], "count": len(upcoming)},
        "overdue": {"by_currency": {c: {k: s(v) for k, v in t.items()} for c, t in sorted(overdue.items())}, "items": late[:10], "count": len(late)},
        "no_date_count": no_date,
        "unallocated": [{**u, "amount": s(u["amount"])} for _, u in sorted(unalloc.items())],
        "reconciliation": {"transactions_needs_reconciliation": needs_rec, "obligation_conflicts": conflicts, "provisional_allocations": provisional},
        "data_quality": {"unknown_balance": bool(stale), "conflicts": conflicts > 0, "unallocated": bool(unalloc)},
    }
