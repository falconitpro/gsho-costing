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
    have = best_by_code(owned(db, user_id=user_id))
    names = {p.code: p.name for p in db.scalars(select(ServicePermit).where(ServicePermit.code.in_(codes))).all()}
    out = []
    for c in codes:
        p = have.get(c)
        if p is None:
            out.append({"code": c, "name": names.get(c, c), "problem": "missing", "valid_until": None})
        elif p.valid_until is not None and p.valid_until < on:
            out.append({"code": c, "name": names.get(c, c), "problem": "expired", "valid_until": p.valid_until.isoformat()})
        elif p.result == "unfit":   # D-206: медосмотр «не годен» — итог без диагноза
            out.append({"code": c, "name": names.get(c, c), "problem": "unfit", "valid_until": p.valid_until.isoformat() if p.valid_until else None})
    return out


def owned(db: Session, user_id: Optional[str] = None, employee_id: Optional[str] = None) -> list[EngineerPermit]:
    """D-206: допуски человека — по учётной записи CRM и по карточке «Персонала» (они связаны через hr_employees.user_id)."""
    from ..models import HrEmployee
    uids, eids = set(), set()
    if user_id:
        uids.add(user_id)
        eids |= set(db.scalars(select(HrEmployee.id).where(HrEmployee.user_id == user_id)).all())
    if employee_id:
        eids.add(employee_id)
        u = db.scalar(select(HrEmployee.user_id).where(HrEmployee.id == employee_id))
        if u:
            uids.add(u)
    if not uids and not eids:
        return []
    return list(db.scalars(select(EngineerPermit).where(EngineerPermit.user_id.in_(uids) | EngineerPermit.employee_id.in_(eids))).all())


def best_by_code(rows: list[EngineerPermit]) -> dict[str, EngineerPermit]:
    """Если допуск заведён и в сервисе, и в «Персонале» — берётся действующий дольше (бессрочный — лучше всех)."""
    out: dict[str, EngineerPermit] = {}
    far = date(9999, 12, 31)
    for p in rows:
        cur = out.get(p.permit_code)
        if cur is None or (p.valid_until or far, p.updated_at or 0) > (cur.valid_until or far, cur.updated_at or 0):
            out[p.permit_code] = p
    return out


def describe(gaps: list[dict]) -> str:
    return "; ".join(f"{g['code']} — " + ("нет" if g["problem"] == "missing" else "не годен по медосмотру" if g["problem"] == "unfit" else "истёк " + g["valid_until"])
                     for g in gaps)
