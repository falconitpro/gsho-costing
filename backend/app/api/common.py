from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import Role, filter_payload
from ..core.workflow import LOGISTICS_VISIBLE_FROM, DealStatus
from ..models import Deal


def payment_terms_set(deal: Deal) -> bool:
    """Задача 1: условия оплаты клиента заданы КД явно. Без альтернатив — на сделке; с альтернативами — у каждого предложения."""
    if deal.has_alternatives:
        offers = [v for v in deal.variants if not v.is_default]
        return bool(offers) and all(v.client_payments for v in offers)
    return bool(deal.client_payments)


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
    if user.has(Role.SERVICE_HEAD, Role.PURCHASING) and any(p.local_option_rows for p in deal.positions) and deal.status not in (DealStatus.DRAFT,):
        return True   # задача 6: опции РФ оценивает сервис, в будущем — закупки
    if user.has(Role.OBSERVER):
        scope = user.observer_scope or {}
        return not scope.get("deal_ids") or deal.id in scope["deal_ids"]
    return False


def get_deal_or_404(db: Session, deal_id: str, user: CurrentUser) -> Deal:
    deal = db.get(Deal, deal_id)
    if deal is None or not can_see_deal(user, deal):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сделка не найдена")
    return deal


def position_payload(p) -> dict[str, Any]:
    d = serialize(p)
    d["tech_param_rows"] = [serialize(r) for r in (p.tech_param_rows or [])]        # задача 4
    d["local_options"] = [serialize(o) for o in (p.local_option_rows or [])]        # задача 6
    d["images"] = [serialize(i) for i in (p.image_rows or [])]                      # задача 10
    # код ТН ВЭД из карточки оборудования каталога — подсказка логисту, пока в позиции код не введён
    if not (p.logistics or {}).get("hs_code"):
        from sqlalchemy import select
        from sqlalchemy.orm import object_session
        from ..models import EquipmentModel
        db = object_session(p)
        m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == p.factory_id, EquipmentModel.model == p.model)) if db else None
        if m is not None and m.default_hs_code:
            d["catalog_hs"] = {**(m.hs_info or {}), "code": m.default_hs_code}
    return d


def deal_payload(deal: Deal, user: CurrentUser, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data = serialize(deal)
    data["client_name"] = deal.client.name if deal.client else None
    data["positions"] = [position_payload(p) for p in deal.positions]
    data["variants"] = [serialize(v) for v in deal.variants]
    data["batches"] = [serialize(b) for b in deal.batches]
    data["offer"] = next((serialize(v) for v in deal.variants if v.is_default), None)   # задача 5: одно предложение без выбора вариантов
    data["payment_terms_set"] = payment_terms_set(deal)
    if extra:
        data.update(extra)
    # менеджер видит цену только после утверждения
    if user.has(Role.MANAGER) and not user.has(Role.CD, Role.FD, Role.GD) and deal.status in (
            DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY,
            DealStatus.CD_APPROVAL, DealStatus.FD_VISA, DealStatus.GD_VISA, DealStatus.REWORK):
        for v in data["variants"] + ([data["offer"]] if data["offer"] else []):
            v.pop("price_rub", None)
    return filter_payload(data, user)
