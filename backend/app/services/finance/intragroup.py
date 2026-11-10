"""Внутригрупповые расчёты (D-162, D-163): кто из компаний группы кому должен, и исключение внутригрупповых обязательств
из сводной ликвидности группы.

Внутригрупповое обязательство — обязательство компании группы, контрагент которого тоже компания группы:
поставщик-компания группы (Factory.supplier_status = group_company с привязанным юрлицом, например «Альянс-Импекс»)
или сама компания группы (контрагент — юрлицо). Пример: договор Берг-Хаус ↔ Альянс-Импекс — Берг-Хаус платит
Альянс-Импекс; договор Альянс-Импекс ↔ конечный поставщик — внешний, не внутригрупповой.

Суммы — по валютам, без пересчёта. Для пары компаний «плательщик → получатель»: начислено, оплачено (подтверждено),
временно разнесено, остаток, просрочено (от юридического срока). Один и тот же долг может быть заведён у обеих сторон
(расход у плательщика и поступление у получателя): итоги пары берутся по книгам плательщика, а если там ничего нет —
по книгам получателя; строки другой стороны показываются для сверки и в итоги не входят.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Contract, Deal, Factory, LegalEntity
from .money import ZERO, s
from .obligations import ObligationFilter, query


def group_map(db: Session) -> dict[tuple[str, str], str]:
    """(вид контрагента, id) → юрлицо группы."""
    out: dict[tuple[str, str], str] = {}
    for f in db.scalars(select(Factory).where(Factory.supplier_status == "group_company", Factory.legal_entity_id.is_not(None))).all():
        out[("factory", f.id)] = f.legal_entity_id
    for e in db.scalars(select(LegalEntity)).all():
        for kind in ("other", "entity", "group"):
            out[(kind, e.id)] = e.id
    return out


def counterpart_entity(gm: dict, kind: Optional[str], cid: Optional[str]) -> Optional[str]:
    return gm.get((kind or "other", cid or "")) if cid else None


def is_intragroup(gm: dict, o) -> bool:
    return counterpart_entity(gm, o.counterparty_kind, o.counterparty_id) is not None


def build(db: Session, vis, as_of: Optional[date] = None, currency: Optional[str] = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    gm = group_map(db)
    names = {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    reg = query(db, vis, ObligationFilter(as_of=as_of, currency=currency, limit=100_000))
    pairs: dict[tuple, dict] = {}
    deals_with_intra: dict[str, str] = {}
    for r in reg["items"]:
        cp = r["counterparty"] or {}
        other = counterpart_entity(gm, cp.get("kind"), cp.get("id"))
        own = r.get("legal_entity_id")
        if other is None or not own or other == own:
            continue
        payer, receiver = (own, other) if r["direction"] == "out" else (other, own)
        side = "payer" if r["direction"] == "out" else "receiver"
        key = (payer, receiver, r["currency"], side)
        p = pairs.setdefault(key, {"payer_id": payer, "payer": names.get(payer, ""), "receiver_id": receiver, "receiver": names.get(receiver, ""),
                                   "currency": r["currency"], "books": side, "amount": ZERO, "confirmed": ZERO, "provisional": ZERO, "remaining": ZERO,
                                   "overdue": ZERO, "count": 0, "rows": [], "other_side_rows": []})
        rem = Decimal(r["remaining"])
        p["amount"] += Decimal(r["amount"])
        p["confirmed"] += Decimal(r["confirmed_paid"])
        p["provisional"] += Decimal(r["provisional_paid"])
        p["remaining"] += max(rem, ZERO)
        if rem > 0 and int(r.get("days_overdue") or 0) > 0:
            p["overdue"] += rem
        p["count"] += 1
        deal = r.get("deal") or {}
        p["rows"].append({"id": r["id"], "title": r["title"], "deal": deal, "amount": r["amount"], "remaining": r["remaining"], "status": r["status"],
                          "legal_due_on": r.get("legal_due_on"), "forecast_on": r.get("forecast_on"), "days_overdue": r.get("days_overdue")})
        if deal.get("id") and r["direction"] == "out":
            deals_with_intra[deal["id"]] = deal.get("number") or ""
    warnings = []
    for did, number in sorted(deals_with_intra.items(), key=lambda x: x[1]):
        signed = db.scalar(select(Contract.id).where(Contract.deal_id == did, Contract.mirror_kind == "via_group",
                                                    Contract.status.in_(("active", "closed"))).limit(1))
        if signed is None:
            warnings.append(f"Сделка {number}: нет подписанного договора компании группы с конечным поставщиком — "
                            "при исключении внутригрупповых из сводной ликвидности закупка в прогнозе не видна")
    chosen: dict[tuple, dict] = {}
    for (payer, receiver, cur, side), p in pairs.items():
        k = (payer, receiver, cur)
        if side == "payer" or k not in chosen:
            if k in chosen:          # книги получателя — для сверки
                p["other_side_rows"] = chosen[k]["rows"]
            chosen[k] = p
        else:
            chosen[k]["other_side_rows"] = p["rows"]
    rows = sorted(chosen.values(), key=lambda p: (p["payer"], p["receiver"], p["currency"]))
    for p in rows:
        for k in ("amount", "confirmed", "provisional", "remaining", "overdue"):
            p[k] = s(p[k])
    # сальдо пары компаний: A→B минус B→A, по валютам
    net: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for p in rows:
        a, b = sorted([p["payer_id"], p["receiver_id"]])
        sign = 1 if p["payer_id"] == a else -1
        net[(a, b, p["currency"])] += sign * Decimal(p["remaining"])
    balances = [{"debtor_id": a if v > 0 else b, "debtor": names.get(a if v > 0 else b, ""), "creditor_id": b if v > 0 else a,
                 "creditor": names.get(b if v > 0 else a, ""), "currency": c, "net": s(abs(v))} for (a, b, c), v in sorted(net.items()) if v != 0]
    return {"as_of": as_of.isoformat(), "pairs": rows, "balances": balances, "warnings": warnings,
            "note": "Суммы по валютам без пересчёта; остаток — без временно разнесённых оплат; просрочка — от юридического срока."}
