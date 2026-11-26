"""Контактные лица клиентов — единый справочник (D-184, D-188).

Контакт — человек (ФИО, должность, рабочие телефон, почта, доп. адреса, мессенджер). Связь контакт ↔ клиент
(client_contact_links) — роли, площадка, «основной», период «с — по»: один человек может быть у нескольких организаций.
Персональные данные (152-ФЗ, D-180): только рабочие каналы; доступ — группа «Клиенты: контактные лица»; каждый просмотр —
в журнал; история изменений — отдельно, с доступом по правке; удаление по запросу — обезличивание (запись и ссылки
остаются, данные стираются; отправленные КП не меняются)."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ, Role
from ..db import get_db
from ..models import Client, ClientContact, ClientContactHistory, ClientContactLink, ClientContactView, ClientSite, User
from ..services import settings_service
from ..services.deal_service import audit

router = APIRouter(prefix="/api/clients/contacts", tags=["client-contacts"])

CONTACT_ROLES = {"decision": "Принимает решения", "commercial": "Коммерческий", "purchasing": "Закупки", "technical": "Технический специалист",
                 "service": "Сервис и ремонт", "operations": "Эксплуатация / диспетчер", "finance": "Бухгалтерия и оплата", "logistics": "Логистика",
                 # юридические роли (решение владельца «Единый справочник контактов» v1.0, раздел 4)
                 "director": "Руководитель", "poa": "Представитель по доверенности", "legal_mail": "Юридически значимая переписка",
                 "contracts": "Договоры и документы", "legal": "Юридический отдел", "other": "Другое"}
LEGAL_ROLES = ("director", "poa", "legal_mail", "contracts", "legal")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^[0-9+()\-.\s]{5,40}(доб\.?\s*\d+)?$")
PERSON_FIELDS = ("full_name", "position", "department", "work_phone", "work_email", "messenger", "extra_emails", "consent_on", "consent_source", "comment")


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


def active_link():
    t = date.today()
    return (ClientContactLink.is_active.is_(True)) & (or_(ClientContactLink.valid_to.is_(None), ClientContactLink.valid_to >= t))


class ContactIn(BaseModel):
    client_id: str                                  # клиент, для которого создаётся / правится связь
    full_name: str = Field(min_length=1, max_length=255)
    position: str = Field(default="", max_length=255)
    department: str = Field(default="", max_length=255)
    roles: list[str] = Field(default_factory=list)
    work_phone: str = Field(default="", max_length=64)
    work_email: str = Field(default="", max_length=255)
    extra_emails: list[dict] = Field(default_factory=list)   # [{email, label}]
    messenger: str = Field(default="", max_length=255)
    site_id: Optional[str] = None
    is_primary: bool = False
    is_active: bool = True
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
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
        extra, seen = [], {self.work_email} if self.work_email else set()
        for x in self.extra_emails:
            em = str((x or {}).get("email") or "").strip().lower()
            if not em:
                continue
            if not EMAIL.match(em):
                raise ValueError(f"Почта указана с ошибкой: {em}")
            if em in seen:
                continue
            seen.add(em)
            extra.append({"email": em, "label": str((x or {}).get("label") or "")[:64]})
        self.extra_emails = extra
        if any(r not in CONTACT_ROLES for r in self.roles):
            raise ValueError("Неизвестная роль контакта")
        self.roles = list(dict.fromkeys(self.roles))
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("Дата окончания раньше даты начала")
        return self


def _consent_required(db: Session) -> bool:
    return bool(settings_service.get(db, "contacts_consent_required"))


def emails_of(c: ClientContact) -> list[str]:
    return [e for e in [c.work_email, *[x.get("email", "") for x in (c.extra_emails or [])]] if e]


def _check(db: Session, body: ContactIn, exclude_contact: str = "") -> None:
    if db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Выберите существующего клиента")
    if body.site_id:
        site = db.get(ClientSite, body.site_id)
        if site is None or site.client_id != body.client_id:
            raise HTTPException(422, "Площадка не найдена или принадлежит другому клиенту")
    if _consent_required(db) and not exclude_contact and not (body.consent_on and body.consent_source.strip()):
        raise HTTPException(422, "Укажите дату и источник согласия на обработку персональных данных")
    mine = {e for e in [body.work_email, *[x["email"] for x in body.extra_emails]] if e}
    # тот же человек у этого клиента — по почте или телефону (одно ФИО не считается: решение v1.0, п. 5.1)
    for o, _ in client_contacts(db, body.client_id):
        if o.id == exclude_contact:
            continue
        if mine & set(emails_of(o)):
            raise HTTPException(409, f"У клиента уже есть контакт с этой почтой: {o.full_name}")
        if body.work_phone and len(_digits(body.work_phone)) >= 7 and _digits(o.work_phone)[-10:] == _digits(body.work_phone)[-10:]:
            raise HTTPException(409, f"У клиента уже есть контакт с этим телефоном: {o.full_name}")
    # тот же человек у другого клиента — привязать, а не заводить второго
    if mine and not exclude_contact:
        for o in db.scalars(select(ClientContact).where(ClientContact.anonymized_at.is_(None))).all():
            if mine & set(emails_of(o)):
                raise HTTPException(409, {"message": f"Этот человек уже есть в справочнике: {o.full_name}. Привяжите его к клиенту, а не заводите заново.",
                                          "contact_id": o.id})


def _sites(db: Session, client_ids: set[str]) -> dict[str, str]:
    if not client_ids:
        return {}
    return dict(db.execute(select(ClientSite.id, ClientSite.name).where(ClientSite.client_id.in_(client_ids))).all())


def payload(c: ClientContact, link: Optional[ClientContactLink], sites: dict[str, str], others: Optional[list[str]] = None) -> dict:
    d = {k: getattr(c, k) for k in ("id", "full_name", "position", "department", "work_phone", "work_email", "extra_emails", "messenger",
                                    "consent_on", "consent_source", "comment", "anonymized_at", "created_at", "updated_at")}
    d["emails"] = emails_of(c)
    if link is not None:
        d.update({"link_id": link.id, "client_id": link.client_id, "roles": link.roles or [], "site_id": link.site_id, "is_primary": link.is_primary,
                  "is_active": link.is_active and (link.valid_to is None or link.valid_to >= date.today()), "valid_from": link.valid_from,
                  "valid_to": link.valid_to, "link_comment": link.comment})
        d["roles_titles"] = [CONTACT_ROLES.get(r, r) for r in (link.roles or [])]
        d["site_name"] = sites.get(link.site_id or "", "")
    d["other_clients"] = others or []
    return d


def _history(db: Session, c: ClientContact, before: dict, user_id: str) -> None:
    for f in PERSON_FIELDS:
        old, new = before.get(f), getattr(c, f)
        if (old or "") != (new or ""):
            db.add(ClientContactHistory(contact_id=c.id, user_id=user_id, field=f, old_value=str(old or ""), new_value=str(new or "")))


def _safe(link: ClientContactLink) -> dict:
    """Для общего журнала действий — без персональных данных."""
    return {"client_id": link.client_id, "is_active": link.is_active, "is_primary": link.is_primary, "roles": link.roles}


def _set_primary(db: Session, link: ClientContactLink) -> None:
    for o in db.scalars(select(ClientContactLink).where(ClientContactLink.client_id == link.client_id, ClientContactLink.is_primary.is_(True),
                                                        ClientContactLink.id != (link.id or ""))).all():
        o.is_primary = False


def _link(db: Session, contact_id: str, client_id: str) -> Optional[ClientContactLink]:
    return db.scalars(select(ClientContactLink).where(ClientContactLink.contact_id == contact_id, ClientContactLink.client_id == client_id)
                      .order_by(ClientContactLink.is_active.desc(), ClientContactLink.created_at.desc())).first()


def client_contacts(db: Session, client_id: str, include_archived: bool = False) -> list[tuple[ClientContact, ClientContactLink]]:
    """Контакты клиента по связям — общий источник для карточки клиента, КП и юр. профиля."""
    stmt = (select(ClientContact, ClientContactLink).join(ClientContactLink, ClientContactLink.contact_id == ClientContact.id)
            .where(ClientContactLink.client_id == client_id, ClientContact.anonymized_at.is_(None)))
    if not include_archived:
        stmt = stmt.where(active_link())
    return list(db.execute(stmt.order_by(ClientContactLink.is_primary.desc(), ClientContact.full_name)).all())


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return {"roles": CONTACT_ROLES, "legal_roles": list(LEGAL_ROLES), "consent_required": _consent_required(db),
            "can_edit": user.can("client_contacts", EDIT), "can_see_views": user.can("security_audit", READ)}


@router.get("")
def list_contacts(client_id: str = Query(min_length=1), include_archived: bool = False, role: str = "",
                  db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Клиент не найден")
    rows = client_contacts(db, client_id, include_archived)
    if role:
        wanted = set(LEGAL_ROLES) if role == "legal_any" else {role}
        rows = [(c, l) for c, l in rows if wanted & set(l.roles or [])]
    ids = [c.id for c, _ in rows]
    others: dict[str, list[str]] = {}
    if ids:
        for cid, name in db.execute(select(ClientContactLink.contact_id, Client.name).join(Client, Client.id == ClientContactLink.client_id)
                                    .where(ClientContactLink.contact_id.in_(ids), ClientContactLink.client_id != client_id, active_link())).all():
            others.setdefault(cid, []).append(name)
    db.add(ClientContactView(user_id=user.id, client_id=client_id, kind="list"))
    db.commit()
    sites = _sites(db, {client_id})
    return {"items": [payload(c, l, sites, others.get(c.id)) for c, l in rows]}


@router.post("", status_code=201)
def create_contact(body: ContactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    _check(db, body)
    data = body.model_dump()
    c = ClientContact(created_by=user.id, client_id=body.client_id, roles=body.roles, site_id=body.site_id, is_primary=body.is_primary,
                      **{k: data[k] for k in PERSON_FIELDS})
    db.add(c)
    db.flush()
    link = ClientContactLink(contact_id=c.id, client_id=body.client_id, site_id=body.site_id, roles=body.roles, is_primary=body.is_primary,
                             valid_from=body.valid_from, valid_to=body.valid_to, is_active=body.is_active, created_by=user.id)
    db.add(link)
    db.flush()
    if link.is_primary:
        _set_primary(db, link)
    audit(db, user.id, None, "client_contact", c.id, "create", None, _safe(link))
    db.commit()
    return payload(c, link, _sites(db, {body.client_id}))


class LinkIn(BaseModel):
    client_id: str
    roles: list[str] = Field(default_factory=list)
    site_id: Optional[str] = None
    is_primary: bool = False
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    comment: str = ""


@router.post("/{contact_id}/links", status_code=201)
def add_link(contact_id: str, body: LinkIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Привязать существующего человека к ещё одному клиенту (своя роль, площадка и период)."""
    c = db.get(ClientContact, contact_id)
    if c is None or c.anonymized_at:
        raise HTTPException(404, "Контакт не найден")
    if db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Выберите существующего клиента")
    if any(r not in CONTACT_ROLES for r in body.roles):
        raise HTTPException(422, "Неизвестная роль контакта")
    if body.valid_from and body.valid_to and body.valid_to < body.valid_from:
        raise HTTPException(422, "Дата окончания раньше даты начала")
    if body.site_id:
        site = db.get(ClientSite, body.site_id)
        if site is None or site.client_id != body.client_id:
            raise HTTPException(422, "Площадка не найдена или принадлежит другому клиенту")
    if db.scalars(select(ClientContactLink).where(ClientContactLink.contact_id == c.id, ClientContactLink.client_id == body.client_id, active_link())).first():
        raise HTTPException(409, "Контакт уже привязан к этому клиенту")
    link = ClientContactLink(contact_id=c.id, created_by=user.id, **body.model_dump())
    db.add(link)
    db.flush()
    if link.is_primary:
        _set_primary(db, link)
    audit(db, user.id, None, "client_contact", c.id, "link", None, _safe(link))
    db.commit()
    return payload(c, link, _sites(db, {body.client_id}))


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


