"""Блок логиста: партии, статьи, ТН ВЭД, проверка по истории, завершение блока."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..core.workflow import DealState, DealStatus, block_completed
from ..db import get_db
from ..models import Batch, Deal, HsCode, LogisticsHistory, Position
from ..services import settings_service
from ..services.deal_service import apply_transition, audit, complete_tasks, sla
from ..services.history_check import history_check, hs_check
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api/deals/{deal_id}/logistics", tags=["logistics"])
LOGIST = require_roles(Role.LOGISTICIAN, Role.GD)

ITEM_CODES = {"domestic_logistics", "export_formalities", "intl_freight", "insurance", "broker_rub", "rf_delivery_rub", "certification_rub"}


class BatchIn(BaseModel):
    name: str = "Партия 1"
    route: str = "завод → Маньчжурия → Забайкальск → пункт поставки"
    transport_units: int = 1
    weight_kg: Optional[float] = None
    volume_m3: Optional[float] = None
    ready_from: Optional[date] = None
    ready_to: Optional[date] = None
    transit_days: int = 14
    customs_days: int = 5
    rf_delivery_days: int = 5                      # доставка по РФ после выпуска ДТ (задача 3)
    border_planned: Optional[date] = None          # плановая дата пересечения границы КНР–РФ (CMR) (задача 1)
    border_actual: Optional[date] = None           # факт: CMR с отметкой таможни
    allocation: str = "cost"
    items: dict = Field(default_factory=dict)      # intl_freight{amount,currency}, insurance_pct|insurance_amount, broker_rub, rf_delivery_rub, comments{}
    payments: list[dict] = Field(default_factory=list)
    position_ids: list[str] = Field(default_factory=list)


class PositionLogisticsIn(BaseModel):
    domestic_logistics: dict = Field(default_factory=lambda: {"amount": 0, "currency": "CNY"})
    export_formalities: dict = Field(default_factory=lambda: {"amount": 0, "currency": "CNY"})
    hs_code: str = ""
    duty_pct: Optional[float] = None
    vat_pct: Optional[float] = None
    hs_manual_comment: str = ""
    util_fee_rub: float = 0.0
    certification_rub: float = 0.0
    certifications: list[str] = Field(default_factory=list)
    volume_m3: Optional[float] = None
    comments: dict[str, str] = Field(default_factory=dict)   # статья → комментарий к отклонению


def _ensure_logistics_stage(deal: Deal) -> None:
    if deal.status not in (DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.REWORK):
        raise HTTPException(409, "Блок логистики редактируется только на этапе расчёта или доработки")


def _history(db: Session, item: str, route: str = "", machine_type: str = "", region: str = "") -> list[dict]:
    q = select(LogisticsHistory).where(LogisticsHistory.item_code == item)
    rows = db.scalars(q.order_by(LogisticsHistory.on.desc())).all()
    scored = []
    for r in rows:
        score = (r.route == route) + (r.machine_type == machine_type) + (r.region == region)
        scored.append((score, r))
    best = max((s for s, _ in scored), default=0)
    return [{"amount": r.amount, "currency": r.currency, "on": r.on, "deal_id": r.deal_id} for s, r in scored if s == best][:20]


@router.post("/batches", status_code=201)
def create_batch(deal_id: str, body: BatchIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    data = body.model_dump()
    pos_ids = data.pop("position_ids")
    b = Batch(deal_id=deal.id, **data)
    db.add(b)
    db.flush()
    b.items = _check_batch_items(db, deal, b, body.items)
    for pid in pos_ids:
        p = db.get(Position, pid)
        if p and p.deal_id == deal.id:
            p.batch_id = b.id
    deal.logistician_id = deal.logistician_id or user.id
    audit(db, user.id, deal.id, "batch", b.id, "create", after=data)
    db.commit()
    return serialize(b)


def _check_batch_items(db: Session, deal: Deal, b: Batch, items: dict) -> dict:
    thr = settings_service.get(db, "logistics_deviation_pct")
    checks = {}
    comments = items.get("comments", {})
    for key in ("intl_freight", "broker_rub", "rf_delivery_rub"):
        val = items.get(key)
        amount = val.get("amount") if isinstance(val, dict) else val
        if amount:
            chk = history_check(amount, _history(db, key, b.route, region=deal.region), thr)
            chk["value"] = amount
            chk["comment"] = comments.get(key, "")
            if chk["needs_comment"] and not chk["comment"]:
                raise HTTPException(422, {"message": f"Статья {key}: отклонение от истории выше {thr} % — нужен комментарий", "check": chk})
            checks[key] = chk
    return {**items, "checks": checks}


@router.patch("/batches/{batch_id}")
def update_batch(deal_id: str, batch_id: str, body: BatchIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    b = db.get(Batch, batch_id)
    if b is None or b.deal_id != deal.id:
        raise HTTPException(404)
    before = serialize(b)
    data = body.model_dump()
    pos_ids = data.pop("position_ids")
    for k, v in data.items():
        setattr(b, k, v)
    b.items = _check_batch_items(db, deal, b, body.items)
    for p in deal.positions:
        if p.id in pos_ids:
            p.batch_id = b.id
        elif p.batch_id == b.id:
            p.batch_id = None
    audit(db, user.id, deal.id, "batch", b.id, "update", before, data)
    db.commit()
    return serialize(b)


@router.put("/positions/{pos_id}")
def update_position_logistics(deal_id: str, pos_id: str, body: PositionLogisticsIn, db: Session = Depends(get_db),
                              user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404)
    thr = settings_service.get(db, "logistics_deviation_pct")
    data = body.model_dump()
    # ТН ВЭД: подстановка ставок из справочника, ручная ставка — с комментарием
    ref = None
    if body.hs_code:
        ref_row = db.scalar(select(HsCode).where(HsCode.code == body.hs_code, HsCode.valid_to.is_(None)))
        if ref_row:
            ref = {"duty_pct": ref_row.duty_pct, "vat_pct": ref_row.vat_pct, "util_fee": ref_row.util_fee, "certification": ref_row.certification}
    chk = hs_check(body.duty_pct, body.vat_pct, ref)
    if chk.get("mismatch") and not body.hs_manual_comment:
        raise HTTPException(422, {"message": "Ставка отличается от справочника ТН ВЭД — укажите комментарий", "hs_check": chk})
    if ref:
        data["duty_pct"] = body.duty_pct if body.duty_pct is not None else ref["duty_pct"]
        data["vat_pct"] = body.vat_pct if body.vat_pct is not None else ref["vat_pct"]
    else:
        data["duty_pct"] = body.duty_pct or 0.0
        data["vat_pct"] = body.vat_pct if body.vat_pct is not None else 20.0
    data["hs_check"] = chk
    # проверка по истории
    checks = {}
    for key in ("domestic_logistics", "export_formalities"):
        amt = data[key].get("amount")
        if amt:
            c = history_check(amt, _history(db, key, machine_type=pos.machine_type), thr)
            c["value"], c["comment"] = amt, body.comments.get(key, "")
            if c["needs_comment"] and not c["comment"]:
                raise HTTPException(422, {"message": f"Статья {key}: отклонение выше {thr} % — нужен комментарий", "check": c})
            checks[key] = c
    if body.certification_rub:
        c = history_check(body.certification_rub, _history(db, "certification_rub", machine_type=pos.machine_type), thr)
        c["value"], c["comment"] = body.certification_rub, body.comments.get("certification_rub", "")
        if c["needs_comment"] and not c["comment"]:
            raise HTTPException(422, {"message": "Сертификация: отклонение выше порога — нужен комментарий", "check": c})
        checks["certification_rub"] = c
    data["checks"] = checks
    before = pos.logistics
    pos.logistics = data
    audit(db, user.id, deal.id, "position", pos.id, "logistics", before, data)
    db.commit()
    return {"logistics": pos.logistics}


@router.get("/history/{item}")
def item_history(deal_id: str, item: str, route: str = "", machine_type: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    rows = _history(db, item, route, machine_type)
    return {"last5": rows[:5], "avg": round(sum(r["amount"] for r in rows[:5]) / len(rows[:5]), 2) if rows else None}


@router.post("/complete")
def complete(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    missing = [p.designation for p in deal.positions if not (p.logistics or {}).get("hs_code")]
    if missing:
        raise HTTPException(422, {"message": "Не задан код ТН ВЭД", "positions": missing})
    if any(p.batch_id is None for p in deal.positions):
        raise HTTPException(422, "Все позиции должны быть распределены по партиям")
    st = DealState(status=deal.status, service_flag=deal.service_flag, logistics_done=deal.logistics_done, service_done=deal.service_done)
    tr = block_completed(st, "logistics", date.today(), sla(db))
    deal.logistics_done = True
    # история логистики пополняется из каждого расчёта
    for b in deal.batches:
        for key in ("intl_freight", "broker_rub", "rf_delivery_rub"):
            val = (b.items or {}).get(key)
            amount, cur = (val.get("amount"), val.get("currency")) if isinstance(val, dict) else (val, "RUB")
            if amount:
                db.add(LogisticsHistory(item_code=key, route=b.route, region=deal.region, amount=amount, currency=cur,
                                        per_unit=amount / max(b.transport_units, 1), on=date.today(), deal_id=deal.id, batch_id=b.id))
    for p in deal.positions:
        for key in ("domestic_logistics", "export_formalities"):
            val = (p.logistics or {}).get(key) or {}
            if val.get("amount"):
                db.add(LogisticsHistory(item_code=key, machine_type=p.machine_type, amount=val["amount"], currency=val.get("currency", "CNY"),
                                        on=date.today(), deal_id=deal.id))
    complete_tasks(db, deal, block="logistics")
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value}
