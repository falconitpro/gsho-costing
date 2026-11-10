"""Уведомления «в системе»: значок с числом непрочитанных, список за 30 дней, отметка «прочитано»."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import Deal, NotificationLog

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
EVENT_TITLES = {"task": "Задача", "task_new": "Задача", "task_overdue": "Просрочка", "legal_doc": "Документы", "acceptance": "Приёмка",
                "rework": "Доработка", "approval": "Утверждение", "parts_sla": "Запчасти", "to_reminder": "ТО машины",
                "backup": "Резервные копии"}
# куда ведёт уведомление без сделки (события эксплуатации)
EVENT_ROUTES = {"backup": "/settings?s=backup"}


def _base(db: Session, user: CurrentUser):
    since = datetime.now(timezone.utc) - timedelta(days=30)
    return select(NotificationLog).where(NotificationLog.user_id == user.id, NotificationLog.channel == "app", NotificationLog.at >= since)


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    n = db.scalar(select(func.count()).select_from(_base(db, user).where(NotificationLog.read_at.is_(None)).subquery()))
    return {"unread": int(n or 0)}


@router.get("")
def list_notifications(limit: int = 50, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(_base(db, user).order_by(NotificationLog.at.desc()).limit(min(limit, 200))).all()
    deals = {d.id: d.number for d in db.scalars(select(Deal).where(Deal.id.in_({r.deal_id for r in rows if r.deal_id}))).all()} if rows else {}
    return {"unread": sum(1 for r in rows if r.read_at is None),
            "items": [{"id": r.id, "at": r.at.isoformat() if r.at else None, "event": r.event, "event_title": EVENT_TITLES.get(r.event, r.event),
                       "text": r.text, "deal_id": r.deal_id, "deal_number": deals.get(r.deal_id, ""), "url": EVENT_ROUTES.get(r.event), "read": r.read_at is not None} for r in rows]}


class ReadIn(BaseModel):
    ids: Optional[list[str]] = None   # пусто — все


@router.post("/read")
def mark_read(body: ReadIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    q = update(NotificationLog).where(NotificationLog.user_id == user.id, NotificationLog.channel == "app", NotificationLog.read_at.is_(None))
    if body.ids:
        q = q.where(NotificationLog.id.in_(body.ids))
    db.execute(q.values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return unread_count(db, user)
