"""Блок «Персонал», L1 (D-205): работодатели, подразделения, должности, штатное расписание, сотрудники,
трудовые отношения и кадровые события. Только кадровое ядро: паспорт, адрес, зарплата — не здесь.

Изоляция: каждая запись несёт employer_id, все чтения и записи фильтруются областью пользователя (hr_core.allowed_employers).
Чужой работодатель неотличим от несуществующего (404). В журнал пишутся только названия полей, без значений.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.clock import today
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import (HrAccessScope, HrDepartment, HrEmployee, HrEmployeeEvent, HrEmployment, HrPosition, HrStaffSlot, LegalEntity, User)
from ..services import hr_core as hc
from ..services.deal_service import audit

router = APIRouter(prefix="/api/hr", tags=["hr"])
NOT_FOUND = "Не найдено"


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("hr_core", READ):
        raise HTTPException(403, "Нет доступа к разделу «Персонал»")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("hr_core", EDIT):
        raise HTTPException(403, "Кадровые изменения вносят сотрудники с правом редактирования «Персонала»")
    return user


def _admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("access_admin", EDIT):
        raise HTTPException(403, "Работодателей и области доступа настраивает ЗГД")
    return user


def _log(db: Session, user: CurrentUser, entity: str, entity_id: str, action: str, fields: list[str] | None = None, comment: str = "") -> None:
    audit(db, user.id, None, entity, entity_id, action, after={"fields": sorted(fields or [])} if fields else None, comment=comment)


def _scoped(db: Session, user: CurrentUser, model, obj_id: str):
    obj = db.get(model, obj_id)
    if not obj or not hc.in_scope(hc.allowed_employers(db, user), obj.employer_id):
        raise HTTPException(404, NOT_FOUND)
    return obj


def _employer(db: Session, user: CurrentUser, employer_id: str) -> LegalEntity:
    le = db.get(LegalEntity, employer_id)
    if not le or not le.is_employer or not hc.in_scope(hc.allowed_employers(db, user), le.id):
        raise HTTPException(404, "Работодатель не найден")
    return le


def _valid_dates(f: date, t: Optional[date]) -> None:
    if t and t < f:
        raise HTTPException(422, "Дата окончания раньше даты начала")


# ───────────── работодатели и области доступа

class EmployerIn(BaseModel):
    is_employer: Optional[bool] = None
    hr_timezone: Optional[str] = Field(None, max_length=64)
    hr_external_id: Optional[str] = Field(None, max_length=64)


def _employer_out(le: LegalEntity) -> dict:
    return {"id": le.id, "code": le.code, "name": le.name, "country": le.country, "is_employer": le.is_employer,
            "hr_timezone": le.hr_timezone, "hr_external_id": le.hr_external_id, "is_active": le.is_active}


@router.get("/employers")
def employers(all: bool = False, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    """Работодатели из области пользователя. all=1 (только ЗГД / настройка доступа) — все компании группы, чтобы отметить работодателей."""
    rows = db.scalars(select(LegalEntity).order_by(LegalEntity.code)).all()
    if all and user.can("access_admin", EDIT):
        return [_employer_out(le) for le in rows]
    scope = hc.allowed_employers(db, user)
    return [_employer_out(le) for le in rows if le.is_employer and hc.in_scope(scope, le.id)]


@router.patch("/employers/{employer_id}")
def employer_update(employer_id: str, body: EmployerIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    le = db.get(LegalEntity, employer_id)
    if not le:
        raise HTTPException(404, "Компания не найдена")
    data = body.model_dump(exclude_unset=True)
    if "is_employer" in data and not user.can("access_admin", EDIT):
        raise HTTPException(403, "Отметить компанию работодателем может ЗГД")
    if not ("is_employer" in data and user.can("access_admin", EDIT)):
        _employer(db, user, employer_id)
    if data.get("hr_timezone"):
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(data["hr_timezone"])
        except Exception:
            raise HTTPException(422, "Неизвестный часовой пояс (пример: Europe/Moscow, Asia/Bishkek)")
    for k, v in data.items():
        if v is not None:
            setattr(le, k, v.strip() if isinstance(v, str) else v)
    _log(db, user, "hr_employer", le.id, "update", list(data))
    db.commit()
    return _employer_out(le)


@router.get("/scopes")
def scopes(user: CurrentUser = Depends(_admin), db: Session = Depends(get_db)):
    out: dict[str, list[str]] = {}
    for s in db.scalars(select(HrAccessScope)):
        out.setdefault(s.user_id, []).append(s.employer_id)
    return out


class ScopeIn(BaseModel):
    employer_ids: list[str]


@router.put("/scopes/{user_id}")
def scope_set(user_id: str, body: ScopeIn, user: CurrentUser = Depends(_admin), db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(404, "Сотрудник не найден")
    want = set(body.employer_ids)
    for eid in want:
        le = db.get(LegalEntity, eid)
        if not le or not le.is_employer:
            raise HTTPException(422, "В области доступа — только работодатели")
    have = {s.employer_id: s for s in db.scalars(select(HrAccessScope).where(HrAccessScope.user_id == user_id))}
    for eid, s in have.items():
        if eid not in want:
            db.delete(s)
    for eid in want - set(have):
        db.add(HrAccessScope(user_id=user_id, employer_id=eid, granted_by=user.id))
    audit(db, user.id, None, "hr_access_scope", user_id, "set", after={"employer_ids": sorted(want)})
    db.commit()
    return sorted(want)


@router.get("/users")
def users_for_link(user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    """Учётные записи CRM для связи с сотрудником (0..1): только имя и почта, без ролей."""
    return [{"id": u.id, "display_name": u.display_name, "email": u.email}
            for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name))]


# ───────────── подразделения

class DepartmentIn(BaseModel):
    employer_id: Optional[str] = None
    parent_id: Optional[str] = None
    code: str = Field("", max_length=32)
    name: str = Field(..., min_length=1, max_length=255)
    head_employee_id: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


def _dept_out(d: HrDepartment) -> dict:
    return {"id": d.id, "employer_id": d.employer_id, "parent_id": d.parent_id, "code": d.code, "name": d.name,
            "head_employee_id": d.head_employee_id, "valid_from": d.valid_from, "valid_to": d.valid_to}


def _check_parent(db: Session, d: HrDepartment) -> None:
    seen = {d.id}
    p = d.parent_id
    while p:
        if p in seen:
            raise HTTPException(422, "Подразделение не может входить само в себя")
        seen.add(p)
        par = db.get(HrDepartment, p)
        if not par or par.employer_id != d.employer_id:
            raise HTTPException(422, "Вышестоящее подразделение — другого работодателя или не найдено")
        p = par.parent_id


def _check_head(db: Session, employer_id: str, employee_id: Optional[str]) -> None:
    if not employee_id:
        return
    ok = db.scalar(select(HrEmployment.id).where(HrEmployment.employee_id == employee_id, HrEmployment.employer_id == employer_id))
    if not ok:
        raise HTTPException(422, "Руководитель должен работать у этого работодателя")


@router.get("/departments")
def departments(employer_id: Optional[str] = None, on: Optional[date] = None, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    scope = hc.allowed_employers(db, user)
    q = select(HrDepartment).order_by(HrDepartment.name)
    if employer_id:
        q = q.where(HrDepartment.employer_id == employer_id)
    rows = [d for d in db.scalars(q) if hc.in_scope(scope, d.employer_id)]
    if on:
        rows = [d for d in rows if d.valid_from <= on and (d.valid_to is None or d.valid_to >= on)]
    return [_dept_out(d) for d in rows]


@router.post("/departments")
def department_create(body: DepartmentIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    if not body.employer_id:
        raise HTTPException(422, "Укажите работодателя")
    _employer(db, user, body.employer_id)
    vf = body.valid_from or today()
    _valid_dates(vf, body.valid_to)
    d = HrDepartment(employer_id=body.employer_id, parent_id=body.parent_id or None, code=body.code.strip(), name=body.name.strip(),
                     head_employee_id=body.head_employee_id or None, valid_from=vf, valid_to=body.valid_to)
    db.add(d)
    db.flush()
    _check_parent(db, d)
    _check_head(db, d.employer_id, d.head_employee_id)
    _log(db, user, "hr_department", d.id, "create")
    db.commit()
    return _dept_out(d)


@router.patch("/departments/{dept_id}")
def department_update(dept_id: str, body: DepartmentIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    d = _scoped(db, user, HrDepartment, dept_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("employer_id") and data["employer_id"] != d.employer_id:
        raise HTTPException(422, "Работодателя подразделения не меняют — создайте подразделение у другого работодателя")
    data.pop("employer_id", None)
    for k, v in data.items():
        if k == "name" and not v:
            continue
        setattr(d, k, (v.strip() if isinstance(v, str) else v) if v != "" or k == "code" else None)
    if not d.valid_from:
        raise HTTPException(422, "Укажите дату начала")
    _valid_dates(d.valid_from, d.valid_to)
    _check_parent(db, d)
    _check_head(db, d.employer_id, d.head_employee_id)
    _log(db, user, "hr_department", d.id, "update", list(data))
    db.commit()
    return _dept_out(d)


# ───────────── должности

class PositionIn(BaseModel):
    employer_id: Optional[str] = None
    code: str = Field("", max_length=32)
    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field("worker", pattern="^(manager|specialist|worker|other)$")
    is_active: bool = True


def _pos_out(p: HrPosition) -> dict:
    return {"id": p.id, "employer_id": p.employer_id, "code": p.code, "name": p.name, "category": p.category, "is_active": p.is_active}


@router.get("/positions")
def positions(employer_id: Optional[str] = None, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    scope = hc.allowed_employers(db, user)
    q = select(HrPosition).order_by(HrPosition.name)
    if employer_id:
        q = q.where(HrPosition.employer_id == employer_id)
    return [_pos_out(p) for p in db.scalars(q) if hc.in_scope(scope, p.employer_id)]


@router.post("/positions")
def position_create(body: PositionIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    if not body.employer_id:
        raise HTTPException(422, "Укажите работодателя")
    _employer(db, user, body.employer_id)
    name = body.name.strip()
    if db.scalar(select(HrPosition.id).where(HrPosition.employer_id == body.employer_id, HrPosition.name == name)):
        raise HTTPException(409, "Такая должность у работодателя уже есть")
    p = HrPosition(employer_id=body.employer_id, code=body.code.strip(), name=name, category=body.category, is_active=body.is_active)
    db.add(p)
    db.flush()
    _log(db, user, "hr_position", p.id, "create")
    db.commit()
    return _pos_out(p)


@router.patch("/positions/{pos_id}")
def position_update(pos_id: str, body: PositionIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    p = _scoped(db, user, HrPosition, pos_id)
    data = body.model_dump(exclude_unset=True)
    data.pop("employer_id", None)
    if "name" in data:
        name = data["name"].strip()
        dup = db.scalar(select(HrPosition.id).where(HrPosition.employer_id == p.employer_id, HrPosition.name == name, HrPosition.id != p.id))
        if dup:
            raise HTTPException(409, "Такая должность у работодателя уже есть")
        data["name"] = name
    for k, v in data.items():
        setattr(p, k, v)
    _log(db, user, "hr_position", p.id, "update", list(data))
    db.commit()
    return _pos_out(p)


# ───────────── штатное расписание

class SlotIn(BaseModel):
    employer_id: Optional[str] = None
    department_id: str
    position_id: str
    fte: float = Field(1, gt=0, le=999)
    work_location: str = Field("", max_length=255)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    comment: str = ""


class SlotChangeIn(BaseModel):
    """Новая версия штатной единицы с даты: прежняя закрывается днём раньше."""
    valid_from: date
    fte: Optional[float] = Field(None, gt=0, le=999)
    work_location: Optional[str] = Field(None, max_length=255)
    comment: str = ""


class SlotCloseIn(BaseModel):
    valid_to: date
    comment: str = ""


def _occupancy(db: Session, scope, on: date) -> dict[str, float]:
    """Занятые ставки по штатным единицам на дату — из назначений действующих трудовых отношений."""
    q = select(HrEmployment)
    emps = [e for e in db.scalars(q) if hc.in_scope(scope, e.employer_id) and hc.status_on(e, on) == "active"]
    evs = hc.events_of(db, [e.id for e in emps])
    out: dict[str, float] = {}
    for e in emps:
        a = hc.assignment_on(evs[e.id], on)
        if a.get("staff_slot_id"):
            out[a["staff_slot_id"]] = out.get(a["staff_slot_id"], 0) + float(a.get("rate") or 1)
    return out


def _slot_out(s: HrStaffSlot, busy: dict[str, float]) -> dict:
    return {"id": s.id, "employer_id": s.employer_id, "department_id": s.department_id, "position_id": s.position_id, "fte": float(s.fte),
            "work_location": s.work_location, "valid_from": s.valid_from, "valid_to": s.valid_to, "supersedes_id": s.supersedes_id,
            "comment": s.comment, "occupied": round(busy.get(s.id, 0), 2)}


def _check_slot_refs(db: Session, employer_id: str, department_id: str, position_id: str) -> None:
    d, p = db.get(HrDepartment, department_id), db.get(HrPosition, position_id)
    if not d or d.employer_id != employer_id:
        raise HTTPException(422, "Подразделение — другого работодателя или не найдено")
    if not p or p.employer_id != employer_id:
        raise HTTPException(422, "Должность — другого работодателя или не найдена")


@router.get("/staff-slots")
def staff_slots(employer_id: Optional[str] = None, on: Optional[date] = None, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    scope = hc.allowed_employers(db, user)
    d = on or today()
    q = select(HrStaffSlot).order_by(HrStaffSlot.valid_from)
    if employer_id:
        q = q.where(HrStaffSlot.employer_id == employer_id)
    rows = [s for s in db.scalars(q) if hc.in_scope(scope, s.employer_id) and s.valid_from <= d and (s.valid_to is None or s.valid_to >= d)]
    busy = _occupancy(db, scope, d)
    return [_slot_out(s, busy) for s in rows]


@router.post("/staff-slots")
def staff_slot_create(body: SlotIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    if not body.employer_id:
        raise HTTPException(422, "Укажите работодателя")
    _employer(db, user, body.employer_id)
    _check_slot_refs(db, body.employer_id, body.department_id, body.position_id)
    vf = body.valid_from or today()
    _valid_dates(vf, body.valid_to)
    s = HrStaffSlot(employer_id=body.employer_id, department_id=body.department_id, position_id=body.position_id, fte=body.fte,
                    work_location=body.work_location.strip(), valid_from=vf, valid_to=body.valid_to, comment=body.comment)
    db.add(s)
    db.flush()
    _log(db, user, "hr_staff_slot", s.id, "create")
    db.commit()
    return _slot_out(s, {})


@router.post("/staff-slots/{slot_id}/change")
def staff_slot_change(slot_id: str, body: SlotChangeIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    from datetime import timedelta
    s = _scoped(db, user, HrStaffSlot, slot_id)
    if s.valid_to and s.valid_to < body.valid_from:
        raise HTTPException(409, "Штатная единица уже закрыта раньше этой даты")
    if body.valid_from <= s.valid_from:
        raise HTTPException(422, "Новая версия начинается позже текущей")
    n = HrStaffSlot(employer_id=s.employer_id, department_id=s.department_id, position_id=s.position_id,
                    fte=body.fte if body.fte is not None else s.fte, work_location=(body.work_location if body.work_location is not None else s.work_location).strip(),
                    valid_from=body.valid_from, valid_to=s.valid_to, supersedes_id=s.id, comment=body.comment)
    s.valid_to = body.valid_from - timedelta(days=1)
    db.add(n)
    db.flush()
    _log(db, user, "hr_staff_slot", n.id, "version", [k for k in ("fte", "work_location") if getattr(body, k) is not None])
    db.commit()
    return _slot_out(n, {})


@router.post("/staff-slots/{slot_id}/close")
def staff_slot_close(slot_id: str, body: SlotCloseIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    s = _scoped(db, user, HrStaffSlot, slot_id)
    _valid_dates(s.valid_from, body.valid_to)
    s.valid_to = body.valid_to
    if body.comment:
        s.comment = body.comment
    _log(db, user, "hr_staff_slot", s.id, "close")
    db.commit()
    return _slot_out(s, {})


# ───────────── сотрудники, трудовые отношения, кадровые события

class Assign(BaseModel):
    department_id: Optional[str] = None
    position_id: Optional[str] = None
    staff_slot_id: Optional[str] = None
    rate: Optional[float] = Field(None, gt=0, le=2)


class HireIn(Assign):
    employer_id: str
    kind: str = Field("main", pattern="^(main|internal_part|external_part)$")
    start_date: date
    external_id: str = Field("", max_length=64)
    comment: str = ""


class EmployeeIn(BaseModel):
    last_name: str = Field(..., min_length=1, max_length=128)
    first_name: str = Field(..., min_length=1, max_length=128)
    middle_name: str = Field("", max_length=128)
    user_id: Optional[str] = None
    hire: HireIn


class EmployeePatch(BaseModel):
    last_name: Optional[str] = Field(None, min_length=1, max_length=128)
    first_name: Optional[str] = Field(None, min_length=1, max_length=128)
    middle_name: Optional[str] = Field(None, max_length=128)
    user_id: Optional[str] = None


class EventIn(Assign):
    kind: str = Field(..., pattern="^(transfer|rate|terminate|cancel)$")
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    cancels_id: Optional[str] = None
    reason: str = Field("", max_length=255)
    comment: str = ""


def _assign_data(db: Session, employer_id: str, a: Assign, need: bool) -> dict:
    data: dict = {}
    if a.staff_slot_id:
        s = db.get(HrStaffSlot, a.staff_slot_id)
        if not s or s.employer_id != employer_id:
            raise HTTPException(422, "Штатная единица — другого работодателя или не найдена")
        data.update(staff_slot_id=s.id, department_id=s.department_id, position_id=s.position_id)
        if a.department_id and a.department_id != s.department_id or a.position_id and a.position_id != s.position_id:
            raise HTTPException(422, "Подразделение и должность берутся из штатной единицы")
    else:
        if a.department_id:
            d = db.get(HrDepartment, a.department_id)
            if not d or d.employer_id != employer_id:
                raise HTTPException(422, "Подразделение — другого работодателя или не найдено")
            data["department_id"] = d.id
        if a.position_id:
            p = db.get(HrPosition, a.position_id)
            if not p or p.employer_id != employer_id:
                raise HTTPException(422, "Должность — другого работодателя или не найдена")
            data["position_id"] = p.id
        if data:
            data["staff_slot_id"] = None
    if a.rate is not None:
        data["rate"] = a.rate
    if need and not (data.get("department_id") and data.get("position_id")):
        raise HTTPException(422, "При приёме укажите подразделение и должность (или штатную единицу)")
    if need:
        data.setdefault("rate", 1.0)
    return data


def _check_user_link(db: Session, user_id: Optional[str], employee_id: str = "") -> None:
    if not user_id:
        return
    if not db.get(User, user_id):
        raise HTTPException(422, "Учётная запись не найдена")
    other = db.scalar(select(HrEmployee.id).where(HrEmployee.user_id == user_id, HrEmployee.id != employee_id))
    if other:
        raise HTTPException(409, "Эта учётная запись уже связана с другим сотрудником")


def _employment_out(db: Session, emp: HrEmployment, evs: list[HrEmployeeEvent], on: date, names: dict) -> dict:
    a = hc.assignment_on(evs, on)
    dead = hc.cancelled_ids(evs)
    return {"id": emp.id, "employer_id": emp.employer_id, "employer": names["employer"].get(emp.employer_id, ""), "kind": emp.kind,
            "start_date": emp.start_date, "end_date": emp.end_date, "external_id": emp.external_id, "status": hc.status_on(emp, on),
            "assignment": {**a, "department": names["dept"].get(a.get("department_id"), ""), "position": names["pos"].get(a.get("position_id"), "")},
            "events": [{"id": e.id, "kind": e.kind, "effective_from": e.effective_from, "effective_to": e.effective_to,
                        "data": {**(e.data or {}), "department": names["dept"].get((e.data or {}).get("department_id"), ""),
                                 "position": names["pos"].get((e.data or {}).get("position_id"), "")},
                        "cancels_id": e.cancels_id, "cancelled": e.id in dead, "comment": e.comment,
                        "recorded_by": names["user"].get(e.recorded_by, ""), "recorded_at": e.recorded_at}
                       for e in sorted(evs, key=lambda e: (e.effective_from, e.recorded_at), reverse=True)]}


def _names(db: Session) -> dict:
    return {"employer": {le.id: le.name for le in db.scalars(select(LegalEntity))},
            "dept": {d.id: d.name for d in db.scalars(select(HrDepartment))},
            "pos": {p.id: p.name for p in db.scalars(select(HrPosition))},
            "user": {u.id: u.display_name for u in db.scalars(select(User))}}


def _visible_employee(db: Session, user: CurrentUser, employee_id: str) -> tuple[HrEmployee, list[HrEmployment]]:
    e = db.get(HrEmployee, employee_id)
    if not e:
        raise HTTPException(404, NOT_FOUND)
    scope = hc.allowed_employers(db, user)
    emps = [m for m in db.scalars(select(HrEmployment).where(HrEmployment.employee_id == e.id).order_by(HrEmployment.start_date))
            if hc.in_scope(scope, m.employer_id)]
    if not emps:
        raise HTTPException(404, NOT_FOUND)
    return e, emps


def _employee_out(db: Session, e: HrEmployee, emps: list[HrEmployment], on: date, names: Optional[dict] = None, full: bool = True) -> dict:
    names = names or _names(db)
    evs = hc.events_of(db, [m.id for m in emps])
    items = [_employment_out(db, m, evs[m.id], on, names) for m in emps]
    if not full:
        for it in items:
            it.pop("events")
    cur = next((it for it in items if it["status"] == "active" and it["kind"] == "main"), None) or next((it for it in items if it["status"] == "active"), None)
    login = db.get(User, e.user_id) if e.user_id else None
    on_today = today()
    every = db.scalars(select(HrEmployment).where(HrEmployment.employee_id == e.id)).all()   # все трудовые отношения, не только видимые
    gone = bool(every) and all(m.end_date is not None and m.end_date < on_today for m in every)
    return {"user_active": bool(login and login.is_active),
            # D-207: уволен отовсюду, а вход в CRM ещё действует
            "offboard_needed": bool(login and login.is_active and gone),
            "id": e.id, "personnel_no": e.personnel_no, "last_name": e.last_name, "first_name": e.first_name, "middle_name": e.middle_name,
            "full_name": hc.full_name(e), "user_id": e.user_id, "user": names["user"].get(e.user_id, "") if e.user_id else "",
            "status": cur["status"] if cur else ("planned" if any(it["status"] == "planned" for it in items) else "terminated"),
            "current": cur, "employments": items}


@router.get("/employees")
def employees(q: str = "", employer_id: Optional[str] = None, department_id: Optional[str] = None, status: str = "active",
              on: Optional[date] = None, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    """Реестр: сотрудники, у которых есть трудовые отношения у работодателей из области пользователя."""
    d = on or today()
    scope = hc.allowed_employers(db, user)
    emps = [m for m in db.scalars(select(HrEmployment)) if hc.in_scope(scope, m.employer_id) and (not employer_id or m.employer_id == employer_id)]
    by_emp: dict[str, list[HrEmployment]] = {}
    for m in emps:
        by_emp.setdefault(m.employee_id, []).append(m)
    if not by_emp:
        return []
    names = _names(db)
    needle = q.strip().lower()
    out = []
    for e in db.scalars(select(HrEmployee).where(HrEmployee.id.in_(list(by_emp))).order_by(HrEmployee.last_name, HrEmployee.first_name)):
        if needle and needle not in f"{hc.full_name(e)} {e.personnel_no}".lower():
            continue
        row = _employee_out(db, e, sorted(by_emp[e.id], key=lambda m: m.start_date), d, names, full=False)
        if status != "all" and row["status"] != status:
            continue
        if department_id and not any(it["assignment"].get("department_id") == department_id and it["status"] == "active" for it in row["employments"]):
            continue
        out.append(row)
    return out


@router.get("/employees/{employee_id}")
def employee(employee_id: str, on: Optional[date] = None, user: CurrentUser = Depends(_reader), db: Session = Depends(get_db)):
    e, emps = _visible_employee(db, user, employee_id)
    return _employee_out(db, e, emps, on or today())


def _add_event(db: Session, user: CurrentUser, m: HrEmployment, kind: str, eff: date, data: dict, eff_to: Optional[date] = None,
               cancels_id: Optional[str] = None, comment: str = "") -> HrEmployeeEvent:
    ev = HrEmployeeEvent(employment_id=m.id, employee_id=m.employee_id, employer_id=m.employer_id, kind=kind, effective_from=eff,
                         effective_to=eff_to, data=data, cancels_id=cancels_id, comment=comment, recorded_by=user.id)
    db.add(ev)
    db.flush()
    _log(db, user, "hr_employment", m.id, kind, [k for k in data if k != "reason"])
    return ev


def _hire(db: Session, user: CurrentUser, e: HrEmployee, h: HireIn) -> HrEmployment:
    _employer(db, user, h.employer_id)
    if hc.overlaps(db, e.id, h.employer_id, h.start_date, h.kind):
        raise HTTPException(409, "У сотрудника уже есть основное место у этого работодателя на эту дату — сначала увольнение")
    data = _assign_data(db, h.employer_id, h, need=True)
    m = HrEmployment(employee_id=e.id, employer_id=h.employer_id, kind=h.kind, start_date=h.start_date, external_id=h.external_id.strip())
    db.add(m)
    db.flush()
    _add_event(db, user, m, "hire", h.start_date, data, comment=h.comment)
    return m


@router.post("/employees")
def employee_create(body: EmployeeIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    """Новый сотрудник создаётся вместе с приёмом: без трудовых отношений его никто, кроме ЗГД, не увидит."""
    _employer(db, user, body.hire.employer_id)
    _check_user_link(db, body.user_id)
    e = HrEmployee(personnel_no=hc.next_personnel_no(db), last_name=body.last_name.strip(), first_name=body.first_name.strip(),
                   middle_name=body.middle_name.strip(), user_id=body.user_id or None, created_by=user.id)
    db.add(e)
    db.flush()
    _log(db, user, "hr_employee", e.id, "create")
    _hire(db, user, e, body.hire)
    db.commit()
    return employee(e.id, None, user, db)


@router.patch("/employees/{employee_id}")
def employee_update(employee_id: str, body: EmployeePatch, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    e, _ = _visible_employee(db, user, employee_id)
    data = body.model_dump(exclude_unset=True)
    if "user_id" in data:
        _check_user_link(db, data["user_id"], e.id)
        e.user_id = data["user_id"] or None
        if e.user_id:   # D-206: допуски, заведённые в сервисе на эту учётную запись, — к карточке сотрудника
            from ..models import EngineerPermit
            mine = set(db.scalars(select(EngineerPermit.permit_code).where(EngineerPermit.employee_id == e.id)).all())
            for p in db.scalars(select(EngineerPermit).where(EngineerPermit.user_id == e.user_id, EngineerPermit.employee_id.is_(None))):
                if p.permit_code not in mine:
                    p.employee_id = e.id
    for k in ("last_name", "first_name", "middle_name"):
        if data.get(k) is not None:
            setattr(e, k, data[k].strip())
    _log(db, user, "hr_employee", e.id, "update", list(data))
    db.commit()
    return employee(e.id, None, user, db)


@router.post("/employees/{employee_id}/employments")
def employment_create(employee_id: str, body: HireIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    """Повторный приём или совместительство — новые трудовые отношения; прежние остаются в истории."""
    e = db.get(HrEmployee, employee_id)
    if not e:
        raise HTTPException(404, NOT_FOUND)
    # нанять «своего» сотрудника можно, только если он уже виден пользователю (иначе — новая карточка)
    _visible_employee(db, user, employee_id)
    _hire(db, user, e, body)
    db.commit()
    return employee(e.id, None, user, db)


@router.post("/employments/{employment_id}/events")
def employment_event(employment_id: str, body: EventIn, user: CurrentUser = Depends(_editor), db: Session = Depends(get_db)):
    m = _scoped(db, user, HrEmployment, employment_id)
    evs = hc.events_of(db, [m.id])[m.id]
    dead = hc.cancelled_ids(evs)
    if body.kind == "cancel":
        target = next((x for x in evs if x.id == body.cancels_id), None)
        if not target:
            raise HTTPException(404, "Событие не найдено")
        if target.kind in ("hire", "cancel"):
            raise HTTPException(422, "Приём и отмену не отменяют. Ошибочный приём — увольнение датой приёма с причиной «ошибка ввода»")
        if target.id in dead:
            raise HTTPException(409, "Событие уже отменено")
        if not body.comment.strip():
            raise HTTPException(422, "Укажите причину отмены")
        _add_event(db, user, m, "cancel", target.effective_from, {}, cancels_id=target.id, comment=body.comment.strip())
        if target.kind == "terminate":
            m.end_date = None
        db.commit()
        return employee(m.employee_id, None, user, db)
    eff = body.effective_from or today()
    if eff < m.start_date:
        raise HTTPException(422, "Дата раньше приёма")
    if m.end_date and eff > m.end_date:
        raise HTTPException(409, "Трудовые отношения уже прекращены")
    if body.kind == "terminate":
        later = [x for x in evs if x.id not in dead and x.kind in ("transfer", "rate") and x.effective_from > eff]
        if later:
            raise HTTPException(409, "После даты увольнения есть переводы — сначала отмените их")
        _add_event(db, user, m, "terminate", eff, {"reason": body.reason.strip()}, comment=body.comment)
        m.end_date = eff
        db.commit()
        return employee(m.employee_id, None, user, db)
    _valid_dates(eff, body.effective_to)
    if body.kind == "rate":
        if body.rate is None:
            raise HTTPException(422, "Укажите ставку")
        data = {"rate": body.rate}
    else:
        data = _assign_data(db, m.employer_id, body, need=False)
        if not data:
            raise HTTPException(422, "Укажите, куда перевод: подразделение, должность или штатную единицу")
    _add_event(db, user, m, body.kind, eff, data, eff_to=body.effective_to, comment=body.comment)
    db.commit()
    return employee(m.employee_id, None, user, db)
