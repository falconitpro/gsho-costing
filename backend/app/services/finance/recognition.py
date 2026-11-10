"""Признание выручки и расхода (docs/finance/02, раздел 4.7; 04, раздел 9; D-014, D-028, D-133).

- новое событие требует основания: первичный документ (вид, номер, дата) либо хозяйственное событие с комментарием;
- сумма в валюте требует явного курса RUB, даты и источника — `amount_rub` фиксируется в событии;
- поступление/списание денег признание НЕ создаёт;
- старый факт по статье план/факта (`PlanFactEntry.kind=cost_item`) — одно legacy-событие `legacy_business_event` по `source=planfact:{id}`,
  без выдуманного номера документа; повтор не дублирует; удаление записи план/факта сторнирует событие;
- исправление — сторно (status=reversed с причиной), запись не удаляется.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...calc.types import COST_ITEMS
from ...models import FinanceObligation, FinanceRecognitionEvent, PlanFactEntry
from ..deal_service import audit
from .money import dec, rate as to_rate, s

ITEM_CODES = {k for _, k, _ in COST_ITEMS}
BASIS = {"primary_document": "первичный документ", "business_event": "хозяйственное событие", "legacy_business_event": "старый факт план/факта (без документа)"}


def _rub(amount: Decimal, currency: str, fx_rate, fx_on, fx_source) -> tuple[Decimal, Optional[Decimal]]:
    if currency == "RUB":
        return amount, None
    try:
        r = to_rate(fx_rate)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if r is None or not fx_on or not (fx_source or "").strip():
        raise HTTPException(422, f"Сумма в {currency}: укажите курс RUB за единицу, дату и источник курса")
    return dec(amount * r), r


def create(db: Session, user, deal, body: dict, key: str) -> FinanceRecognitionEvent:
    dup = db.scalar(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.idempotency_key == key))
    if dup is not None:
        return dup
    kind = body.get("kind")
    if kind not in ("revenue", "expense"):
        raise HTTPException(422, "kind: revenue | expense")
    code = body.get("cost_item_code") or None
    if kind == "expense" and code not in ITEM_CODES:
        raise HTTPException(422, "Для расхода укажите статью себестоимости")
    if kind == "revenue":
        code = None
    amount = dec(body.get("amount"))
    if amount is None or amount <= 0:
        raise HTTPException(422, "Сумма признания — положительная")
    currency = str(body.get("currency") or "RUB").upper()
    amount_rub, r = _rub(amount, currency, body.get("fx_rate"), body.get("fx_on"), body.get("fx_source"))
    basis = body.get("basis_type") or "primary_document"
    if basis not in ("primary_document", "business_event"):
        raise HTTPException(422, "Основание: первичный документ или хозяйственное событие")
    if basis == "primary_document" and not (body.get("document_type") and body.get("document_number") and body.get("document_date")):
        raise HTTPException(422, "Первичный документ: укажите вид, номер и дату (УПД, акт, накладная, ГТД…)")
    if basis == "business_event" and len((body.get("comment") or "").strip()) < 3:
        raise HTTPException(422, "Хозяйственное событие: опишите его в комментарии")
    ob_id = body.get("obligation_id") or None
    if ob_id:
        o = db.get(FinanceObligation, ob_id)
        if o is None or o.deal_id != deal.id:
            raise HTTPException(422, "Обязательство не относится к этой сделке")
    ev = FinanceRecognitionEvent(kind=kind, deal_id=deal.id, obligation_id=ob_id, cost_item_code=code, position_id=body.get("position_id") or None,
                                 amount=amount, currency=currency, fx_rate=r, fx_source=(body.get("fx_source") or "")[:64], fx_on=body.get("fx_on"), amount_rub=amount_rub,
                                 recognized_on=body.get("recognized_on") or date.today(), basis_type=basis, document_type=(body.get("document_type") or "")[:64],
                                 document_number=(body.get("document_number") or "")[:64], document_date=body.get("document_date"),
                                 attachment_id=body.get("attachment_id") or None, source_type="manual", source_id=f"manual:{key}"[:64], idempotency_key=key,
                                 comment=body.get("comment") or "", created_by=user.id)
    db.add(ev)
    db.flush()
    audit(db, user.id, deal.id, "finance_recognition", ev.id, "create", after={"kind": kind, "item": code, "amount": s(amount), "currency": currency, "amount_rub": s(amount_rub),
                                                                              "basis": basis, "document": f"{ev.document_type} {ev.document_number}".strip(), "idempotency_key": key})
    return ev


def reverse(db: Session, user, ev: FinanceRecognitionEvent, reason: str) -> FinanceRecognitionEvent:
    if ev.status == "reversed":
        return ev
    if len((reason or "").strip()) < 3:
        raise HTTPException(422, "Укажите причину сторно признания")
    ev.status, ev.reversal_reason, ev.reversed_by, ev.reversed_at = "reversed", reason.strip(), user.id if user else None, datetime.now(timezone.utc)
    audit(db, user.id if user else None, ev.deal_id, "finance_recognition", ev.id, "reverse", before={"status": "posted"}, after={"status": "reversed", "reason": reason.strip()})
    return ev


# ------------------------------------------------------------------ старый факт по статьям план/факта

def from_planfact(db: Session, user_id: Optional[str], e: PlanFactEntry) -> Optional[FinanceRecognitionEvent]:
    """Legacy cost_item → одно событие расхода (D-028). Повтор — то же событие. payment_in/out сюда не попадают (это не признание)."""
    if e.kind != "cost_item" or e.cost_item_code not in ITEM_CODES or not e.amount or e.amount <= 0:
        return None
    ex = db.scalar(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.source_type == "planfact", FinanceRecognitionEvent.source_id == e.id))
    if ex is not None:
        return ex
    amount = dec(e.amount)
    cur = (e.currency or "RUB").upper()
    r = None if cur == "RUB" else (dec(e.fx_rate) if e.fx_rate else None)
    amount_rub = amount if cur == "RUB" else (dec(amount * Decimal(str(e.fx_rate))) if e.fx_rate else amount)   # курс обязателен при вводе план/факта
    ev = FinanceRecognitionEvent(kind="expense", deal_id=e.deal_id, cost_item_code=e.cost_item_code, position_id=e.position_id, amount=amount, currency=cur,
                                 fx_rate=Decimal(str(e.fx_rate)) if r is not None else None, fx_source="курс из план/факта" if r is not None else "", fx_on=e.on if r is not None else None,
                                 amount_rub=amount_rub, recognized_on=e.on, basis_type="legacy_business_event", source_type="planfact", source_id=e.id,
                                 comment=e.comment or "", created_by=e.entered_by)
    db.add(ev)
    db.flush()
    audit(db, user_id, e.deal_id, "finance_recognition", ev.id, "legacy_create", after={"planfact_id": e.id, "item": e.cost_item_code, "amount_rub": s(amount_rub)})
    return ev


def planfact_deleted(db: Session, user, e: PlanFactEntry) -> None:
    ev = db.scalar(select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.source_type == "planfact", FinanceRecognitionEvent.source_id == e.id))
    if ev is not None:
        reverse(db, user, ev, "запись план/факта удалена")


def migrate_planfact(db: Session, user_id: Optional[str]) -> int:
    """Однократная (идемпотентная) миграция старых фактов по статьям; в составе POST /api/finance/sync."""
    done = {sid for (sid,) in db.execute(select(FinanceRecognitionEvent.source_id).where(FinanceRecognitionEvent.source_type == "planfact")).all()}
    n = 0
    for e in db.scalars(select(PlanFactEntry).where(PlanFactEntry.kind == "cost_item")).all():
        if e.id not in done and from_planfact(db, user_id, e) is not None:
            n += 1
    return n


def dto(ev: FinanceRecognitionEvent) -> dict:
    title = next((t for _, k, t in COST_ITEMS if k == ev.cost_item_code), "")
    return {"id": ev.id, "kind": ev.kind, "cost_item_code": ev.cost_item_code, "cost_item_title": title, "amount": s(ev.amount), "currency": ev.currency,
            "fx_rate": str(ev.fx_rate) if ev.fx_rate is not None else None, "fx_on": ev.fx_on.isoformat() if ev.fx_on else None, "fx_source": ev.fx_source,
            "amount_rub": s(ev.amount_rub), "recognized_on": ev.recognized_on.isoformat(), "basis_type": ev.basis_type, "basis_title": BASIS.get(ev.basis_type, ev.basis_type),
            "document": {"type": ev.document_type, "number": ev.document_number, "date": ev.document_date.isoformat() if ev.document_date else None},
            "obligation_id": ev.obligation_id, "source_type": ev.source_type, "status": ev.status, "reversal_reason": ev.reversal_reason, "comment": ev.comment}


# ------------------------------------------------------------------ права ввода (по текущей логике план/факта, 04 — раздел 9)

def can_view_all(user) -> bool:
    """Видеть все события сделки (суммы расходов раскрывают себестоимость): право «Себестоимость»; иначе — только свои."""
    from ...core.roles import READ
    return user.can("cost", READ)


def check_create(user, deal, kind: str, code: Optional[str]) -> None:
    from ...api.tasks_files_planfact import _allowed_items
    from ...core.roles import EDIT, Role
    from ...core.workflow import POST_WIN_STATUSES
    if deal.status not in POST_WIN_STATUSES:
        raise HTTPException(409, "Признание вносится после выигрыша (стадии контракта и исполнения)")
    if deal.plan_fact_closed and not user.has(Role.FD):
        raise HTTPException(403, "План/факт закрыт — изменения вносит только ФД")
    if kind == "revenue":
        if not (user.has(Role.FD, Role.GD) and user.can("execution_payments", EDIT)):
            raise HTTPException(403, "Выручку признают ФД и ЗГД")
    elif kind == "expense":
        if code not in _allowed_items(user):
            t = next((t for _, k, t in COST_ITEMS if k == code), code or "—")
            raise HTTPException(403, f"Статью «{t}» вносит другая роль")


def check_reverse(user, deal, ev: FinanceRecognitionEvent) -> None:
    from ...core.roles import Role
    if ev.source_type == "planfact":
        raise HTTPException(409, "Событие перенесено из план/факта — исправляется удалением записи на вкладке «Платежи и план/факт»")
    if deal.plan_fact_closed and not user.has(Role.FD):
        raise HTTPException(403, "План/факт закрыт — изменения вносит только ФД")
    if ev.created_by != user.id and not user.has(Role.FD, Role.GD):
        raise HTTPException(403, "Сторнировать признание может автор, ФД или ЗГД")


def list_for(db: Session, user, deal) -> list[dict]:
    q = select(FinanceRecognitionEvent).where(FinanceRecognitionEvent.deal_id == deal.id).order_by(FinanceRecognitionEvent.recognized_on, FinanceRecognitionEvent.created_at)
    evs = db.scalars(q).all()
    if not can_view_all(user):
        evs = [e for e in evs if e.created_by == user.id]
    return [dto(e) for e in evs]
