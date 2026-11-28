"""Блок «Персонал», L1 (D-205): область доступа по работодателю и назначение сотрудника на дату из кадровых событий.

Кадровые события только добавляются. Назначение (подразделение, должность, штатная единица, ставка) на дату d —
результат применения по порядку (effective_from, recorded_at) всех неотменённых событий hire / transfer / rate,
которые действуют на d. Временный перевод (effective_to) после своей даты перестаёт действовать сам — прежнее
назначение возвращается без отдельной записи.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import HR_ALL_EMPLOYERS, Role
from ..models import HrAccessScope, HrEmployee, HrEmployeeEvent, HrEmployment

KINDS = ("hire", "transfer", "rate", "terminate", "cancel")
ASSIGN_KINDS = ("hire", "transfer", "rate")
ASSIGN_FIELDS = ("department_id", "position_id", "staff_slot_id", "rate")
EMPLOYMENT_KINDS = {"main": "основное место", "internal_part": "внутреннее совместительство", "external_part": "внешнее совместительство"}


def allowed_employers(db: Session, user: CurrentUser) -> Optional[set[str]]:
    """None — все работодатели; иначе — множество id из области пользователя (пусто — никого).
    ЗГД — всегда все. ФД и бухгалтер по зарплате — все, пока ЗГД не задаст им список юрлиц (решение владельца 28.11.2026)."""
    if Role.GD in user.roles:
        return None
    rows = set(db.scalars(select(HrAccessScope.employer_id).where(HrAccessScope.user_id == user.id)).all())
    if not rows and HR_ALL_EMPLOYERS & set(user.roles):
        return None
    return rows


def in_scope(scope: Optional[set[str]], employer_id: str) -> bool:
    return scope is None or employer_id in scope


def cancelled_ids(events: Iterable[HrEmployeeEvent]) -> set[str]:
    return {e.cancels_id for e in events if e.kind == "cancel" and e.cancels_id}


def _order(e: HrEmployeeEvent):
    return (e.effective_from, e.recorded_at.isoformat() if e.recorded_at else "", e.id)


def assignment_on(events: list[HrEmployeeEvent], d: date) -> dict:
    """Назначение по трудовым отношениям на дату d (по событиям одного employment)."""
    dead = cancelled_ids(events)
    out: dict = {}
    for e in sorted((e for e in events if e.kind in ASSIGN_KINDS and e.id not in dead), key=_order):
        if e.effective_from > d or (e.effective_to and d > e.effective_to):
            continue
        for k in ASSIGN_FIELDS:
            if k in (e.data or {}):
                out[k] = e.data[k]
    return out


def status_on(emp: HrEmployment, d: date) -> str:
    if d < emp.start_date:
        return "planned"
    if emp.end_date and d > emp.end_date:
        return "terminated"
    return "active"


def events_of(db: Session, employment_ids: Iterable[str]) -> dict[str, list[HrEmployeeEvent]]:
    ids = list(employment_ids)
    out: dict[str, list[HrEmployeeEvent]] = {i: [] for i in ids}
    if not ids:
        return out
    for e in db.scalars(select(HrEmployeeEvent).where(HrEmployeeEvent.employment_id.in_(ids))):
        out[e.employment_id].append(e)
    return out


def overlaps(db: Session, employee_id: str, employer_id: str, start: date, kind: str, skip_id: str = "") -> bool:
    """Основное место у одного работодателя — одно в каждый момент: новое не может начаться, пока прежнее не закончилось."""
    if kind != "main":
        return False
    q = select(HrEmployment).where(HrEmployment.employee_id == employee_id, HrEmployment.employer_id == employer_id, HrEmployment.kind == "main")
    for e in db.scalars(q):
        if e.id != skip_id and (e.end_date is None or e.end_date >= start):
            return True
    return False


def next_personnel_no(db: Session) -> str:
    """D-210: табельный номер в CRM из счётчика (атомарно); первый раз продолжает уже выданные номера."""
    from .numbering import next_seq
    nums = [int(x[2:]) for x in db.scalars(select(HrEmployee.personnel_no).where(HrEmployee.personnel_no.like("П-%"))) if x[2:].isdigit()]
    while True:
        no = f"П-{next_seq(db, 'hr_personnel', 0, max(nums or [0]) + 1):04d}"
        if not db.scalar(select(HrEmployee.id).where(HrEmployee.personnel_no == no)):
            return no


def full_name(e: HrEmployee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


def block_if_gone(db: Session, employee: HrEmployee, on: date, by_id: Optional[str]) -> Optional[dict]:
    """D-207: уволен отовсюду (последний рабочий день прошёл) и есть вход в CRM — доступ блокируется сразу.
    by_id — кто внёс увольнение (автор отключения в журнале)."""
    from .offboarding import offboard_terminated
    if not employee.user_id:
        return None
    every = db.scalars(select(HrEmployment).where(HrEmployment.employee_id == employee.id)).all()
    if not every or any(m.end_date is None or m.end_date >= on for m in every):
        return None
    last = max(m.end_date for m in every)
    return offboard_terminated(db, employee.user_id, by_id, full_name(employee), last)


def terminated_by(db: Session, employee_id: str) -> Optional[str]:
    """Кто внёс действующее (неотменённое) увольнение — последнее по дате."""
    evs = list(db.scalars(select(HrEmployeeEvent).where(HrEmployeeEvent.employee_id == employee_id)))
    dead = cancelled_ids(evs)
    t = [e for e in evs if e.kind == "terminate" and e.id not in dead]
    return max(t, key=lambda e: (e.effective_from, e.recorded_at)).recorded_by if t else None
