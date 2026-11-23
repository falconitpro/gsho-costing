"""Управление (D-154): руководители направлений и поручения ЗГД. Права — здесь и в services/assignments.py."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import ROLE_TITLES, Role
from ..db import get_db
from ..models import Assignment, AssignmentEvent, OrgDirection, User
from ..services import assignments as svc
from ..services.deal_service import audit
from ..services.finance.cashview import moscow_today

router = APIRouter(prefix="/api/management", tags=["management"])


def _names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


def _position(u: User) -> str:
    pos = (u.contacts or {}).get("position", "") if isinstance(u.contacts, dict) else ""
    if pos:
        return pos
    titles = [ROLE_TITLES.get(Role(r), r) for r in (u.roles or []) if r in Role._value2member_map_]
    return titles[0] if titles else ""


# ------------------------------------------------------------------ направления

@router.get("/directions")
def directions(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.has(Role.GD, Role.ADMIN):
        raise HTTPException(403, "Оргструктуру видит и назначает ЗГД")
    today = moscow_today()
    users = {u.id: u for u in db.scalars(select(User)).all()}
    hd = svc.heads(db)
    out = []
    for key, title in svc.DIRECTIONS:
        h = users.get(hd.get(key) or "")
        out.append({"key": key, "title": title, "head_id": h.id if h else None, "head": h.display_name if h else None,
                    "position": _position(h) if h else "", "deputy": svc.deputy_info(h, users, today) if h else None,
                    "head_active": bool(h and h.is_active)})
    return {"items": out, "users": [{"id": u.id, "name": u.display_name, "position": _position(u), "direction": svc.direction_of(u)}
                                    for u in sorted(users.values(), key=lambda x: x.display_name) if u.is_active]}


class DirectionsIn(BaseModel):
    heads: dict[str, Optional[str]]


@router.put("/directions")
def set_directions(body: DirectionsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.has(Role.GD):
        raise HTTPException(403, "Руководителей направлений назначает ЗГД")
    before = svc.heads(db)
    for key, uid in body.heads.items():
        if key not in svc.DIRECTION_TITLES:
            raise HTTPException(422, f"Неизвестное направление: {key}")
        if uid:
            u = db.get(User, uid)
            if not u or not u.is_active:
                raise HTTPException(422, "Сотрудник не найден или отключён")
        row = db.get(OrgDirection, key)
        if not row:
            row = OrgDirection(key=key)
            db.add(row)
        row.head_id, row.updated_by = uid or None, user.id
    audit(db, user.id, None, "org_direction", "heads", "update", before, {**before, **{k: v or None for k, v in body.heads.items()}})
    db.commit()
    return directions(db, user)


# ------------------------------------------------------------------ поручения

class AssignmentIn(BaseModel):
    title: str
    body: str = ""
    mode: str = "direct"
    assignee_id: str
    direction_key: str = ""
    due_on: date
    priority: str = "normal"
    kind: str = "task"
    source_url: str = ""
    source_key: str = ""
    source_title: str = ""


class ActionIn(BaseModel):
    action: str
    comment: str = ""
    report: str = ""
    due_on: Optional[date] = None
    executor_id: Optional[str] = None


@router.get("/assignments")
def list_assignments(scope: str = "all", status: str = "open", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """scope: all — всё, что вижу; mine — мне исполнять; control — я автор; team — моему подразделению (я руководитель).
    status: open | overdue | review | done | cancelled | any."""
    today = moscow_today()
    q = svc.visible_query(user)
    me = list({user.id} | set(user.acting_for or set()))
    if scope == "mine":
        q = q.where((Assignment.assignee_id.in_(me)) | (Assignment.executor_id.in_(me)))
    elif scope == "control":
        q = q.where(Assignment.author_id.in_(me))
    elif scope == "team":
        q = q.where(Assignment.head_id.in_(me))
    if status == "open":
        q = q.where(Assignment.status.in_(svc.OPEN))
    elif status == "overdue":
        q = q.where(Assignment.status.in_(svc.OPEN), Assignment.due_on < today)
    elif status in svc.STATUS_TITLES:
        q = q.where(Assignment.status == status)
    rows = db.scalars(q.order_by(Assignment.due_on, Assignment.seq)).all()
    names = _names(db)
    pr = {"critical": 0, "high": 1, "normal": 2}
    items = sorted((svc.as_dict(a, names, today, user) for a in rows), key=lambda x: (not x["overdue"], pr.get(x["priority"], 2), x["due_on"]))
    return {"items": items, "can_create": user.has(Role.GD), "today": today.isoformat(),
            "statuses": svc.STATUS_TITLES, "directions": [{"key": k, "title": t} for k, t in svc.DIRECTIONS]}


@router.post("/assignments")
def create_assignment(body: AssignmentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    a = svc.create(db, user, **body.model_dump())
    audit(db, user.id, None, "assignment", a.id, "create", None, {"number": a.number, "assignee_id": a.assignee_id, "mode": a.mode})
    db.commit()
    return get_assignment(a.id, db, user)


@router.get("/assignments/{aid}")
def get_assignment(aid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    a = db.get(Assignment, aid)
    if not a or not svc.can_see(user, a):
        raise HTTPException(404, "Поручение не найдено")
    names = _names(db)
    out = svc.as_dict(a, names, moscow_today(), user)
    ev = db.scalars(select(AssignmentEvent).where(AssignmentEvent.assignment_id == a.id).order_by(AssignmentEvent.at, AssignmentEvent.id)).all()
    out["history"] = [{"at": e.at.isoformat() if e.at else None, "by": names.get(e.by_id or "", ""), "action": e.action,
                       "action_title": svc.ACTION_TITLES.get(e.action, e.action), "from": svc.STATUS_TITLES.get(e.from_status, ""),
                       "to": svc.STATUS_TITLES.get(e.to_status, ""), "comment": e.comment, "data": e.data} for e in ev]
    return out


@router.post("/assignments/{aid}/action")
def assignment_action(aid: str, body: ActionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    a = db.get(Assignment, aid)
    if not a or not svc.can_see(user, a):
        raise HTTPException(404, "Поручение не найдено")
    svc.act(db, user, a, body.action, comment=body.comment, report=body.report, due_on=body.due_on, executor_id=body.executor_id)
    db.commit()
    return get_assignment(a.id, db, user)


@router.get("/assignees")
def assignees(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Кому можно поручить: активные сотрудники с подразделением; руководители направлений."""
    users = db.scalars(select(User).where(User.is_active.is_(True))).all()
    hd = svc.heads(db)
    return {"users": [{"id": u.id, "name": u.display_name, "position": _position(u), "direction": svc.direction_of(u)}
                      for u in sorted(users, key=lambda x: x.display_name)],
            "heads": hd, "directions": [{"key": k, "title": t} for k, t in svc.DIRECTIONS]}
