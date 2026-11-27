"""Блок «Персонал», L4 (D-206): допуски, обучение, инструктажи и медосмотры сотрудников.

Справочник — общий с сервисом (service_permits, у записи категория). Допуски — общая таблица engineer_permits:
запись принадлежит карточке сотрудника (employee_id) и/или учётной записи CRM (user_id). Сервис при назначении
инженера на наряд продолжает проверять допуски — и видит заведённые здесь (services/engineer_permits.owned).
Медосмотр — только итог (годен / ограниченно годен / не годен) и срок, без диагноза.
Права: группа hr_core (просмотр — READ, изменения — EDIT); область по юрлицам — как в api/hr.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.clock import today
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import EngineerPermit, HrEmployee, HrEmployment, ServicePermit
from ..services import hr_core as hc
from ..services.deal_service import audit
from ..services.engineer_permits import owned
from .hr import _visible_employee

router = APIRouter(prefix="/api/hr", tags=["hr"])

CATEGORIES = {"permit": "Допуск", "training": "Обучение, квалификация", "briefing": "Инструктаж", "medical": "Медосмотр"}
RESULTS = {"": "", "fit": "годен", "limited": "годен с ограничениями", "unfit": "не годен"}
SOON_DAYS = 30


def _need(user: CurrentUser, level: int = READ) -> None:
    if not user.can("hr_core", level):
        raise HTTPException(403, "Нет доступа к разделу «Персонал»" + (" (нужно право редактирования)" if level == EDIT else ""))


def _state(p: EngineerPermit, on: date) -> str:
    if p.result == "unfit":
        return "unfit"
    if p.valid_until and p.valid_until < on:
        return "expired"
    if p.valid_until and p.valid_until <= on + timedelta(days=SOON_DAYS):
        return "expiring"
    return "ok"


def _out(p: EngineerPermit, kinds: dict[str, ServicePermit], on: date) -> dict:
    k = kinds.get(p.permit_code)
    return {"id": p.id, "permit_code": p.permit_code, "name": k.name if k else p.permit_code, "category": k.category if k else "permit",
            "number": p.number, "issued_on": p.issued_on, "valid_until": p.valid_until, "result": p.result, "result_title": RESULTS.get(p.result, p.result),
            "comment": p.comment, "state": _state(p, on), "from_service": p.employee_id is None, "updated_at": p.updated_at}


def _kinds(db: Session) -> dict[str, ServicePermit]:
    return {k.code: k for k in db.scalars(select(ServicePermit))}


def _best(rows: list[EngineerPermit]) -> list[EngineerPermit]:
    from ..services.engineer_permits import best_by_code
    return list(best_by_code(rows).values())


# ───────────── справочник

class KindIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9А-Яа-яЁё_.\-]+$")
    name: str = Field(..., min_length=1, max_length=255)
    category: Literal["permit", "training", "briefing", "medical"] = "permit"
    valid_months: Optional[int] = Field(None, ge=1, le=240)


@router.get("/admission-kinds")
def kinds(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user)
    rows = db.scalars(select(ServicePermit).where(ServicePermit.is_active.is_(True)).order_by(ServicePermit.category, ServicePermit.name)).all()
    return {"categories": CATEGORIES, "results": {k: v for k, v in RESULTS.items() if k},
            "items": [{"code": k.code, "name": k.name, "category": k.category, "valid_months": k.valid_months, "is_draft": k.is_draft} for k in rows]}


@router.post("/admission-kinds", status_code=201)
def kind_create(body: KindIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Новый вид допуска, обучения, инструктажа или медосмотра — в общий справочник (его видит и сервис)."""
    _need(user, EDIT)
    code = body.code.strip().upper()
    if db.scalar(select(ServicePermit.id).where(ServicePermit.code == code)):
        raise HTTPException(409, "Такой код уже есть в справочнике")
    k = ServicePermit(code=code, name=body.name.strip(), category=body.category, valid_months=body.valid_months, is_draft=False, is_active=True)
    db.add(k)
    db.flush()
    audit(db, user.id, None, "service_permit", k.id, "create", after={"code": code, "category": body.category})
    db.commit()
    return {"code": k.code, "name": k.name, "category": k.category, "valid_months": k.valid_months, "is_draft": k.is_draft}


# ───────────── допуски сотрудника

class AdmissionIn(BaseModel):
    permit_code: str = Field(..., min_length=1, max_length=32)
    number: str = Field("", max_length=64)
    issued_on: Optional[date] = None
    valid_until: Optional[date] = None
    result: Literal["", "fit", "limited", "unfit"] = ""
    comment: str = Field("", max_length=2000)


