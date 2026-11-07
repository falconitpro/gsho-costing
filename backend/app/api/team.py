"""Сотрудники: карточки контактов исполнителей и каналы уведомлений о задачах."""
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
            "deputy_id": u.deputy_id, "deputy_from": u.deputy_from.isoformat() if u.deputy_from else None, "deputy_to": u.deputy_to.isoformat() if u.deputy_to else None,
            "deputy_active": _deputy_active(u), "observer_scope": u.observer_scope or {},
            "can_edit": viewer.id == u.id or _can_channels(viewer), "can_channels": _can_channels(viewer),
            "can_manage": viewer.has(Role.GD, Role.ADMIN), "pending_changes": u.pending_changes or {}}


def _deputy_active(u: User) -> bool:
    if not u.deputy_id:
        return False
    today = date.today()
    return (u.deputy_from is None or u.deputy_from <= today) and (u.deputy_to is None or u.deputy_to >= today)


class DeputyIn(BaseModel):
    deputy_id: Optional[str] = None   # пусто — снять заместителя
    deputy_from: Optional[date] = None
    deputy_to: Optional[date] = None


@router.put("/{user_id}/deputy")
def set_deputy(user_id: str, body: DeputyIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Заместитель на период: видит сделки и задачи замещаемого как свои, получает его уведомления. Назначает сам сотрудник, КД, ЗГД или администратор."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    if not (user.id == u.id or _can_channels(user)):
        raise HTTPException(403, "Заместителя назначает сам сотрудник, КД, ЗГД или администратор")
    if body.deputy_id:
        d = db.get(User, body.deputy_id)
        if d is None or not d.is_active:
            raise HTTPException(422, "Заместитель не найден или отключён")
        if d.id == u.id:
            raise HTTPException(422, "Нельзя назначить заместителем самого себя")
        if body.deputy_from and body.deputy_to and body.deputy_to < body.deputy_from:
            raise HTTPException(422, "Дата окончания раньше даты начала")
    before = {"deputy_id": u.deputy_id, "deputy_from": str(u.deputy_from or ""), "deputy_to": str(u.deputy_to or "")}
    u.deputy_id, u.deputy_from, u.deputy_to = body.deputy_id or None, body.deputy_from if body.deputy_id else None, body.deputy_to if body.deputy_id else None
    audit(db, user.id, None, "user", u.id, "deputy", before, body.model_dump(mode="json"))
    if body.deputy_id:
        notify(db, None, body.deputy_id, "deputy", None, f"Вы назначены заместителем: {u.display_name}" + (f" с {body.deputy_from:%d.%m}" if body.deputy_from else "") + (f" по {body.deputy_to:%d.%m}" if body.deputy_to else ""))
    db.commit()
    return _card(u, user)


class ScopeIn(BaseModel):
    all: bool = False
    client_ids: list[str] = []
    manager_ids: list[str] = []
    deal_ids: list[str] = []
    phase: Optional[str] = None      # sale | contract | None — обе
    until: Optional[date] = None     # срок доступа


@router.put("/{user_id}/observer-scope")
def set_scope(user_id: str, body: ScopeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Область наблюдателя: по умолчанию пустая — ничего не видно. КД, ЗГД, администратор."""
    if not _can_channels(user):
        raise HTTPException(403)
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    if body.phase not in (None, "", "sale", "contract"):
        raise HTTPException(422, "Фаза: sale, contract или пусто")
    before = u.observer_scope
    u.observer_scope = {k: v for k, v in body.model_dump(mode="json").items() if v}
    audit(db, user.id, None, "user", u.id, "observer_scope", before, u.observer_scope)
    db.commit()
    return _card(u, user)


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
    display_name: Optional[str] = None     # ФИО тоже правится из карточки (у сотрудника — с подтверждением)


FIELD_TITLES = {"display_name": "ФИО", "wecom_user_id": "WeCom ID", "position": "Должность", "department": "Подразделение", "mobile": "Мобильный", "work_phone": "Рабочий телефон",
                "wechat": "WeChat", "telegram": "Telegram", "whatsapp": "WhatsApp", "max": "MAX", "extra_email": "Доп. e-mail", "note": "Примечание"}


@router.put("/{user_id}/contacts")
def set_contacts(user_id: str, body: ContactsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка сотрудника. Администратор и ЗГД меняют всё сразу. Сам сотрудник: пустые поля заполняет сразу, а изменение уже заполненного значения
    уходит на подтверждение администратору / ЗГД (до подтверждения действует прежнее значение). КД — как сотрудник, но для любого."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    manage = user.has(Role.GD, Role.ADMIN)
    if not (user.id == u.id or _can_channels(user)):
        raise HTTPException(403, "Карточку сотрудника меняет он сам, КД, ЗГД или администратор")
    before = dict(u.contacts or {})
    new_contacts = {k: str(body.contacts.get(k, before.get(k, "")) or "").strip()[:255] for k in CONTACT_FIELDS}
    wanted: dict[str, tuple[str, str]] = {k: (before.get(k, "") or "", v) for k, v in new_contacts.items() if (before.get(k, "") or "") != v}
    if body.wecom_user_id is not None and (body.wecom_user_id.strip() or None) != u.wecom_user_id:
        wanted["wecom_user_id"] = (u.wecom_user_id or "", body.wecom_user_id.strip())
    if body.display_name is not None and body.display_name.strip() and body.display_name.strip() != u.display_name:
        wanted["display_name"] = (u.display_name, body.display_name.strip()[:255])
    applied, pending = {}, {}
    for k, (old, new) in wanted.items():
        if manage or not old:                 # администратор/ЗГД — сразу; пустое поле — сразу; иначе на подтверждение
            applied[k] = [old, new]
        else:
            pending[k] = [old, new]
    for k, (_, new) in ((k, v) for k, v in applied.items()):
        if k == "display_name":
            u.display_name = new
        elif k == "wecom_user_id":
            u.wecom_user_id = new or None
        else:
            u.contacts = {**(u.contacts or {}), k: new}
    if pending:
        cur = dict(u.pending_changes or {})
        fields = {**(cur.get("fields") or {}), **pending}
        u.pending_changes = {"fields": fields, "requested_at": date.today().isoformat(), "requested_by": user.id}
        for admin_id in db.scalars(select(User.id).where(User.is_active.is_(True))).all():
            adm = db.get(User, admin_id)
            if adm and ({"admin", "gd"} & set(adm.roles or [])) and adm.id != user.id:
                try:
                    notify(db, None, adm.id, "user_change", None, f"{u.display_name}: запрос на изменение карточки ({', '.join(FIELD_TITLES.get(k, k) for k in pending)}) — подтвердите в «Настройки → Сотрудники»")
                except Exception:  # noqa: BLE001
                    pass
    if applied:
        audit(db, user.id, None, "user", u.id, "contacts", {k: v[0] for k, v in applied.items()}, {k: v[1] for k, v in applied.items()})
    if pending:
        audit(db, user.id, None, "user", u.id, "contacts_request", {k: v[0] for k, v in pending.items()}, {k: v[1] for k, v in pending.items()})
    db.commit()
    return {**_card(u, user), "applied": list(applied), "pending": list(pending)}


class DecisionIn(BaseModel):
    approve: bool
    fields: Optional[list[str]] = None     # подтвердить/отклонить только часть полей; пусто — все
    comment: str = ""


@router.post("/{user_id}/pending/decide")
def decide_pending(user_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Администратор / ЗГД подтверждает или отклоняет изменения карточки сотрудника."""
    if not user.has(Role.GD, Role.ADMIN):
        raise HTTPException(403, "Изменения карточки подтверждает администратор или ЗГД")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    pend = dict((u.pending_changes or {}).get("fields") or {})
    if not pend:
        raise HTTPException(409, "Изменений, ожидающих подтверждения, нет")
    keys = [k for k in (body.fields or list(pend)) if k in pend]
    for k in keys:
        old, new = pend.pop(k)
        if body.approve:
            if k == "display_name":
                u.display_name = new
            elif k == "wecom_user_id":
                u.wecom_user_id = new or None
            else:
                u.contacts = {**(u.contacts or {}), k: new}
    u.pending_changes = {**(u.pending_changes or {}), "fields": pend} if pend else {}
    audit(db, user.id, None, "user", u.id, "contacts_approve" if body.approve else "contacts_reject", after={"fields": keys, "comment": body.comment})
    try:
        notify(db, None, u.id, "user_change", None, f"Изменения карточки ({', '.join(FIELD_TITLES.get(k, k) for k in keys)}) {'подтверждены' if body.approve else 'отклонены'}" + (f": {body.comment}" if body.comment else ""))
    except Exception:  # noqa: BLE001
        pass
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
