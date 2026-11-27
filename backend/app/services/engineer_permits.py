"""Допуски сотрудников (I-2, D-195): какие допуски нужны наряду и что из них есть у исполнителя на дату работ."""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.clock import msk_date, today as msk_today
from ..models import EngineerPermit, ServicePermit, ServiceWorkType, WorkOrder, WorkOrderWork


def required(db: Session, wo: WorkOrder) -> list[str]:
    """Допуски, которые нужны наряду: из вида работ наряда и видов работ всех его работ (без повторов, по порядку)."""
    ids = [wo.work_type_id] if wo.work_type_id else []
    ids += [w.work_type_id for w in db.scalars(select(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id)).all() if w.work_type_id]
    codes: list[str] = []
    for wt in (db.get(ServiceWorkType, i) for i in dict.fromkeys(ids)):
        for c in (wt.permit_codes if wt else None) or []:
            if c not in codes:
                codes.append(c)
    return codes


def work_date(wo: WorkOrder) -> date:
    """На какую дату проверять допуски: начало по плану (по Москве), иначе сегодня."""
    return msk_date(wo.planned_start) or msk_today()


def check(db: Session, user_id: Optional[str], codes: list[str], on: date) -> list[dict]:
    """Чего не хватает: [{code, name, problem: missing | expired, valid_until}] — пусто, если всё есть."""
    if not user_id or not codes:
        return []
    have = {p.permit_code: p for p in db.scalars(select(EngineerPermit).where(EngineerPermit.user_id == user_id)).all()}
    names = {p.code: p.name for p in db.scalars(select(ServicePermit).where(ServicePermit.code.in_(codes))).all()}
    out = []
    for c in codes:
        p = have.get(c)
        if p is None:
            out.append({"code": c, "name": names.get(c, c), "problem": "missing", "valid_until": None})
        elif p.valid_until is not None and p.valid_until < on:
            out.append({"code": c, "name": names.get(c, c), "problem": "expired", "valid_until": p.valid_until.isoformat()})
    return out


def describe(gaps: list[dict]) -> str:
    return "; ".join(f"{g['code']} — {'нет' if g['problem'] == 'missing' else 'истёк ' + g['valid_until']}" for g in gaps)
