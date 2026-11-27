"""Блок логиста: партии, статьи, ТН ВЭД, проверка по истории, завершение блока."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.roles import Role
from ..core.workflow import DealState, DealStatus, block_completed
from ..db import get_db
from ..models import Batch, Deal, HsCode, LogisticsHistory, Position
from ..services import settings_service
from ..services.deal_service import apply_transition, audit, complete_tasks, sla
from ..services.history_check import history_check, hs_check
from .common import get_deal_or_404, serialize
from ..core.clock import today as msk_today

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
    hs_description: str = ""                  # наименование кода, введённого вручную (нет в справочнике) — пополняет справочник
    util_fee_rub: float = 0.0
    util_applies: Optional[bool] = None       # начисляется ли утилизационный сбор; None — по справочнику ТН ВЭД
    util_base_rate_rub: Optional[float] = None    # базовая ставка, ₽
    util_coefficient: Optional[float] = None      # коэффициент по виду техники и коду ТН ВЭД
    certification_rub: float = 0.0
    vat_included: dict[str, bool] = Field(default_factory=dict)   # D-122: {"certification_rub": true} — сумма введена с НДС
    certifications: list[str] = Field(default_factory=list)
    volume_m3: Optional[float] = None
    comments: dict[str, str] = Field(default_factory=dict)   # статья → комментарий к отклонению
    per_unit: bool = True        # D-087: суммы — на 1 машину (в расчёт — × количество позиции)


def _ensure_logistics_stage(deal: Deal) -> None:
    if deal.status not in (DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.REWORK):
        raise HTTPException(409, "Блок логистики редактируется только на этапе расчёта или доработки")


def _history(db: Session, item: str, route: str = "", machine_type: str = "", region: str = "", currency: str = "") -> list[dict]:
    """История статьи — суммы на 1 машину (D-111); сравнивается только с суммами в той же валюте."""
    q = select(LogisticsHistory).where(LogisticsHistory.item_code == item)
    if currency:
        q = q.where(LogisticsHistory.currency == currency.upper())
    rows = db.scalars(q.order_by(LogisticsHistory.on.desc())).all()
    scored = []
    for r in rows:
        score = (r.route == route) + (r.machine_type == machine_type) + (r.region == region)
        scored.append((score, r))
    best = max((s for s, _ in scored), default=0)
    return [{"amount": r.amount, "currency": r.currency, "on": r.on, "deal_id": r.deal_id} for s, r in scored if s == best][:20]


def _batch_name(deal: Deal, name: str, own_id: str = "") -> str:
    """Название партии в сделке уникально (D-147): иначе этапы исполнения двух партий неразличимы. «Партия N» (подставляется экраном
    по числу партий — после удаления может совпасть) → следующий свободный номер; своё название-дубль — отказ."""
    import re
    taken = {b.name.strip().lower() for b in deal.batches if b.id != own_id and b.name}
    if name and name.lower() not in taken:
        return name
    if name and not re.fullmatch(r"партия\s*\d+", name.lower()):
        raise HTTPException(409, f"Партия «{name}» в сделке уже есть — назовите иначе")
    n = 1
    while f"партия {n}" in taken:
        n += 1
    return f"Партия {n}"


@router.post("/batches", status_code=201)
def create_batch(deal_id: str, body: BatchIn, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    data = body.model_dump()
    pos_ids = data.pop("position_ids")
    data["name"] = _batch_name(deal, (data.get("name") or "").strip())
    b = Batch(deal_id=deal.id, **data)
    db.add(b)
    db.flush()
    b.items = _check_batch_items(db, deal, b, body.items)
    for pid in pos_ids:
        p = db.get(Position, pid)
        if p and p.deal_id == deal.id:
            p.batch_id = b.id
    if Role.LOGISTICIAN in user.roles:      # исполнитель логистики сделки — логист; ЗГД, заполнивший партию за него, исполнителем не становится
        deal.logistician_id = deal.logistician_id or user.id
    audit(db, user.id, deal.id, "batch", b.id, "create", after=data)
    _finance_sync(db, b, user.id)
    db.commit()
    return serialize(b)


def _finance_sync(db: Session, b: Batch, user_id: str) -> None:
    """Раздел «Финансы»: график платежей партии — строки обязательств в той же транзакции (D-123)."""
    from ..services.finance.sources import sync_batch
    db.flush()
    sync_batch(db, b, user_id)


def _check_batch_items(db: Session, deal: Deal, b: Batch, items: dict) -> dict:
    # D-087: суммы партии вводятся на 1 машину (в расчёт — × количество машин в партии); прежние партии без флага — на всю партию
    items = {**items, "per_unit": bool(items.get("per_unit", True))}
    thr = settings_service.get(db, "logistics_deviation_pct")
    checks = {}
    comments = items.get("comments", {})
    from ..services import logistics_vat as lv
    vat_pct = float(settings_service.get(db, "sales_vat_pct") or 22.0)
    items["vat_included"] = {k: bool(v) for k, v in (items.get("vat_included") or {}).items() if k in lv.BATCH_ITEMS}
    ft = items.get("freight_terms")
    if ft:   # D-124: условия оплаты перевозки — проверка и нормализация
        try:
            pre = float(ft.get("prepay_pct", 100)); bd = int(ft.get("prepay_days_before") or 0); pd_ = int(ft.get("post_days") or 0)
        except (TypeError, ValueError):
            raise HTTPException(422, "Условия оплаты перевозки: проценты и дни — числа")
        ev = str(ft.get("post_event") or "delivery")
        if not (0 <= pre <= 100) or not (0 <= bd <= 365) or not (0 <= pd_ <= 365) or ev not in ("border", "declaration", "delivery"):
            raise HTTPException(422, "Условия оплаты перевозки: предоплата 0–100 %, дни 0–365, постоплата — после границы, выпуска ДТ или поставки клиенту")
        items["freight_terms"] = {"prepay_pct": pre, "prepay_days_before": bd, "post_event": ev, "post_days": pd_}
    else:
        items.pop("freight_terms", None)
    for key in ("intl_freight", "broker_rub", "rf_delivery_rub"):
        val = items.get(key)
        amount = val.get("amount") if isinstance(val, dict) else val
        cur = (val.get("currency") or "") if isinstance(val, dict) else "RUB"
        if amount and key in lv.BATCH_ITEMS:          # история — суммы без НДС (D-122)
            amount = round(lv.split(amount, lv.included(items, key), vat_pct)[0], 2)
        if amount:
            chk = history_check(amount, _history(db, key, b.route, region=deal.region, currency=cur), thr)
            chk["value"] = amount
            chk["comment"] = comments.get(key, "")
            if chk["needs_comment"] and not chk["comment"]:
                raise HTTPException(422, {"message": f"{ {'intl_freight': 'Международная перевозка', 'broker_rub': 'Брокер и СВХ', 'rf_delivery_rub': 'Доставка по РФ'}.get(key, key)}: отклонение от истории выше {thr} % — нужен комментарий", "check": chk})
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
    data["name"] = _batch_name(deal, (data.get("name") or "").strip(), own_id=b.id)
    for k, v in data.items():
        setattr(b, k, v)
    b.items = _check_batch_items(db, deal, b, body.items)
    for p in deal.positions:
        if p.id in pos_ids:
            p.batch_id = b.id
        elif p.batch_id == b.id:
            p.batch_id = None
    audit(db, user.id, deal.id, "batch", b.id, "update", before, data)
    _finance_sync(db, b, user.id)
    db.commit()
    return serialize(b)


@router.delete("/batches/{batch_id}", status_code=204)
def delete_batch(deal_id: str, batch_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    """Лишняя партия (например, созданная пустой): позиции становятся нераспределёнными, строки платежей выводятся из итогов."""
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    b = db.get(Batch, batch_id)
    if b is None or b.deal_id != deal.id:
        raise HTTPException(404)
    for p in deal.positions:
        if p.batch_id == b.id:
            p.batch_id = None
    from ..services.finance.sources import sync_batch
    sync_batch(db, b, user.id, deleted=True)
    audit(db, user.id, deal.id, "batch", b.id, "delete", before=serialize(b))
    db.delete(b)
    db.commit()


def delivery_cost(items: dict) -> float:
    """Сумма введённых статей доставки партии (в валютах ввода — только для проверки «введено ли что-то»)."""
    it = items or {}
    money = lambda k: float(((it.get(k) or {}).get("amount") or 0) if isinstance(it.get(k), dict) else (it.get(k) or 0))   # noqa: E731
    return money("door_to_door") + money("intl_freight") + money("rf_delivery_rub") + money("broker_rub")


def batch_problems(deal: Deal) -> list[dict]:
    """Партии, расходы которых не попадут в расчёт себестоимости: без позиций (расходы есть) или с позициями без стоимости доставки."""
    out = []
    for b in deal.batches:
        members = [p for p in deal.positions if p.batch_id == b.id]
        cost = delivery_cost(b.items)
        if not members and cost:
            out.append({"batch": b.name, "kind": "no_positions",
                        "message": f"Партия «{b.name}»: расходы введены, но позиций в партии нет — в себестоимость они не попадут. Выберите позиции или удалите партию."})
        elif members and not cost:
            out.append({"batch": b.name, "kind": "no_cost",
                        "message": f"Партия «{b.name}»: стоимость доставки не введена — в себестоимости доставка, брокер и страховка от партии равны нулю."})
    return out


class CompleteIn(BaseModel):
    no_delivery_cost: bool = False      # логист подтверждает: доставка в этой сделке действительно не оплачивается нами


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
    if body.hs_code and ref is None and body.hs_description.strip():
        # ручной код ТН ВЭД с наименованием — в справочник (ставки — введённые логистом)
        import re as _re
        code = _re.sub(r"\D", "", body.hs_code)
        if len(code) == 10:
            db.add(HsCode(code=code, description=body.hs_description.strip(), duty_pct=body.duty_pct or 0.0,
                          vat_pct=body.vat_pct if body.vat_pct is not None else 22.0, util_fee=bool(body.util_applies), certification=[],
                          valid_from=msk_today()))
            data["hs_code"] = code
            audit(db, user.id, deal.id, "hs_code", code, "manual_add", after={"description": body.hs_description.strip()})
    # код ТН ВЭД — в карточку оборудования каталога (модель позиции): в следующих сделках подставляется логисту
    if body.hs_code:
        from ..models import EquipmentModel
        import re as _re2
        code_ = _re2.sub(r"\D", "", body.hs_code)
        m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model))
        if m is not None and len(code_) == 10:
            m.default_hs_code = code_
            m.hs_info = {"code": code_, "description": body.hs_description.strip() or (ref_row.description if ref else ""),
                         "duty_pct": body.duty_pct, "vat_pct": body.vat_pct, "util_applies": body.util_applies, "on": msk_today().isoformat(), "deal": deal.number}
    chk = hs_check(body.duty_pct, body.vat_pct, ref)
    if chk.get("mismatch") and not body.hs_manual_comment:
        raise HTTPException(422, {"message": "Ставка отличается от справочника ТН ВЭД — укажите комментарий", "hs_check": chk})
    if ref:
        data["duty_pct"] = body.duty_pct if body.duty_pct is not None else ref["duty_pct"]
        data["vat_pct"] = body.vat_pct if body.vat_pct is not None else ref["vat_pct"]
    else:
        data["duty_pct"] = body.duty_pct or 0.0
        data["vat_pct"] = body.vat_pct if body.vat_pct is not None else 22.0
    data["hs_check"] = chk
    # утилизационный сбор: начисляется → базовая ставка × коэффициент; не начисляется → 0
    applies = body.util_applies if body.util_applies is not None else bool(body.util_fee_rub or (ref and ref.get("util_fee")))
    data["util_applies"] = applies
    if applies and body.util_applies is None and body.util_fee_rub and body.util_coefficient is None:
        pass   # сумма введена напрямую (прежний способ) — оставляем как есть
    elif applies:
        uref = settings_service.util_reference(settings_service.get_all(db), body.hs_code, pos.machine_type)
        base = body.util_base_rate_rub if body.util_base_rate_rub is not None else uref["base_rate_rub"]
        coef = body.util_coefficient if body.util_coefficient is not None else uref["coefficient"]
        if coef is None:
            raise HTTPException(422, {"message": "Утилизационный сбор начисляется — укажите коэффициент для вида техники и кода ТН ВЭД"})
        data["util_base_rate_rub"], data["util_coefficient"] = base, coef
        data["util_fee_rub"] = round(base * coef, 2)
    else:
        data["util_base_rate_rub"], data["util_coefficient"], data["util_fee_rub"] = body.util_base_rate_rub, body.util_coefficient, 0.0
    # проверка по истории
    checks = {}
    for key in ("domestic_logistics", "export_formalities"):
        amt = data[key].get("amount")
        if amt:
            c = history_check(amt, _history(db, key, machine_type=pos.machine_type, currency=data[key].get("currency") or ""), thr)
            c["value"], c["comment"] = amt, body.comments.get(key, "")
            if c["needs_comment"] and not c["comment"]:
                raise HTTPException(422, {"message": f"{ {'domestic_logistics': 'Внутрикитайская логистика', 'export_formalities': 'Экспортные формальности КНР'}[key]}: отклонение от истории выше {thr} % — нужен комментарий", "check": c})
            checks[key] = c
    from ..services import logistics_vat as lv
    data["vat_included"] = {k: bool(v) for k, v in (body.vat_included or {}).items() if k in lv.POSITION_ITEMS}
    if body.certification_rub:
        cert_net = round(lv.split(body.certification_rub, lv.included(data, "certification_rub"), float(settings_service.get(db, "sales_vat_pct") or 22.0))[0], 2)
        c = history_check(cert_net, _history(db, "certification_rub", machine_type=pos.machine_type, currency="RUB"), thr)
        c["value"], c["comment"] = cert_net, body.comments.get("certification_rub", "")
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
def complete(deal_id: str, body: Optional[CompleteIn] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(LOGIST)):
    deal = get_deal_or_404(db, deal_id, user)
    _ensure_logistics_stage(deal)
    missing = [p.designation for p in deal.positions if not (p.logistics or {}).get("hs_code")]
    if missing:
        raise HTTPException(422, {"message": "Не задан код ТН ВЭД", "positions": missing})
    unbatched = [p for p in deal.positions if p.batch_id is None]
    if unbatched:   # распределять позиции по партиям не нужно: без партии — в первую (или в общую партию сделки)
        b0 = deal.batches[0] if deal.batches else None
        if b0 is None:
            b0 = Batch(deal_id=deal.id, name="Партия 1", items={})
            db.add(b0)
            db.flush()
        for p in unbatched:
            p.batch_id = b0.id
        db.flush()
        db.refresh(deal)
    # расходы партии без позиций теряются, партия без стоимости доставки даёт ноль в себестоимости — не отправляем молча
    probs = batch_problems(deal)
    lost = [x for x in probs if x["kind"] == "no_positions"]
    if lost:
        db.rollback()
        raise HTTPException(422, {"message": lost[0]["message"], "problems": probs})
    if any(x["kind"] == "no_cost" for x in probs) and not (body and body.no_delivery_cost):
        db.rollback()
        raise HTTPException(422, {"message": "; ".join(x["message"] for x in probs), "problems": probs, "confirm": "no_delivery_cost"})
    st = DealState(status=deal.status, service_flag=deal.service_flag, logistics_done=deal.logistics_done, service_done=deal.service_done)
    tr = block_completed(st, "logistics", msk_today(), sla(db))
    deal.logistics_done = True
    # история логистики пополняется из каждого расчёта: суммы на 1 машину (D-111), по сделке — последний расчёт
    from ..services.logistics_history import record_deal
    record_deal(db, deal)
    complete_tasks(db, deal, block="logistics")
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value}


@router.get("/positions/{pos_id}/util-reference")
def util_reference(deal_id: str, pos_id: str, hs_code: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подсказка логисту по утилизационному сбору: начисляется ли по справочнику ТН ВЭД, базовая ставка, коэффициент."""
    deal = get_deal_or_404(db, deal_id, user)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404)
    code = hs_code or (pos.logistics or {}).get("hs_code") or ""
    row = db.scalar(select(HsCode).where(HsCode.code == code, HsCode.valid_to.is_(None))) if code else None
    ref = settings_service.util_reference(settings_service.get_all(db), code, pos.machine_type)
    return {**ref, "hs_code": code, "hs_found": row is not None, "applies_by_hs": bool(row.util_fee) if row else None, "machine_type": pos.machine_type}


@router.get("/defaults")
def logistics_defaults(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Нормативы для экрана логиста: доля единой стоимости доставки до границы РФ."""
    get_deal_or_404(db, deal_id, user)
    return {"door_to_door_border_pct": float(settings_service.get(db, "door_to_door_border_pct") or 10.0)}
