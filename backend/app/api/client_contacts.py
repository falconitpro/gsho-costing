"""Контактные лица клиентов (D-184, K-1). Персональные данные (152-ФЗ, решение 1 владельца, D-180):
только рабочие телефон, почта и мессенджер; доступ — группа «Клиенты: контактные лица»; каждый просмотр — в журнал
client_contact_views; удаление по запросу — полное, в журнале действий без значений; отметка о согласии —
когда включена настройка «Требовать отметку о согласии у контактов клиентов»."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ, Role
from ..db import get_db
from ..models import Client, ClientContact, ClientContactView, ClientSite, User
from ..services import settings_service
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/clients/contacts", tags=["client-contacts"])

CONTACT_ROLES = {"decision": "Принимает решения", "purchasing": "Закупки", "technical": "Технический специалист", "service": "Сервис и ремонт",
                 "operations": "Эксплуатация / диспетчер", "finance": "Бухгалтерия и оплата", "legal": "Юрист", "logistics": "Логистика", "other": "Другое"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^[0-9+()\-.\s]{5,40}(доб\.?\s*\d+)?$")


def _engineer_only(user: CurrentUser) -> bool:
    return bool(user.roles) and all(r == Role.ENGINEER for r in user.roles)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("client_contacts", READ) or _engineer_only(user):   # инженер — по своим нарядам, с I-1
        raise HTTPException(403, "Нет доступа к контактам клиентов")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("client_contacts", EDIT) or _engineer_only(user):
        raise HTTPException(403, "Контакты клиентов ведут менеджеры, сервис, КД и ЗГД")
    return user


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class ContactIn(BaseModel):
    client_id: str
    full_name: str = Field(min_length=1, max_length=255)
    position: str = Field(default="", max_length=255)
    department: str = Field(default="", max_length=255)
    roles: list[str] = Field(default_factory=list)
    work_phone: str = Field(default="", max_length=64)
    work_email: str = Field(default="", max_length=255)
    messenger: str = Field(default="", max_length=255)
    site_id: Optional[str] = None
    is_primary: bool = False
    is_active: bool = True
    consent_on: Optional[date] = None
    consent_source: str = Field(default="", max_length=255)
    comment: str = ""

    @model_validator(mode="after")
    def _check(self):
        self.full_name = " ".join(self.full_name.split())
        self.work_email = self.work_email.strip().lower()
        self.work_phone = self.work_phone.strip()
        if not self.full_name:
            raise ValueError("Укажите ФИО")
        if self.work_email and not EMAIL.match(self.work_email):
            raise ValueError("Почта указана с ошибкой")
        if self.work_phone and not PHONE.match(self.work_phone):
            raise ValueError("Телефон: только цифры, +, скобки, дефисы и «доб.»")
        bad = [r for r in self.roles if r not in CONTACT_ROLES]
        if bad:
            raise ValueError("Неизвестная роль контакта")
        self.roles = list(dict.fromkeys(self.roles))
        return self


def _consent_required(db: Session) -> bool:
    return bool(settings_service.get(db, "contacts_consent_required"))


def _check(db: Session, body: ContactIn, exclude: str = "") -> None:
    if db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Выберите существующего клиента")
    if body.site_id:
        site = db.get(ClientSite, body.site_id)
        if site is None or site.client_id != body.client_id:
            raise HTTPException(422, "Площадка не найдена или принадлежит другому клиенту")
    if _consent_required(db) and not (body.consent_on and body.consent_source.strip()):
        raise HTTPException(422, "Укажите дату и источник согласия на обработку персональных данных")
    others = db.scalars(select(ClientContact).where(ClientContact.client_id == body.client_id, ClientContact.is_active.is_(True),
                                                    ClientContact.id != (exclude or ""))).all()
    for o in others:
        if body.work_email and o.work_email == body.work_email:
            raise HTTPException(409, f"У клиента уже есть контакт с этой почтой: {o.full_name}")
        if body.work_phone and len(_digits(body.work_phone)) >= 7 and _digits(o.work_phone)[-10:] == _digits(body.work_phone)[-10:]:
            raise HTTPException(409, f"У клиента уже есть контакт с этим телефоном: {o.full_name}")


def _payload(c: ContactIn | ClientContact, sites: dict[str, str]) -> dict:
    d = serialize(c)
    d["roles_titles"] = [CONTACT_ROLES.get(r, r) for r in (c.roles or [])]
    d["site_name"] = sites.get(c.site_id or "", "")
    return d


def _sites(db: Session, client_id: str) -> dict[str, str]:
    return dict(db.execute(select(ClientSite.id, ClientSite.name).where(ClientSite.client_id == client_id)).all())


def _apply(db: Session, c: ClientContact, body: ContactIn) -> None:
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    if body.is_primary:   # основной контакт у клиента — один
        for o in db.scalars(select(ClientContact).where(ClientContact.client_id == body.client_id, ClientContact.is_primary.is_(True),
                                                        ClientContact.id != (c.id or ""))).all():
            o.is_primary = False


def _safe(c: ClientContact) -> dict:
    """Для журнала действий — без персональных данных."""
    return {"client_id": c.client_id, "is_active": c.is_active, "is_primary": c.is_primary, "roles": c.roles}


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return {"roles": CONTACT_ROLES, "consent_required": _consent_required(db), "can_edit": user.can("client_contacts", EDIT),
            "can_see_views": user.can("security_audit", READ)}


@router.get("")
def list_contacts(client_id: str = Query(min_length=1), include_archived: bool = False,
                  db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Клиент не найден")
    stmt = select(ClientContact).where(ClientContact.client_id == client_id)
    if not include_archived:
        stmt = stmt.where(ClientContact.is_active.is_(True))
    rows = db.scalars(stmt.order_by(ClientContact.is_primary.desc(), ClientContact.full_name)).all()
    db.add(ClientContactView(user_id=user.id, client_id=client_id, kind="list"))
    db.commit()
    sites = _sites(db, client_id)
    return {"items": [_payload(c, sites) for c in rows]}


@router.post("", status_code=201)
def create_contact(body: ContactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    _check(db, body)
    c = ClientContact(created_by=user.id)
    _apply(db, c, body)
    db.add(c)
    db.flush()
    audit(db, user.id, None, "client_contact", c.id, "create", None, _safe(c))
    db.commit()
    return _payload(c, _sites(db, c.client_id))


@router.get("/views")
def views(client_id: str = "", limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Журнал просмотров (152-ФЗ): кто и когда открывал контакты. Видят те, у кого есть журнал безопасности."""
    if not user.can("security_audit", READ):
        raise HTTPException(403, "Журнал просмотров доступен ЗГД и администратору")
    stmt = select(ClientContactView).order_by(ClientContactView.id.desc()).limit(limit)
    if client_id:
        stmt = stmt.where(ClientContactView.client_id == client_id)
    rows = db.scalars(stmt).all()
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_({r.user_id for r in rows if r.user_id}))).all()) if rows else {}
    return [{"at": r.at, "user": names.get(r.user_id, ""), "client_id": r.client_id, "contact_id": r.contact_id, "kind": r.kind} for r in rows]


