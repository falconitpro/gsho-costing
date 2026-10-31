from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import Role, filter_payload
from ..core.workflow import LOGISTICS_VISIBLE_FROM, DealStatus
from ..models import Deal


def serialize(obj, exclude: set[str] | None = None) -> dict[str, Any]:
    """ORM → dict (столбцы таблицы)."""
    out = {}
    for c in obj.__table__.columns:
        if exclude and c.name in exclude:
            continue
        v = getattr(obj, c.name)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif hasattr(v, "value"):
            v = v.value
        out[c.name] = v
    return out


def can_see_deal(user: CurrentUser, deal: Deal) -> bool:
    if user.has(Role.CD, Role.FD, Role.GD):
        return True
    if user.has(Role.MANAGER) and deal.manager_id == user.id:
        return True
    if user.has(Role.LOGISTICIAN) and deal.status in LOGISTICS_VISIBLE_FROM:
        return True
    if user.has(Role.SERVICE_HEAD) and deal.service_flag and deal.status not in (DealStatus.DRAFT,):
        return True
    if user.has(Role.OBSERVER):
        scope = user.observer_scope or {}
        return not scope.get("deal_ids") or deal.id in scope["deal_ids"]
    return False


def get_deal_or_404(db: Session, deal_id: str, user: CurrentUser) -> Deal:
    deal = db.get(Deal, deal_id)
    if deal is None or not can_see_deal(user, deal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сделка не найдена")
    return deal


def deal_payload(deal: Deal, user: CurrentUser, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data = serialize(deal)
    data["client_name"] = deal.client.name if deal.client else None
    data["positions"] = [serialize(p) for p in deal.positions]
    data["variants"] = [serialize(v) for v in deal.variants]
    data["batches"] = [serialize(b) for b in deal.batches]
    if extra:
        data.update(extra)
    # менеджер видит цену только после утверждения
    if user.has(Role.MANAGER) and not user.has(Role.CD, Role.FD, Role.GD) and deal.status in (
            DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY,
            DealStatus.CD_APPROVAL, DealStatus.FD_VISA, DealStatus.GD_VISA, DealStatus.REWORK):
        for v in data["variants"]:
            v.pop("price_rub", None)
    return filter_payload(data, user.roles)
