"""Стадия контракта и исполнения (задача 2): реквизиты контракта, этапы, фактические даты и платежи, закрытие."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import DealState, DealStatus, close_execution, contract_terms_changed, role_title, start_execution
from ..db import get_db
from ..models import Approval, Deal, ExecutionStage
from ..services import execution_service as ex
from ..services.deal_service import apply_transition, audit, complete_tasks, create_version, sla
from .common import deal_payload, get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}/execution", tags=["execution"])


class ContractIn(BaseModel):
    contract_number: Optional[str] = None
    contract_signed_at: Optional[date] = None
    factory_contract_signed_at: Optional[date] = None
    factory_contract_number: Optional[str] = None
    won_variant_id: Optional[str] = None


def _stage_deal(deal: Deal) -> None:
    if deal.status not in (DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED):
        raise HTTPException(409, "Сделка не на стадии контракта или исполнения")


@router.get("/stages")
def stages(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if deal.status not in (DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED):
        return {"stages": [], "status": deal.status.value}
    if not db.scalar(select(ExecutionStage.id).where(ExecutionStage.deal_id == deal.id)):
        ex.rebuild_stages(db, deal)
        db.commit()
    rows = ex.stage_rows(db, deal)
    for r in rows:
        st = db.get(ExecutionStage, r["id"])
        r["can_set"] = st is not None and ex.can_set(user, st)
        if not user.can("cost"):
            r.pop("amount", None); r.pop("actual_amount", None)
    related = []
    if deal.request_group_id:
        for d in db.scalars(select(Deal).where(Deal.request_group_id == deal.request_group_id, Deal.id != deal.id)).all():
            related.append({"id": d.id, "number": d.number, "status": d.status.value})
    return {"stages": rows, "status": deal.status.value, "contract_number": deal.contract_number, "contract_signed_at": deal.contract_signed_at,
            "factory_contract_signed_at": deal.factory_contract_signed_at, "factory_contract_number": deal.factory_contract_number, "won_variant_id": deal.won_variant_id, "related": related,
            "contract_steps": ex.contract_steps(db, deal), "status_detail": ex.status_detail(deal)}


@router.patch("/contract")
def contract(deal_id: str, body: ContractIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Менеджер вносит номер контракта и даты подписания (с клиентом и с заводом); при альтернативах — выбранное предложение."""
    deal = get_deal_or_404(db, deal_id, user)
    _stage_deal(deal)
    if not (user.has(Role.GD) or (user.has(Role.MANAGER) and deal.manager_id == user.id) or user.has(Role.CD)):
        raise HTTPException(403, "Реквизиты контракта вносит менеджер сделки")
    before = {k: getattr(deal, k) for k in ContractIn.model_fields}
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(deal, k, v)
    if body.won_variant_id and not any(v.id == body.won_variant_id for v in deal.variants):
        raise HTTPException(422, "Предложение не найдено")
    audit(db, user.id, deal.id, "deal", deal.id, "contract", {k: str(v) for k, v in before.items()}, body.model_dump(mode="json", exclude_none=True))
    ex.rebuild_stages(db, deal)
    db.commit()
    db.refresh(deal)
    return deal_payload(deal, user)


class ContractStepIn(BaseModel):
    code: str
    done: bool = True
    on: Optional[date] = None
    comment: str = ""
    contract_number: Optional[str] = None       # для «Контракт подписан клиентом»
    factory_contract_number: Optional[str] = None   # для «Контракт с заводом подписан»