@router.get("/employees/{employee_id}/admissions")
def admissions(employee_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user)
    e, _ = _visible_employee(db, user, employee_id)
    on = today()
    ks = _kinds(db)
    items = sorted((_out(p, ks, on) for p in _best(owned(db, employee_id=e.id))), key=lambda x: (x["category"], x["name"]))
    return {"items": items, "has_login": bool(e.user_id)}


def _valid(ref: ServicePermit, issued: Optional[date], valid: Optional[date]) -> Optional[date]:
    if valid is None and issued and ref.valid_months:
        m = issued.month - 1 + ref.valid_months
        y, m = issued.year + m // 12, m % 12 + 1
        valid = date(y, m, min(issued.day, calendar.monthrange(y, m)[1]))
    if issued and valid and valid < issued:
        raise HTTPException(422, "Срок действия раньше даты выдачи")
    return valid


@router.put("/employees/{employee_id}/admissions")
def admission_save(employee_id: str, body: AdmissionIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Завести или продлить. Пустой срок — от даты выдачи по сроку из справочника (пусто в справочнике — бессрочно)."""
    _need(user, EDIT)
    e, _ = _visible_employee(db, user, employee_id)
    ref = db.scalar(select(ServicePermit).where(ServicePermit.code == body.permit_code))
    if ref is None:
        raise HTTPException(422, "Нет такого вида в справочнике")
    if body.result and ref.category != "medical":
        raise HTTPException(422, "Итог «годен / не годен» — только для медосмотра")
    valid = _valid(ref, body.issued_on, body.valid_until)
    same = [p for p in owned(db, employee_id=e.id) if p.permit_code == ref.code]
    p = _best(same)[0] if same else None
    before = {"number": p.number, "issued_on": p.issued_on, "valid_until": p.valid_until, "result": p.result} if p else None
    if p is None:
        p = EngineerPermit(employee_id=e.id, user_id=e.user_id, permit_code=ref.code, created_by=user.id)
        db.add(p)
    elif p.employee_id is None:
        p.employee_id = e.id   # допуск, заведённый в сервисе, теперь и в карточке сотрудника
    p.number, p.issued_on, p.valid_until, p.result, p.comment = body.number.strip(), body.issued_on, valid, body.result, body.comment.strip()
    db.flush()
    audit(db, user.id, None, "engineer_permit", p.id, "update" if before else "create", before=before,
          after={"number": p.number, "issued_on": p.issued_on, "valid_until": p.valid_until, "result": p.result, "employee_id": e.id})
    db.commit()
    return _out(p, _kinds(db), today())


@router.delete("/admissions/{permit_id}")
def admission_delete(permit_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user, EDIT)
    p = db.get(EngineerPermit, permit_id)
    if p is None:
        raise HTTPException(404, "Не найдено")
    eid = p.employee_id or db.scalar(select(HrEmployee.id).where(HrEmployee.user_id == p.user_id))
    if not eid:
        raise HTTPException(404, "Не найдено")
    _visible_employee(db, user, eid)
    audit(db, user.id, None, "engineer_permit", p.id, "delete",
          before={"permit_code": p.permit_code, "number": p.number, "valid_until": p.valid_until, "employee_id": eid})
    db.delete(p)
    db.commit()
    return {"ok": True}


# ───────────── сводка: что истекает

@router.get("/admissions/overview")
def overview(days: int = Query(SOON_DAYS, ge=1, le=365), employer_id: Optional[str] = None,
             user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Истёкшие, истекающие в ближайшие N дней и «не годен» — по работающим сотрудникам из области пользователя."""
    _need(user)
    on = today()
    scope = hc.allowed_employers(db, user)
    emps = [m for m in db.scalars(select(HrEmployment)) if hc.in_scope(scope, m.employer_id) and hc.status_on(m, on) == "active"
            and (not employer_id or m.employer_id == employer_id)]
    people: dict[str, list[str]] = {}
    for m in emps:
        people.setdefault(m.employee_id, []).append(m.employer_id)
    if not people:
        return {"items": [], "days": days}
    ks = _kinds(db)
    employees = {e.id: e for e in db.scalars(select(HrEmployee).where(HrEmployee.id.in_(list(people))))}
    out = []
    for eid, e in employees.items():
        for p in _best(owned(db, employee_id=eid)):
            st = _state(p, on)
            if st == "unfit" or st == "expired" or (p.valid_until and p.valid_until <= on + timedelta(days=days)):
                out.append({**_out(p, ks, on), "state": st if st != "ok" else "expiring", "employee_id": eid, "employee": hc.full_name(e), "personnel_no": e.personnel_no})
    rank = {"unfit": 0, "expired": 1, "expiring": 2}
    out.sort(key=lambda x: (rank.get(x["state"], 3), x["valid_until"] or date(9999, 12, 31), x["employee"]))
    return {"items": out, "days": days}