@router.get("/{contact_id}")
def get_contact(contact_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    c = db.get(ClientContact, contact_id)
    if c is None:
        raise HTTPException(404, "Контакт не найден")
    db.add(ClientContactView(user_id=user.id, client_id=c.client_id, contact_id=c.id, kind="card"))
    db.commit()
    return _payload(c, _sites(db, c.client_id))


@router.put("/{contact_id}")
def update_contact(contact_id: str, body: ContactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = db.get(ClientContact, contact_id)
    if c is None:
        raise HTTPException(404, "Контакт не найден")
    if body.client_id != c.client_id:
        raise HTTPException(409, "Контакт нельзя перенести к другому клиенту — заведите новый")
    if body.is_active:
        _check(db, body, exclude=c.id)
    before = _safe(c)
    _apply(db, c, body)
    audit(db, user.id, None, "client_contact", c.id, "update", before, _safe(c))
    db.commit()
    return _payload(c, _sites(db, c.client_id))


class DeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{contact_id}/delete")
def delete_contact(contact_id: str, body: DeleteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Удаление по запросу субъекта ПДн (152-ФЗ): запись удаляется полностью; в журнале — только факт, кто и основание."""
    c = db.get(ClientContact, contact_id)
    if c is None:
        raise HTTPException(404, "Контакт не найден")
    audit(db, user.id, None, "client_contact", c.id, "delete", {"client_id": c.client_id}, None, comment=body.reason.strip())
    # та же запись в прежнем списке карточки клиента (clients.contacts) — тоже удаляется, иначе данные остались бы
    cl = db.get(Client, c.client_id)
    if cl and cl.contacts:
        def same(x) -> bool:
            if not isinstance(x, dict):
                return False
            em = str(x.get("email") or "").strip().lower()
            nm = " ".join(str(x.get("name") or x.get("full_name") or "").split()).lower()
            return bool(c.work_email and em == c.work_email) or (not em and nm == c.full_name.lower())
        cl.contacts = [x for x in cl.contacts if not same(x)]
    db.delete(c)
    db.commit()
    return {"ok": True}