@router.post("/contract-step")
def contract_step(deal_id: str, body: ContractStepIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подэтап заключения контракта выполнен (или отметка снята): задача следующему ответственному, запись в ленту и журнал."""
    from datetime import timedelta
    from ..core.roles import Role as _R
    from ..core.workflow import add_business_days
    from ..models import FeedEntry, Task
    deal = get_deal_or_404(db, deal_id, user)
    if deal.status != DealStatus.CONTRACT:
        raise HTTPException(409, "Подэтапы отмечаются на стадии «Заключение контракта»")
    step = next((c for c in ex.CONTRACT_STEPS if c["code"] == body.code), None)
    if step is None:
        raise HTTPException(404, "Неизвестный подэтап")
    is_owner = user.has(_R.MANAGER, _R.CD) and deal.manager_id == user.id
    allowed = user.has(_R.GD) or any(user.has(_R(r)) for r in step["roles"] if r != "manager") or ("manager" in step["roles"] and is_owner)
    if not allowed:
        raise HTTPException(403, f"Подэтап «{step['title']}» отмечают: {', '.join(role_title(r) for r in step['roles'])}")
    on = body.on or date.today()
    steps = dict(deal.contract_steps or {})
    if body.done:
        if body.code == "client_signed":
            number = (body.contract_number or deal.contract_number or "").strip()
            if not number:
                raise HTTPException(422, "Укажите номер контракта")
            deal.contract_number, deal.contract_signed_at = number, on
        if body.code == "factory_signed":
            fnum = (body.factory_contract_number or deal.factory_contract_number or "").strip()
            if not fnum:
                raise HTTPException(422, "Укажите номер контракта с заводом")
            deal.factory_contract_number, deal.factory_contract_signed_at = fnum, on
        steps[body.code] = {"done": True, "on": on.isoformat(), "by": user.id, "comment": body.comment}
    else:
        steps.pop(body.code, None)
        if body.code == "client_signed":
            deal.contract_signed_at = None
        if body.code == "factory_signed":
            deal.factory_contract_signed_at = None
    deal.contract_steps = steps
    audit(db, user.id, deal.id, "deal", deal.id, "contract_step", None, {"step": step["title"], "done": body.done, "on": on.isoformat(), "comment": body.comment})
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Заключение контракта: {step['title']}" + ("" if body.done else " — отметка снята"),
                     body=body.comment, user_id=user.id))
    # задачи: закрываем задачи стадии контракта, ставим задачу следующему ответственному
    complete_tasks(db, deal, block="contract")
    rows = ex.contract_steps(db, deal)
    nxt = next((r for r in rows if r["state"] == "current"), None)
    if nxt:
        days = 5 if nxt["code"] in ("client_review", "factory_signed") else 2
        assignee = deal.manager_id if nxt["task_role"] == "manager" else None
        db.add(Task(deal_id=deal.id, role=nxt["task_role"], assignee_id=assignee, title=f"Заключение контракта: {nxt['title'].lower()}", block="contract",
                    due=add_business_days(date.today(), days)))
    elif deal.contract_number and deal.contract_signed_at:
        db.add(Task(deal_id=deal.id, role="manager", assignee_id=deal.manager_id, title="Контракт подписан — начать исполнение", block="contract",
                    due=date.today() + timedelta(days=1)))
    ex.rebuild_stages(db, deal)
    db.commit()
    return {"contract_steps": ex.contract_steps(db, deal), "status_detail": ex.status_detail(deal)}


@router.post("/start")
def start(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Контракт подписан → исполнение: этапы и задачи ответственным (в т. ч. заказ и установка опций РФ)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not (user.has(Role.GD, Role.CD) or (user.has(Role.MANAGER) and deal.manager_id == user.id)):
        raise HTTPException(403)
    if deal.has_alternatives and not deal.won_variant_id:
        raise HTTPException(422, "Укажите, какое предложение выбрал клиент")
    try:
        tr = start_execution(DealState(status=deal.status, service_flag=deal.service_flag), bool(deal.contract_number and deal.contract_signed_at), date.today())
    except Exception as e:
        raise HTTPException(409, str(e))
    deal.in_execution, deal.execution_started = True, True
    complete_tasks(db, deal, block="contract")
    apply_transition(db, deal, tr, user.id)
    ex.rebuild_stages(db, deal)
    n = ex.create_stage_tasks(db, deal, user.id)
    db.commit()
    return {"status": deal.status.value, "tasks_created": n}


class ActualIn(BaseModel):
    actual_on: Optional[date] = None
    actual_amount: Optional[float] = None
    note: str = ""


@router.post("/stages/{stage_id}/actual")
def set_actual(deal_id: str, stage_id: str, body: ActualIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Фактическая дата этапа (по ролям) или фактический платёж (ФД): пересчёт плановых дат и платежей от факта."""
    deal = get_deal_or_404(db, deal_id, user)
    _stage_deal(deal)
    st = db.get(ExecutionStage, stage_id)
    if st is None or st.deal_id != deal.id:
        raise HTTPException(404)
    if not ex.can_set(user, st):
        raise HTTPException(403, f"Этап «{st.title}» отмечают: {', '.join(role_title(r) for r in st.responsible_roles)}")
    if st.code == "factory_final" and body.actual_on and st.position_id:
        from .acceptance import position_accepted
        if not position_accepted(db, st.position_id):
            raise HTTPException(409, "Окончательный расчёт с заводом — после приёмки всех машин позиции на заводе (вкладка «Исполнение» → «Приёмка на заводе»)")
    if st.code == "factory_acceptance":
        raise HTTPException(409, "Приёмка на заводе отмечается сама, когда приняты все машины позиции")
    if ex.STAGE_DEFS[st.code]["kind"] in ("payment_in", "payment_out") and body.actual_on and body.actual_amount is None:
        raise HTTPException(422, "Укажите фактическую сумму платежа")
    ex.set_actual(db, deal, st, body.actual_on, body.actual_amount, body.note, user)
    if deal.status == DealStatus.EXECUTION:
        ex.create_stage_tasks(db, deal, user.id)
    db.commit()
    return {"ok": True, "stages": ex.stage_rows(db, deal)}


class RefineIn(BaseModel):
    refined_on: Optional[date] = None      # пусто — снять уточнение
    note: str = ""


@router.post("/stages/{stage_id}/refine")
def refine(deal_id: str, stage_id: str, body: RefineIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Уточнённая внутренняя дата этапа (без факта): прогноз следующих этапов пересчитывается, базовый план не меняется."""
    deal = get_deal_or_404(db, deal_id, user)
    _stage_deal(deal)
    st = db.get(ExecutionStage, stage_id)
    if st is None or st.deal_id != deal.id:
        raise HTTPException(404)
    if not (ex.can_set(user, st) or user.has(Role.CD)):
        raise HTTPException(403, f"Дату этапа «{st.title}» уточняют: {', '.join(role_title(r) for r in st.responsible_roles)}")
    if st.actual_on:
        raise HTTPException(409, "Этап уже выполнен — уточнять нечего")
    before = st.refined_on
    st.refined_on, st.refined_note = body.refined_on, body.note
    audit(db, user.id, deal.id, "execution_stage", st.id, "refine", {"refined_on": str(before) if before else None},
          {"refined_on": str(body.refined_on) if body.refined_on else None, "note": body.note})
    db.flush()
    ex.rebuild_stages(db, deal)
    db.commit()
    return {"ok": True, "stages": ex.stage_rows(db, deal)}


class TermsChangedIn(BaseModel):
    reason: str


@router.post("/terms-changed")
def terms_changed(deal_id: str, body: TermsChangedIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Условия контракта отличаются от утверждённого КП → новая версия расчёта, сброс виз, повторная визация."""
    deal = get_deal_or_404(db, deal_id, user)
    if not (user.has(Role.GD, Role.CD) or (user.has(Role.MANAGER) and deal.manager_id == user.id)):
        raise HTTPException(403)
    try:
        tr = contract_terms_changed(DealState(status=deal.status, service_flag=deal.service_flag, in_execution=True), body.reason, date.today(), sla(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all():
        a.reset_at = datetime.now(timezone.utc)
    deal.contract_terms_changed, deal.in_execution = True, True
    create_version(db, deal, "contract_change", user.id)
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value, "version_no": deal.current_version_no}


class CloseIn(BaseModel):
    reason: str = ""


@router.post("/close")
def close(deal_id: str, body: CloseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Закрытие сделки после исполнения — только КД (и ЗГД)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("execution_close", 2):
        raise HTTPException(403, "Сделку закрывает коммерческий директор")
    try:
        tr = close_execution(DealState(status=deal.status, service_flag=deal.service_flag), body.reason)
    except Exception as e:
        raise HTTPException(409, str(e))
    deal.execution_closed_at = date.today()
    closing = db.scalar(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id, ExecutionStage.code == "closing"))
    if closing:
        closing.actual_on, closing.done_by = date.today(), user.id
    complete_tasks(db, deal)
    apply_transition(db, deal, tr, user.id)
    try:   # машины сделки — в парк клиента (запчасти, раздел 9)
        from .parts_fleet import sync_from_deals
        sync_from_deals(db, deal.id, user.id)
    except Exception:   # pragma: no cover — закрытие сделки не зависит от парка
        pass
    db.commit()
    return {"status": deal.status.value}
