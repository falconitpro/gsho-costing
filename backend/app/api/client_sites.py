"""Площадки клиентов (D-182, K-2). Основа — пакет «Sprint 01 Patch K Sites v1», доработано:
права — через группу доступа «Клиенты: площадки» (client_sites); тип площадки и режим допуска — из списков;
площадку с машинами нельзя перенести к другому клиенту; контактов в площадке нет (персональные данные — K-1)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ, Role
from ..db import get_db
from ..models import Client, ClientMachine, ClientSite
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/clients/sites", tags=["client-sites"])

SITE_TYPES = {"quarry": "Карьер", "mine": "Рудник / шахта", "plant": "Фабрика / завод", "repair_base": "Ремонтная база",
              "warehouse": "Склад", "office": "Офис", "other": "Другое"}
ACCESS_MODES = {"": "Не указан", "free": "Свободный", "pass": "По пропуску", "escort": "С сопровождением", "permit": "По наряду-допуску"}


def _engineer_only(user: CurrentUser) -> bool:
    return bool(user.roles) and all(r == Role.ENGINEER for r in user.roles)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    # инженер видит площадки только по своим нарядам — наряды появятся в I-1, до тех пор доступа нет
    if not user.can("client_sites", READ) or _engineer_only(user):
        raise HTTPException(403, "Нет доступа к площадкам клиентов")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("client_sites", EDIT) or _engineer_only(user):
        raise HTTPException(403, "Площадки клиентов ведут менеджеры, сервис, КД и ЗГД")
    return user


class SiteIn(BaseModel):
    client_id: str
    name: str = Field(min_length=1, max_length=255)
    site_type: str = Field(default="other", max_length=32)
    code: str = Field(default="", max_length=64)
    address: str = ""
    country: str = Field(default="", max_length=2)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    timezone: str = Field(default="", max_length=64)
    access_mode: str = Field(default="", max_length=32)
    hse_notes: str = ""
    is_active: bool = True
    comment: str = ""

    @model_validator(mode="after")
    def _check(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Укажите название площадки")
        if self.site_type not in SITE_TYPES:
            raise ValueError("Неизвестный тип площадки")
        if self.access_mode not in ACCESS_MODES:
            raise ValueError("Неизвестный режим допуска")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Широту и долготу заполните вместе")
        return self


def _get_site(db: Session, site_id: str) -> ClientSite:
    site = db.get(ClientSite, site_id)
    if site is None:
        raise HTTPException(404, "Площадка не найдена")
    return site


def _machines_count(db: Session, site_id: str) -> int:
    return db.scalar(select(func.count()).select_from(ClientMachine).where(ClientMachine.site_id == site_id)) or 0


def _payload(site: ClientSite, client_name: str, machines: int) -> dict:
    d = serialize(site)
    d["client_name"] = client_name
    d["site_type_title"] = SITE_TYPES.get(site.site_type, site.site_type)
    d["access_mode_title"] = ACCESS_MODES.get(site.access_mode, site.access_mode)
    d["machines_count"] = machines
    d["map_url"] = (f"https://yandex.ru/maps/?pt={site.longitude},{site.latitude}&z=14&l=map"
                    if site.latitude is not None and site.longitude is not None else "")
    return d


def _duplicate(db: Session, client_id: str, name: str, exclude: str = "") -> Optional[ClientSite]:
    stmt = select(ClientSite).where(ClientSite.client_id == client_id, ClientSite.is_active.is_(True),
                                    func.lower(func.trim(ClientSite.name)) == name.strip().lower())
    if exclude:
        stmt = stmt.where(ClientSite.id != exclude)
    return db.scalars(stmt).first()


@router.get("/meta")
def meta(user: CurrentUser = Depends(_reader)):
    return {"site_types": SITE_TYPES, "access_modes": ACCESS_MODES, "can_edit": user.can("client_sites", EDIT)}


@router.get("")
def list_sites(client_id: str = "", q: str = "", include_archived: bool = False, limit: int = 200, offset: int = 0,
               db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(ClientSite, Client.name).join(Client, Client.id == ClientSite.client_id)
    if client_id:
        stmt = stmt.where(ClientSite.client_id == client_id)
    if not include_archived:
        stmt = stmt.where(ClientSite.is_active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(ClientSite.name.ilike(like), ClientSite.code.ilike(like), ClientSite.address.ilike(like), Client.name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Client.name, ClientSite.name).limit(max(1, min(limit, 500))).offset(max(0, offset))).all()
    ids = [s.id for s, _ in rows]
    counts = dict(db.execute(select(ClientMachine.site_id, func.count()).where(ClientMachine.site_id.in_(ids)).group_by(ClientMachine.site_id)).all()) if ids else {}
    return {"total": total, "items": [_payload(s, name, counts.get(s.id, 0)) for s, name in rows]}


@router.post("", status_code=201)
def create_site(body: SiteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(422, "Выберите существующего клиента")
    if _duplicate(db, body.client_id, body.name):
        raise HTTPException(409, "У клиента уже есть площадка с таким названием")
    site = ClientSite(**body.model_dump(), created_by=user.id)
    db.add(site)
    db.flush()
    audit(db, user.id, None, "client_site", site.id, "create", None, {"client_id": site.client_id, "name": site.name})
    db.commit()
    return _payload(site, client.name, 0)


@router.get("/{site_id}")
def get_site(site_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    site = _get_site(db, site_id)
    client = db.get(Client, site.client_id)
    return _payload(site, client.name if client else "", _machines_count(db, site.id))


@router.put("/{site_id}")
def update_site(site_id: str, body: SiteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    site = _get_site(db, site_id)
    client = db.get(Client, body.client_id)
    if client is None:
        raise HTTPException(422, "Выберите существующего клиента")
    machines = _machines_count(db, site.id)
    if body.client_id != site.client_id and machines:
        raise HTTPException(409, f"К площадке привязано машин: {machines}. Перенести площадку к другому клиенту нельзя — сначала перенесите машины")
    if body.is_active and _duplicate(db, body.client_id, body.name, exclude=site.id):
        raise HTTPException(409, "У клиента уже есть площадка с таким названием")
    before = {"client_id": site.client_id, "name": site.name, "is_active": site.is_active}
    for key, value in body.model_dump().items():
        setattr(site, key, value)
    audit(db, user.id, None, "client_site", site.id, "update", before,
          {"client_id": site.client_id, "name": site.name, "is_active": site.is_active})
    db.commit()
    return _payload(site, client.name, machines)
