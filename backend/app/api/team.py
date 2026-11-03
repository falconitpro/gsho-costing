"""Сотрудники: карточки контактов исполнителей и каналы уведомлений о задачах."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import ROLE_TITLES, Role
from ..db import get_db
from ..models import NotificationLog, User
from ..services.deal_service import CHANNELS, _channels_of, audit, notify

router = APIRouter(prefix="/api/team", tags=["team"])
CONTACT_FIELDS = ("position", "department", "mobile", "work_phone", "wechat", "telegram", "whatsapp", "max", "extra_email", "note")


def _can_channels(user: CurrentUser) -> bool:
    return user.has(Role.CD, Role.GD, Role.ADMIN)


def _card(u: User, viewer: CurrentUser) -> dict:
    return {"id": u.id, "display_name": u.display_name, "email": u.email, "roles": u.roles,
            "role_titles": [ROLE_TITLES.get(Role(r), r) for r in u.roles if r in Role._value2member_map_],
            "contacts": {k: (u.contacts or {}).get(k, "") for k in CONTACT_FIELDS}, "wecom_user_id": u.wecom_user_id or "",
            "notify_channels": _channels_of(u), "is_active": u.is_active,
            "can_edit": viewer.id == u.id or _can_channels(viewer), "can_channels": _can_channels(viewer)}


@router.get("")
def team(include_inactive: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Справочник сотрудников с контактами (виден всем пользователям системы). ЗГД и администратор добавляют сотрудников,
    назначают роли и индивидуальные доступы — им видны и отключённые учётные записи."""
    manage = user.has(Role.GD, Role.ADMIN)
    q = select(User).order_by(User.display_name)
    if not (manage and include_inactive):
        q = q.where(User.is_active.is_(True))
    roles = [{"code": r.value, "title": t} for r, t in ROLE_TITLES.items()]
    return {"users": [_card(u, user) for u in db.scalars(q).all()], "channels": CHANNELS, "can_manage": manage, "roles": roles}


class ContactsIn(BaseModel):
    contacts: dict[str, str] = {}
    wecom_user_id: Optional[str] = None


@router.put("/{user_id}/contacts")
def set_contacts(user_id: str, body: ContactsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Контакты заполняет сам сотрудник; КД, ЗГД и администратор — любому."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    if not (user.id == u.id or _can_channels(user)):
        raise HTTPException(403, "Контакты сотрудника меняет он сам, КД, ЗГД или администратор")
    before = dict(u.contacts or {})
    u.contacts = {k: str(body.contacts.get(k, "") or "").strip()[:255] for k in CONTACT_FIELDS}
    if body.wecom_user_id is not None:
        u.wecom_user_id = body.wecom_user_id.strip() or None
    audit(db, user.id, None, "user", u.id, "contacts", before, u.contacts)
    db.commit()
    return _card(u, user)


class ChannelsIn(BaseModel):
    channels: list[str]


@router.put("/{user_id}/channels")
def set_channels(user_id: str, body: ChannelsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Каналы уведомлений о задачах выбирают КД, ЗГД или администратор («в системе» — всегда)."""
    if not _can_channels(user):
        raise HTTPException(403, "Каналы уведомлений выбирают КД, ЗГД или администратор")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    bad = [c for c in body.channels if c not in CHANNELS]
    if bad:
        raise HTTPException(422, f"Неизвестные каналы: {', '.join(bad)}")
    before = list(u.notify_channels or [])
    u.notify_channels = list(dict.fromkeys(["app", *body.channels]))
    audit(db, user.id, None, "user", u.id, "notify_channels", {"channels": before}, {"channels": u.notify_channels})
    db.commit()
    return _card(u, user)


@router.post("/{user_id}/test")
def test_notification(user_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Проверочное уведомление по всем каналам сотрудника."""
    if not (user.id == user_id or _can_channels(user)):
        raise HTTPException(403)
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    notify(db, None, u.id, "test", None, f"Проверка уведомлений: {u.display_name}")
    db.commit()
    return {"log": _log(db, u.id)}


def _log(db: Session, user_id: str) -> list[dict]:
    rows = db.scalars(select(NotificationLog).where(NotificationLog.user_id == user_id).order_by(NotificationLog.at.desc()).limit(30)).all()
    return [{"at": r.at.isoformat() if r.at else None, "channel": r.channel, "channel_title": CHANNELS.get(r.channel, r.channel), "text": r.text,
             "status": r.status, "error": r.error} for r in rows]


@router.get("/{user_id}/notifications")
def notifications(user_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not (user.id == user_id or _can_channels(user)):
        raise HTTPException(403)
    return {"log": _log(db, user_id)}