def _get(db: Session, contact_id: str) -> ClientContact:
    c = db.get(ClientContact, contact_id)
    if c is None:
        raise HTTPException(404, "Контакт не найден")
    return c


@router.get("/{contact_id}")
def get_contact(contact_id: str, client_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    c = _get(db, contact_id)
    link = _link(db, c.id, client_id or c.client_id)
    db.add(ClientContactView(user_id=user.id, client_id=link.client_id if link else c.client_id, contact_id=c.id, kind="card"))
    db.commit()
    return payload(c, link, _sites(db, {link.client_id} if link else set()))


@router.get("/{contact_id}/history")
def history(contact_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Было / стало по полям контакта — тем, кто правит контакты."""
    c = _get(db, contact_id)
    rows = db.scalars(select(ClientContactHistory).where(ClientContactHistory.contact_id == c.id).order_by(ClientContactHistory.id.desc()).limit(200)).all()
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_({r.user_id for r in rows if r.user_id}))).all()) if rows else {}
    return [{"at": r.at, "user": names.get(r.user_id, ""), "field": r.field, "old": r.old_value, "new": r.new_value} for r in rows]


@router.put("/{contact_id}")
def update_contact(contact_id: str, body: ContactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Правка человека (общая для всех клиентов) и его связи с клиентом body.client_id."""
    c = _get(db, contact_id)
    if c.anonymized_at:
        raise HTTPException(409, "Контакт обезличен по запросу — изменения невозможны")
    link = _link(db, c.id, body.client_id)
    if link is None:
        raise HTTPException(409, "Контакт не привязан к этому клиенту — привяжите его")
    if body.is_active:
        _check(db, body, exclude_contact=c.id)
    before_person = {f: getattr(c, f) for f in PERSON_FIELDS}
    before_link = _safe(link)
    data = body.model_dump()
    for f in PERSON_FIELDS:
        setattr(c, f, data[f])
    for f in ("roles", "site_id", "is_primary", "is_active", "valid_from", "valid_to"):
        setattr(link, f, data[f])
    if c.client_id == link.client_id:   # прежние поля контакта — как у связи «своего» клиента (совместимость до завершения)
        c.roles, c.site_id, c.is_primary, c.is_active = link.roles, link.site_id, link.is_primary, link.is_active
    if link.is_primary:
        _set_primary(db, link)
    _history(db, c, before_person, user.id)
    audit(db, user.id, None, "client_contact", c.id, "update", before_link, _safe(link))
    db.commit()
    return payload(c, link, _sites(db, {link.client_id}))


class DeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{contact_id}/delete")
def anonymize_contact(contact_id: str, body: DeleteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Удаление по запросу субъекта ПДн — обезличивание (решение владельца 26.11): запись и ссылки остаются, ФИО, телефоны,
    почта, мессенджер и история стираются, связи закрываются. Снимки отправленных КП не меняются. В журнале — факт и основание."""
    c = _get(db, contact_id)
    if c.anonymized_at:
        return {"ok": True, "anonymized": True}
    audit(db, user.id, None, "client_contact", c.id, "anonymize", {"client_id": c.client_id}, None, comment=body.reason.strip())
    emails = set(emails_of(c))
    name = c.full_name.lower()
    links = db.scalars(select(ClientContactLink).where(ClientContactLink.contact_id == c.id)).all()
    # та же запись в прежнем списке карточки клиента (clients.contacts) — удаляется, иначе данные остались бы
    for cid in {c.client_id, *[l.client_id for l in links]}:
        cl = db.get(Client, cid)
        if cl and cl.contacts:
            def same(x) -> bool:
                if not isinstance(x, dict):
                    return False
                em = str(x.get("email") or "").strip().lower()
                nm = " ".join(str(x.get("name") or x.get("full_name") or "").split()).lower()
                return bool(em and em in emails) or (not em and nm == name)
            cl.contacts = [x for x in cl.contacts if not same(x)]
    today = date.today()
    for l in links:
        l.is_active, l.is_primary = False, False
        l.valid_to = l.valid_to or today
    db.query(ClientContactHistory).filter(ClientContactHistory.contact_id == c.id).delete()
    c.full_name, c.position, c.department, c.work_phone, c.work_email, c.messenger = "Обезличено по запросу", "", "", "", "", ""
    c.extra_emails, c.comment, c.consent_source, c.consent_on = [], "", "", None
    c.is_active, c.is_primary = False, False
    c.anonymized_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "anonymized": True}
