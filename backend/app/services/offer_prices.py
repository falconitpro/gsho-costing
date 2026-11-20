"""Цены предложения в одном месте (D-109).

Каждое назначение цены — запись в `offer_prices`:
- kd — цена КД в предложении: пишется сама при любом изменении цены предложения (назначение КД, перенос цены
  при изменении условий контракта, исправления);
- revision — итог редакции КП утверждённой версии (правка КП или шаг торгов): пишется сама при создании редакции.

Действующая цена читается одной функцией `current()`:
- после утверждения — последняя редакция КП утверждённой версии (пока редакции действуют, D-104), иначе
- последняя цена КД.
Поля предложения (`variants.price_*`) пока остаются запасными: если записей ещё нет (тестовый стенд без миграции),
читаются они. Записи не меняются и не удаляются — это история цены.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from ..models import Deal, KpRevision, OfferPrice, Variant

PRICE_FIELDS = ("price_rub", "price_currency", "price_fx_rate")


def units_of(deal: Deal, v: Variant) -> int:
    poss = [p for p in deal.positions if not v.position_ids or p.id in v.position_ids]
    return max(1, int(poss[0].qty or 1)) if len(poss) == 1 else 1


def _rate(v: Variant) -> Optional[float]:
    cur = (v.price_currency or "RUB").upper()
    return 1.0 if cur == "RUB" else (float(v.price_fx_rate) if v.price_fx_rate else None)


def _row(deal: Deal, v: Variant, kind: str, total: float, **kw) -> OfferPrice:
    rate, units = _rate(v), units_of(deal, v)
    return OfferPrice(deal_id=deal.id, variant_id=v.id, kind=kind, total_rub=round(float(total), 2), currency=(v.price_currency or "RUB").upper(),
                      fx_rate=rate, units=units, unit_amount=round(float(total) / rate / units, 2) if rate else None, seq=time.time_ns(), **kw)


def _extras(changes: dict, vid: str) -> float:
    ch = (changes or {}).get(vid) or {}
    s = 0.0
    for e in ch.get("extras") or []:
        if str(e.get("name") or "").strip() and e.get("price_rub") not in (None, ""):
            try:
                s += float(e["price_rub"])
            except (TypeError, ValueError):
                pass
    return round(s, 2)


@event.listens_for(Session, "before_flush")
def _record(session: Session, flush_context, instances) -> None:
    """Любое изменение цены предложения и любая новая редакция КП → запись в offer_prices (та же транзакция)."""
    with session.no_autoflush:
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, Variant):
                if obj.price_rub is None or not obj.deal_id:
                    continue
                if not obj.id:          # новое предложение: id обычно назначается при вставке — нужен раньше
                    obj.id = str(uuid.uuid4())
                st = inspect(obj)
                if obj in session.new or any(st.attrs[f].history.has_changes() for f in PRICE_FIELDS):
                    deal = obj.deal if obj.deal is not None else session.get(Deal, obj.deal_id)
                    if deal is not None:
                        session.add(_row(deal, obj, "kd", obj.price_rub))
            elif isinstance(obj, KpRevision) and obj in session.new:
                if not obj.id:
                    obj.id = str(uuid.uuid4())
                deal = session.get(Deal, obj.deal_id)
                if deal is None:
                    continue
                for v in deal.variants:
                    total = (obj.prices or {}).get(v.id)
                    if total is None:
                        continue
                    session.add(_row(deal, v, "revision", total, version_id=obj.version_id, source_id=obj.id or "", revision_no=obj.no,
                                     extras_rub=_extras(obj.changes, v.id), created_by=obj.created_by))


def current(db: Session, deal: Deal, v: Variant) -> dict:
    """Действующая цена предложения: {"kd": запись цены КД, "revision": запись редакции утверждённой версии или None,
    "from_table": есть ли записи}. Без записей — из полей предложения (запасной путь)."""
    from .contract_price import revisions_apply
    rows = list(db.scalars(select(OfferPrice).where(OfferPrice.variant_id == v.id).order_by(OfferPrice.created_at, OfferPrice.seq)).all())
    kd = next((r for r in reversed(rows) if r.kind == "kd"), None)
    rev = None
    if deal.approved_version_id and revisions_apply(deal):
        rev = next((r for r in reversed(rows) if r.kind == "revision" and r.version_id == deal.approved_version_id), None)
        if rev is None:      # редакции до миграции / без записей — из таблицы редакций (запасной путь)
            kr = db.scalar(select(KpRevision).where(KpRevision.deal_id == deal.id, KpRevision.version_id == deal.approved_version_id)
                           .order_by(KpRevision.no.desc()).limit(1))
            if kr is not None and (kr.prices or {}).get(v.id) is not None:
                rev = _row(deal, v, "revision", kr.prices[v.id], version_id=kr.version_id, source_id=kr.id, revision_no=kr.no,
                           extras_rub=_extras(kr.changes, v.id))
    return {"kd_rub": float(kd.total_rub) if kd is not None else (float(v.price_rub) if v.price_rub is not None else None),
            "revision": rev, "from_table": bool(rows)}
