"""Справочники и база клиентов (раздел 11)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.roles import Role, filter_payload
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Client, Deal, DealVersion, EquipmentModel, Factory, FactoryPriceHistory, HsCode, PaymentScheme
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/refs", tags=["references"])


class ClientIn(BaseModel):
    name: str
    inn: str = Field(min_length=10, max_length=12)
    address: str = ""
    sites: list[dict] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    default_payment_scheme_id: Optional[str] = None


@router.get("/clients")
def clients(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(Client).order_by(Client.name)
    if q:
        stmt = stmt.where(or_(Client.name.ilike(f"%{q}%"), Client.inn.like(f"{q}%")))
    return [serialize(c) for c in db.scalars(stmt).all()]


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.GD, Role.ADMIN))):
    if db.scalar(select(Client).where(Client.inn == body.inn)):
        raise HTTPException(409, "Клиент с таким ИНН уже есть")
    c = Client(manager_id=user.id, **body.model_dump())
    db.add(c)
    db.flush()
    audit(db, user.id, None, "client", c.id, "create", after=body.model_dump())
    db.commit()
    return serialize(c)


@router.get("/clients/{client_id}")
def client_card(client_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка клиента: воронка, суммы и средняя маржа по закрытым (видно КД/ФД/ГД), последние КП."""
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    deals = db.scalars(select(Deal).where(Deal.client_id == c.id).order_by(Deal.created_at.desc())).all()
    if user.has(Role.MANAGER) and not user.has(Role.CD, Role.FD, Role.GD):
        deals = [d for d in deals if d.manager_id == user.id]
    funnel = {"requests": len(deals), "kp_sent": sum(d.status in (DealStatus.KP_SENT, DealStatus.WON, DealStatus.LOST) for d in deals),
              "won": sum(d.status == DealStatus.WON for d in deals)}
    margins, sums = [], 0.0
    last_kp = []
    for d in deals:
        for v in d.variants:
            if v.price_rub:
                last_kp.append({"deal": d.number, "variant": v.name, "price_rub": v.price_rub, "on": d.updated_at.isoformat() if d.updated_at else None,
                                "status": d.status.value, "models": [p.model for p in d.positions if p.id in v.position_ids]})
        if d.status == DealStatus.WON and d.approved_version_id:
            ver = db.get(DealVersion, d.approved_version_id)
            if ver:
                for vid, scen in ver.calc_result["variants"].items():
                    m = scen["base"].get("margin_pct")
                    if m is not None:
                        margins.append(m)
                        sums += scen["base"].get("price_rub") or 0
    data = serialize(c)
    from ..services.catalog_service import client_kp_history
    kp_history = client_kp_history(db, c.id) if user.can("client_price_history") else []
    totals = {"kp_count": len(kp_history), "won": sum(1 for h in kp_history if h["outcome"] == "won"),
              "sum_rub": round(sum((h["latest"]["total_rub"] or 0) for h in kp_history), 2)}
    data.update({"funnel": funnel, "deals": [{"id": d.id, "number": d.number, "status": d.status.value, "outcome": d.outcome,
                                             "outcome_reason": d.outcome_reason} for d in deals],
                 "last_kp": last_kp[:10], "kp_history": kp_history, "kp_totals": totals,
                 "avg_margin_pct": round(sum(margins) / len(margins), 2) if margins else None, "sum_won_rub": sums})
    return filter_payload(data, user)


@router.get("/factories")
def factories(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [serialize(f) for f in db.scalars(select(Factory).order_by(Factory.name)).all()]


class FactoryIn(BaseModel):
    name: str
    name_zh: str = ""
    address: str = ""
    country: str = "CN"
    contacts: list[dict] = Field(default_factory=list)
    wecom_external_ids: list[str] = Field(default_factory=list)
    payment_terms: list[dict] = Field(default_factory=list)
    typical_production_days: Optional[int] = None


@router.post("/factories", status_code=201)
def create_factory(body: FactoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.GD, Role.ADMIN))):
    f = Factory(**body.model_dump())
    db.add(f)
    db.commit()
    return serialize(f)


@router.get("/factories/{factory_id}/prices")
def factory_prices(factory_id: str, model: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """История цен и сроков завода (задача 7): видят менеджер, КД, ГД (настраивается в уровнях доступа)."""
    if not user.can("factory_price_history"):
        raise HTTPException(403, "История цен завода закрыта для роли")
    stmt = select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == factory_id).order_by(FactoryPriceHistory.on.desc())
    if model:
        stmt = stmt.where(FactoryPriceHistory.model == model)
    return [serialize(h) for h in db.scalars(stmt.limit(50)).all()]


@router.get("/equipment")
def equipment(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(EquipmentModel).order_by(EquipmentModel.model)
    if q:
        stmt = stmt.where(or_(EquipmentModel.model.ilike(f"%{q}%"), EquipmentModel.machine_type.ilike(f"%{q}%")))
    rows = db.scalars(stmt).all()
    out = [serialize(m) for m in rows]
    if not user.has(Role.SERVICE_HEAD, Role.CD, Role.FD, Role.GD, Role.ADMIN):
        for m in out:
            m.pop("maintenance_regulations", None)
    return out


@router.get("/hs")
def hs_search(q: str = Query(min_length=2), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(HsCode).where(HsCode.valid_to.is_(None), or_(HsCode.code.like(f"{q}%"), HsCode.description.ilike(f"%{q}%"))).limit(30)
    return [serialize(h) for h in db.scalars(stmt).all()]


@router.get("/payment-schemes")
def payment_schemes(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [serialize(p) for p in db.scalars(select(PaymentScheme)).all()]


class SchemeIn(BaseModel):
    name: str
    lines: list[dict]


@router.post("/payment-schemes", status_code=201)
def create_scheme(body: SchemeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.GD, Role.ADMIN))):
    p = PaymentScheme(**body.model_dump())
    db.add(p)
    db.commit()
    return serialize(p)
