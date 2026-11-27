"""Цены конкурентов при утверждении сделки (D-066). Видят и вводят только КД и ЗГД.

Предложение конкурента: дата сведений, название конкурента (если известно), что предлагает, сумма как введена — с НДС или
без, валюта; курс ЦБ на дату сведений фиксируется. Справочно — без НДС / с НДС, в рублях и в валюте контракта.
Сравнение с нашими предложениями — по цене без НДС в рублях. Правки и удаление — в журнале действий; удалённые
строки остаются в истории. По конкуренту с названием — история предложений по всем сделкам.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..db import get_db
from ..models import AuditLog, Client, Competitor, CompetitorOffer, Contract, Deal, FxRate, User, Variant
from ..services import settings_service
from ..services.deal_service import audit
from .common import get_deal_or_404
from ..core.clock import today as msk_today

router = APIRouter(tags=["competitors"])
CD_GD = require_roles(Role.CD, Role.GD)
CURRENCIES = ("RUB", "CNY", "USD", "EUR")


def name_key(name: str) -> str:
    s = re.sub(r"[«»\"'`]", "", (name or "").lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", s).strip()


def rate_on(db: Session, currency: str, on: date) -> tuple[float, Optional[date]]:
    """Курс ЦБ (RUB за единицу) на дату или ближайший предыдущий; нет до даты — ближайший после."""
    cur = (currency or "RUB").upper()
    if cur == "RUB":
        return 1.0, on
    r = db.scalars(select(FxRate).where(FxRate.currency == cur, FxRate.on <= on).order_by(FxRate.on.desc())).first() \
        or db.scalars(select(FxRate).where(FxRate.currency == cur).order_by(FxRate.on)).first()
    if r is None:
        raise HTTPException(422, f"Нет курса ЦБ для валюты {cur}")
    return float(r.rate), r.on


def contract_currency(db: Session, deal: Deal) -> tuple[str, str]:
    """Валюта контракта с клиентом: договор поставки по сделке → валюта нашего предложения → RUB."""
    c = db.scalars(select(Contract).where(Contract.deal_id == deal.id, Contract.direction == "income",
                                          Contract.status.notin_(["archived", "superseded", "cancelled"])).order_by(Contract.created_at.desc())).first()
    if c is not None and c.currency:
        return c.currency.upper(), "договор с клиентом"
    if deal.deal_currency:
        return deal.deal_currency.upper(), "валюта сделки"
    v = next((x for x in deal.variants if x.price_currency), None)
    if v is not None:
        return v.price_currency.upper(), "валюта нашего предложения"
    from .pricing import offer_currency
    return offer_currency(deal), "валюта предложения поставщика"


def money(db: Session, amount: float, currency: str, vat_included: bool, vat_pct: float, rate: float, contract_ccy: str, on: date) -> dict:
    k = 1 + (vat_pct or 0) / 100.0
    net = amount / k if vat_included else amount
    gross = net * k
    out = {"net": round(net, 2), "gross": round(gross, 2), "rub_net": round(net * rate, 2), "rub_gross": round(gross * rate, 2)}
    if contract_ccy != currency.upper():
        crate, _ = rate_on(db, contract_ccy, on)
        out.update(contract_net=round(net * rate / crate, 2), contract_gross=round(gross * rate / crate, 2))
    else:
        out.update(contract_net=out["net"], contract_gross=out["gross"])
    return out


def _offer_dict(db: Session, o: CompetitorOffer, comp: Optional[Competitor], contract_ccy: str, names: dict[str, str]) -> dict:
    return {"id": o.id, "deal_id": o.deal_id, "variant_id": o.variant_id, "competitor_id": o.competitor_id, "competitor": comp.name if comp else "",
            "on": o.on.isoformat(), "equipment": o.equipment, "qty": o.qty, "amount": o.amount, "currency": o.currency, "vat_included": o.vat_included,
            "vat_pct": o.vat_pct, "delivery_term": o.delivery_term or "", "payment_terms": o.payment_terms or "", "fx_rate": o.fx_rate, "fx_on": o.fx_on.isoformat() if o.fx_on else None, "source": o.source, "comment": o.comment,
            "created_by": names.get(o.created_by or "", ""), "created_at": o.created_at.isoformat() if o.created_at else None,
            "deleted": o.deleted_at is not None, "deleted_at": o.deleted_at.isoformat() if o.deleted_at else None,
            **money(db, o.amount, o.currency, o.vat_included, o.vat_pct, o.fx_rate, contract_ccy, o.on)}


def _snapshot(o: CompetitorOffer, comp: Optional[Competitor]) -> dict:
    return {"competitor": comp.name if comp else "", "on": o.on.isoformat(), "equipment": o.equipment, "qty": o.qty, "amount": o.amount,
            "currency": o.currency, "vat_included": o.vat_included, "vat_pct": o.vat_pct, "variant_id": o.variant_id, "source": o.source, "comment": o.comment,
            "delivery_term": o.delivery_term or "", "payment_terms": o.payment_terms or ""}


def _names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name or u.email for u in db.scalars(select(User)).all()}


class OfferIn(BaseModel):
    competitor: str = ""                   # название; пусто — «название неизвестно»
    on: date = Field(default_factory=date.today)
    equipment: str = ""
    qty: float = 1
    amount: float
    currency: str = "RUB"
    vat_included: bool = False
    vat_pct: Optional[float] = None        # по умолчанию — НДС с продажи из настроек
    variant_id: Optional[str] = None
    source: str = ""
    comment: str = ""
    delivery_term: str = ""                # срок поставки конкурента (D-074)
    payment_terms: str = ""                # условия оплаты конкурента


def _competitor(db: Session, name: str, user_id: str) -> Optional[Competitor]:
    key = name_key(name)
    if not key:
        return None
    c = db.scalar(select(Competitor).where(Competitor.name_key == key))
    if c is None:
        c = Competitor(name=re.sub(r"\s+", " ", name).strip()[:255], name_key=key[:255], created_by=user_id)
        db.add(c)
        db.flush()
    return c


def _apply(db: Session, deal: Deal, o: CompetitorOffer, body: OfferIn, user: CurrentUser) -> Optional[Competitor]:
    cur = (body.currency or "RUB").upper()
    if cur not in CURRENCIES:
        raise HTTPException(422, f"Валюта: {', '.join(CURRENCIES)}")
    if body.amount <= 0:
        raise HTTPException(422, "Цена конкурента должна быть больше нуля")
    if body.variant_id and body.variant_id not in {v.id for v in deal.variants}:
        raise HTTPException(422, "Наше предложение не найдено в сделке")
    comp = _competitor(db, body.competitor, user.id)
    o.competitor_id = comp.id if comp else None
    o.on, o.equipment, o.qty, o.amount, o.currency = body.on, body.equipment.strip()[:500], body.qty or 1, body.amount, cur
    o.vat_included = body.vat_included
    o.vat_pct = body.vat_pct if body.vat_pct is not None else float(settings_service.get(db, "sales_vat_pct") or 22.0)
    o.variant_id, o.source, o.comment = body.variant_id, body.source.strip()[:255], body.comment.strip()
    o.delivery_term, o.payment_terms = body.delivery_term.strip()[:255], body.payment_terms.strip()[:500]
    o.fx_rate, o.fx_on = rate_on(db, cur, body.on)
    return comp


def _our(db: Session, deal: Deal, contract_ccy: str) -> list[dict]:
    vat = float(settings_service.get(db, "sales_vat_pct") or 22.0)
    out = []
    # после торгов (D-074) наша цена — последней редакции КП
    from ..models import DealVersion
    from ..services.negotiation import current
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    now = current(db, deal, ver) if ver else {}
    for v in deal.variants:
        if v.price_rub is None:
            continue
        price = now.get(v.id, {}).get("price_rub", v.price_rub)
        crate, _ = rate_on(db, contract_ccy, msk_today())
        out.append({"variant_id": v.id, "name": v.name, "rub_net": round(price, 2), "rub_gross": round(price * (1 + vat / 100), 2),
                    "currency": v.price_currency or "RUB", "net": v.price_amount, "vat_included": v.price_vat_included,
                    "contract_net": round(price / crate, 2), "contract_gross": round(price * (1 + vat / 100) / crate, 2)})
    return out


@router.get("/api/deals/{deal_id}/competitors")
def deal_competitors(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    ccy, ccy_src = contract_currency(db, deal)
    names = _names(db)
    comps = {c.id: c for c in db.scalars(select(Competitor)).all()}
    rows = db.scalars(select(CompetitorOffer).where(CompetitorOffer.deal_id == deal.id).order_by(CompetitorOffer.on.desc(), CompetitorOffer.created_at.desc())).all()
    offers = [_offer_dict(db, o, comps.get(o.competitor_id or ""), ccy, names) for o in rows]
    our = _our(db, deal, ccy)
    by_v = {x["variant_id"]: x for x in our}
    for o in offers:   # отклонение нашего предложения от цены конкурента (без НДС, RUB)
        ref = by_v.get(o["variant_id"]) or (our[0] if len(our) == 1 else None)
        if ref and o["rub_net"]:
            o["our_variant"], o["our_rub_net"] = ref["name"], ref["rub_net"]
            o["diff_rub"] = round(ref["rub_net"] - o["rub_net"], 2)
            o["diff_pct"] = round((ref["rub_net"] - o["rub_net"]) / o["rub_net"] * 100, 1)
    return {"contract_currency": ccy, "contract_currency_source": ccy_src, "vat_pct": float(settings_service.get(db, "sales_vat_pct") or 22.0),
            "our": our, "offers": offers, "competitors": sorted(c.name for c in comps.values())}


@router.post("/api/deals/{deal_id}/competitors", status_code=201)
def add_offer(deal_id: str, body: OfferIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    o = CompetitorOffer(deal_id=deal.id, created_by=user.id, amount=body.amount, on=body.on)
    comp = _apply(db, deal, o, body, user)
    db.add(o)
    db.flush()
    audit(db, user.id, deal.id, "competitor_offer", o.id, "create", after=_snapshot(o, comp))
    db.commit()
    return {"id": o.id}


def _get(db: Session, deal: Deal, oid: str) -> CompetitorOffer:
    o = db.get(CompetitorOffer, oid)
    if o is None or o.deal_id != deal.id:
        raise HTTPException(404)
    return o


@router.patch("/api/deals/{deal_id}/competitors/{oid}")
def edit_offer(deal_id: str, oid: str, body: OfferIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    o = _get(db, deal, oid)
    if o.deleted_at:
        raise HTTPException(409, "Запись удалена — её можно только посмотреть в истории")
    before = _snapshot(o, db.get(Competitor, o.competitor_id) if o.competitor_id else None)
    comp = _apply(db, deal, o, body, user)
    after = _snapshot(o, comp)
    if after != before:
        audit(db, user.id, deal.id, "competitor_offer", o.id, "update", before=before, after=after)
    db.commit()
    return {"id": o.id}


@router.delete("/api/deals/{deal_id}/competitors/{oid}", status_code=204)
def delete_offer(deal_id: str, oid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    o = _get(db, deal, oid)
    if o.deleted_at is None:
        o.deleted_at, o.deleted_by = datetime.now(timezone.utc), user.id
        audit(db, user.id, deal.id, "competitor_offer", o.id, "delete", before=_snapshot(o, db.get(Competitor, o.competitor_id) if o.competitor_id else None))
        db.commit()


@router.get("/api/deals/{deal_id}/competitors/{oid}/history")
def offer_history(deal_id: str, oid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    deal = get_deal_or_404(db, deal_id, user)
    o = _get(db, deal, oid)
    names = _names(db)
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "competitor_offer", AuditLog.entity_id == o.id).order_by(AuditLog.at.desc())).all()
    return [{"at": r.at.isoformat(), "user": names.get(r.user_id or "", ""), "action": r.action, "before": r.before, "after": r.after} for r in rows]


@router.get("/api/competitors")
def competitors(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    key = name_key(q)
    rows = db.scalars(select(Competitor).order_by(Competitor.name)).all()
    counts: dict[str, int] = {}
    for cid in db.scalars(select(CompetitorOffer.competitor_id).where(CompetitorOffer.deleted_at.is_(None), CompetitorOffer.competitor_id.is_not(None))).all():
        counts[cid] = counts.get(cid, 0) + 1
    return [{"id": c.id, "name": c.name, "offers": counts.get(c.id, 0)} for c in rows if not key or key in c.name_key]


@router.get("/api/competitors/{cid}/offers")
def competitor_offers(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD_GD)):
    """История предложений конкурента по всем сделкам — в хронологии, с нашей ценой в той же сделке."""
    c = db.get(Competitor, cid)
    if c is None:
        raise HTTPException(404)
    names = _names(db)
    out = []
    for o in db.scalars(select(CompetitorOffer).where(CompetitorOffer.competitor_id == c.id, CompetitorOffer.deleted_at.is_(None))
                        .order_by(CompetitorOffer.on.desc())).all():
        deal = db.get(Deal, o.deal_id)
        ccy, _ = contract_currency(db, deal)
        d = _offer_dict(db, o, c, ccy, names)
        v = db.get(Variant, o.variant_id) if o.variant_id else next((x for x in deal.variants if x.price_rub is not None), None)
        client = db.get(Client, deal.client_id) if deal.client_id else None
        d.update(deal_number=deal.number, client=client.name if client else "", our_rub_net=v.price_rub if v else None,
                 our_variant=v.name if v else "", diff_pct=round((v.price_rub - d["rub_net"]) / d["rub_net"] * 100, 1) if v and v.price_rub and d["rub_net"] else None)
        out.append(d)
    return {"competitor": {"id": c.id, "name": c.name}, "offers": out}
