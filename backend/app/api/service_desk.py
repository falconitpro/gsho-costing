"""Сервисная служба (I-0, D-191): справочники, обращения, заказ-наряды, события и синхронизация.

Права — группы service_orders (обращения и наряды) и service_refs (справочники). Инженер видит только назначенные ему
наряды и меняет их только событиями; назначает, отменяет и решает конфликты руководитель сервиса (решение 5).
Экранов в I-0 нет — они в I-1 (только в новом виде); здесь основа и протокол, одинаковые для веба и приложения без сети.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import hashlib
import os
import tempfile
import uuid
from datetime import date, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import (EngineerPermit, WorkOrderPhoto, WorkOrderWork, Client, ClientContact, ClientMachine, ClientSite, ServicePermit, ServiceRequest, ServiceSlaLevel,
                      ServiceWorkType, User, WorkOrder, WorkOrderChecklistItem, WorkOrderEvent)
from .. import adapters
from ..core.clock import msk_date, today as msk_today
from ..core.files import MB, file_response, read_limited, safe_filename
from ..services import change_events
from ..services import engineer_permits as ep
from ..services import work_orders as wos
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/service", tags=["service-desk"])

REFS = {"work_types": ServiceWorkType, "permits": ServicePermit, "sla": ServiceSlaLevel}


def _refs_reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("service_refs", READ):
        raise HTTPException(403, "Нет доступа к справочникам сервиса")
    return user


def _refs_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("service_refs", EDIT) or wos.actor_of(user).engineer_only:
        raise HTTPException(403, "Справочники сервиса ведёт руководитель сервиса")
    return user


def _desk(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Обращения: видят все с доступом к сервису, кроме инженера (ему — только его наряды)."""
    if not user.can("service_orders", READ) or wos.actor_of(user).engineer_only:
        raise HTTPException(403, "Нет доступа к обращениям сервиса")
    return user


def _head(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not wos.actor_of(user).is_head:
        raise HTTPException(403, "Это действие — руководителя сервиса")
    return user


def _orders_reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("service_orders", READ):
        raise HTTPException(403, "Нет доступа к нарядам")
    return user


# ─────────────── справочники ───────────────

@router.get("/refs")
def refs(user: CurrentUser = Depends(_refs_reader), db: Session = Depends(get_db)):
    out: dict[str, Any] = {k: [serialize(x) for x in db.scalars(select(m).order_by(m.code)).all()] for k, m in REFS.items()}
    out["categories"], out["channels"] = wos.CATEGORIES, wos.CHANNELS
    out["statuses"], out["request_statuses"] = wos.STATUSES, wos.REQUEST_STATUSES
    out["drafts"] = sum(1 for k in REFS for x in out[k] if x["is_draft"])
    return out


class RefIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    is_draft: bool = True
    is_active: bool = True
    comment: str = ""
    # виды работ
    category: str = "repair"
    machine_kind: str = Field(default="", max_length=64)
    norm_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    crew_size: int = Field(default=1, ge=1, le=50)
    permit_codes: list[str] = []
    checklist: list[str] = []
    # допуски
    valid_months: Optional[int] = Field(default=None, ge=1, le=600)
    # сроки реакции
    reaction_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    arrival_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    restore_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    calendar: str = "24x7"


_REF_FIELDS = {
    "work_types": ("category", "machine_kind", "norm_hours", "crew_size", "permit_codes", "checklist"),
    "permits": ("valid_months",),
    "sla": ("reaction_hours", "arrival_hours", "restore_hours", "calendar"),
}


def _ref_values(kind: str, body: RefIn, db: Session) -> dict:
    if kind not in REFS:
        raise HTTPException(404, "Нет такого справочника")
    data = {"code": body.code.strip(), "name": body.name.strip(), "is_draft": body.is_draft, "is_active": body.is_active, "comment": body.comment}
    if not data["code"] or not data["name"]:
        raise HTTPException(422, "Нужны код и название")
    for f in _REF_FIELDS[kind]:
        data[f] = getattr(body, f)
    if kind == "work_types":
        if body.category not in wos.CATEGORIES:
            raise HTTPException(422, "Категория — из списка")
        known = set(db.scalars(select(ServicePermit.code)).all())
        if unknown := [c for c in body.permit_codes if c not in known]:
            raise HTTPException(422, f"Нет таких допусков: {', '.join(unknown)}")
        data["checklist"] = [s.strip() for s in body.checklist if s.strip()]
    if kind == "sla" and body.calendar not in ("24x7", "business"):
        raise HTTPException(422, "Календарь — 24x7 или business")
    return data


@router.post("/refs/{kind}", status_code=201)
def ref_create(kind: str, body: RefIn, user: CurrentUser = Depends(_refs_editor), db: Session = Depends(get_db)):
    data = _ref_values(kind, body, db)
    m = REFS[kind]
    if db.scalar(select(m).where(m.code == data["code"])):
        raise HTTPException(409, "Такой код уже есть")
    obj = m(**data)
    db.add(obj)
    db.flush()
    audit(db, user.id, None, f"service_{kind}", obj.id, "create", after=data)
    db.commit()
    return serialize(obj)


@router.put("/refs/{kind}/{ref_id}")
def ref_update(kind: str, ref_id: str, body: RefIn, user: CurrentUser = Depends(_refs_editor), db: Session = Depends(get_db)):
    data = _ref_values(kind, body, db)
    m = REFS[kind]
    obj = db.get(m, ref_id)
    if obj is None:
        raise HTTPException(404, "Запись не найдена")
    if data["code"] != obj.code and db.scalar(select(m).where(m.code == data["code"])):
        raise HTTPException(409, "Такой код уже есть")
    before = serialize(obj)
    for k, v in data.items():
        setattr(obj, k, v)
    audit(db, user.id, None, f"service_{kind}", obj.id, "approve" if before["is_draft"] and not obj.is_draft else "update",
          before=before, after=data)
    db.commit()
    return serialize(obj)


# ─────────────── обращения ───────────────

def _names(db: Session, ids) -> dict[str, str]:
    ids = {i for i in ids if i}
    return {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


def _request_payload(db: Session, r: ServiceRequest) -> dict:
    d = serialize(r)
    c = db.get(Client, r.client_id)
    s = db.get(ClientSite, r.site_id) if r.site_id else None
    m = db.get(ClientMachine, r.machine_id) if r.machine_id else None
    d.update(client_name=c.name if c else "", site_name=s.name if s else "", status_title=wos.REQUEST_STATUSES.get(r.status, r.status),
             machine_title=" ".join(x for x in ((m.manufacturer, m.model, m.serial) if m else ()) if x),
             orders=[{"id": o.id, "number": o.number, "status": o.status}
                     for o in db.scalars(select(WorkOrder).where(WorkOrder.request_id == r.id).order_by(WorkOrder.number)).all()])
    return d


class RequestIn(BaseModel):
    client_id: str
    site_id: Optional[str] = None
    machine_id: Optional[str] = None
    contact_id: Optional[str] = None
    channel: str = "phone"
    kind: str = "repair"
    sla_code: str = ""
    description: str = Field(min_length=1, max_length=20000)
    machine_down: bool = False


def _check_links(db: Session, client_id: str, site_id: Optional[str], machine_id: Optional[str], contact_id: Optional[str] = None) -> None:
    if db.get(Client, client_id) is None:
        raise HTTPException(422, "Нет такого клиента")
    if site_id:
        s = db.get(ClientSite, site_id)
        if s is None or s.client_id != client_id:
            raise HTTPException(422, "Площадка другого клиента")
    if machine_id:
        m = db.get(ClientMachine, machine_id)
        if m is None or m.client_id != client_id:
            raise HTTPException(422, "Машина другого клиента")
        if site_id and m.site_id and m.site_id != site_id:
            raise HTTPException(422, "Машина числится на другой площадке")
    if contact_id and db.get(ClientContact, contact_id) is None:
        raise HTTPException(422, "Нет такого контакта")


@router.get("/requests")
def requests_list(status: str = "", client_id: str = "", q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                  user: CurrentUser = Depends(_desk), db: Session = Depends(get_db)):
    st = select(ServiceRequest)
    if status:
        st = st.where(ServiceRequest.status == status)
    if client_id:
        st = st.where(ServiceRequest.client_id == client_id)
    if q.strip():
        like = f"%{q.strip()}%"
        st = st.where(or_(ServiceRequest.number.ilike(like), ServiceRequest.description.ilike(like)))
    rows = db.scalars(st.order_by(ServiceRequest.received_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_request_payload(db, r) for r in rows]}


@router.post("/requests", status_code=201)
def request_create(body: RequestIn, user: CurrentUser = Depends(_desk), db: Session = Depends(get_db)):
    if not user.can("service_orders", EDIT):
        raise HTTPException(403, "Обращения заводит сервисная служба")
    if body.channel not in wos.CHANNELS or body.kind not in wos.CATEGORIES:
        raise HTTPException(422, "Канал и вид — из списка")
    if body.sla_code and not db.scalar(select(ServiceSlaLevel).where(ServiceSlaLevel.code == body.sla_code)):
        raise HTTPException(422, "Нет такой категории срока реакции")
    _check_links(db, body.client_id, body.site_id, body.machine_id, body.contact_id)
    r = ServiceRequest(number=wos.request_number(db), created_by=user.id, status="new", **body.model_dump())
    db.add(r)
    db.flush()
    audit(db, user.id, None, "service_request", r.id, "create", after={"number": r.number, "client_id": r.client_id})
    db.commit()
    return _request_payload(db, r)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=5000)


@router.post("/requests/{request_id}/reject")
def request_reject(request_id: str, body: RejectIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    r = db.get(ServiceRequest, request_id)
    if r is None:
        raise HTTPException(404, "Обращение не найдено")
    if r.status != "new":
        raise HTTPException(409, "Отклонить можно только новое обращение")
    r.status, r.reject_reason = "rejected", body.reason.strip()
    audit(db, user.id, None, "service_request", r.id, "reject", comment=r.reject_reason)
    db.commit()
    return _request_payload(db, r)


# ─────────────── наряды ───────────────

def _visible(user: CurrentUser):
    st = select(WorkOrder)
    if wos.actor_of(user).engineer_only:
        st = st.where(WorkOrder.engineer_id == user.id)
    return st


def _get_order(db: Session, user: CurrentUser, order_id: str) -> WorkOrder:
    wo = db.get(WorkOrder, order_id)
    if wo is None or (wos.actor_of(user).engineer_only and wo.engineer_id != user.id):
        raise HTTPException(404, "Наряд не найден")
    return wo


def _order_payload(db: Session, wo: WorkOrder, full: bool = False, user: Optional[CurrentUser] = None) -> dict:
    d = serialize(wo)
    c = db.get(Client, wo.client_id)
    s = db.get(ClientSite, wo.site_id) if wo.site_id else None
    m = db.get(ClientMachine, wo.machine_id) if wo.machine_id else None
    wt = db.get(ServiceWorkType, wo.work_type_id) if wo.work_type_id else None
    names = _names(db, [wo.engineer_id, wo.assigned_by])
    d.update(status_title=wos.STATUSES.get(wo.status, wo.status), client_name=c.name if c else "",
             site_name=s.name if s else "", site_map=bool(s and s.latitude is not None),
             machine_title=" ".join(x for x in ((m.manufacturer, m.model, m.serial) if m else ()) if x),
             work_type_name=wt.name if wt else "", norm_hours=wt.norm_hours if wt else None,
             engineer_name=names.get(wo.engineer_id or "", ""), conflicts=len(wos.open_conflicts(db, wo.id)))
    if user is not None:
        d["actions"] = wos.actions(wo, wos.actor_of(user))
    if full:
        # площадка и машина — в объёме, нужном исполнителю (инженер видит их только через свой наряд)
        from .client_sites import ACCESS_MODES, _map_url
        d["site"] = ({"id": s.id, "name": s.name, "address": s.address, "access_mode_title": ACCESS_MODES.get(s.access_mode, ""),
                      "hse_notes": s.hse_notes, "map_url": _map_url(s)} if s else None)
        d["machine"] = ({"id": m.id, "manufacturer": m.manufacturer, "model": m.model, "serial": m.serial,
                         "commissioned_on": m.commissioned_on.isoformat() if m.commissioned_on else None,
                         "warranty_until": m.warranty_until.isoformat() if m.warranty_until else None} if m else None)
        r = db.get(ServiceRequest, wo.request_id) if wo.request_id else None
        d["request"] = ({"id": r.id, "number": r.number, "machine_down": r.machine_down, "sla_code": r.sla_code,
                         "received_at": r.received_at.isoformat() if r.received_at else None} if r else None)
        d["assigned_by_name"] = names.get(wo.assigned_by or "", "")
        works = db.scalars(select(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id).order_by(WorkOrderWork.position)).all()
        photos = db.scalars(select(WorkOrderPhoto).where(WorkOrderPhoto.work_order_id == wo.id).order_by(WorkOrderPhoto.uploaded_at)).all()
        pn = _names(db, [w.performer_id for w in works] + [p.uploaded_by for p in photos])
        d["works"] = [{**serialize(w), "performer_name": pn.get(w.performer_id or "", "")} for w in works]
        d["hours"] = {"norm": sum(w.norm_hours or 0 for w in works) if any(w.norm_hours is not None for w in works) else None,
                      "actual": sum(w.actual_hours or 0 for w in works) if any(w.actual_hours is not None for w in works) else None}
        d["photos"] = [_photo_payload(p, pn) for p in photos]
        req_codes = ep.required(db, wo)
        d["permits"] = {"required": req_codes, "on": ep.work_date(wo).isoformat(),
                        "gaps": ep.check(db, wo.engineer_id, req_codes, ep.work_date(wo)) if wo.engineer_id else []}
        items = db.scalars(select(WorkOrderChecklistItem).where(WorkOrderChecklistItem.work_order_id == wo.id)
                           .order_by(WorkOrderChecklistItem.position)).all()
        evs = db.scalars(select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == wo.id).order_by(WorkOrderEvent.received_at)).all()
        en = _names(db, [e.actor_id for e in evs] + [i.done_by for i in items])
        d["checklist"] = [{**serialize(i), "done_by_name": en.get(i.done_by or "", "")} for i in items]
        d["events"] = [wos.event_payload(e, en) for e in evs]
        open_ids = set(wos.open_conflicts(db, wo.id, only_ids=True))
        d["open_conflicts"] = [x for x in d["events"] if x["id"] in open_ids]
    return d


@router.get("/orders")
def orders_list(status: str = "", engineer_id: str = "", client_id: str = "", limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0), user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    st = _visible(user)
    if status:
        st = st.where(WorkOrder.status == status)
    if engineer_id:
        st = st.where(WorkOrder.engineer_id == engineer_id)
    if client_id:
        st = st.where(WorkOrder.client_id == client_id)
    rows = db.scalars(st.order_by(WorkOrder.created_at.desc()).offset(offset).limit(limit)).all()
    counts = {k: 0 for k in wos.STATUSES}
    sq = _visible(user).subquery()
    for st_, n in db.execute(select(sq.c.status, func.count()).group_by(sq.c.status)).all():
        counts[st_] = n
    return {"items": [_order_payload(db, o, user=user) for o in rows], "statuses": wos.STATUSES, "counts": counts}


class OrderIn(BaseModel):
    client_id: Optional[str] = None
    site_id: Optional[str] = None
    machine_id: Optional[str] = None
    request_id: Optional[str] = None
    work_type_id: Optional[str] = None
    description: str = ""


@router.post("/orders", status_code=201)
def order_create(body: OrderIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    data = body.model_dump()
    if body.request_id:
        r = db.get(ServiceRequest, body.request_id)
        if r is None:
            raise HTTPException(422, "Нет такого обращения")
        if r.status not in ("new", "accepted"):
            raise HTTPException(409, "Обращение уже закрыто или отклонено")
        for k in ("client_id", "site_id", "machine_id"):
            data[k] = data[k] or getattr(r, k)
        data["description"] = data["description"] or r.description
    if not data["client_id"]:
        raise HTTPException(422, "Нужен клиент")
    _check_links(db, data["client_id"], data["site_id"], data["machine_id"])
    if data["work_type_id"] and db.get(ServiceWorkType, data["work_type_id"]) is None:
        raise HTTPException(422, "Нет такого вида работ")
    wo = wos.create_order(db, wos.actor_of(user), **data)
    if body.request_id:
        r = db.get(ServiceRequest, body.request_id)
        if r.status == "new":
            r.status = "accepted"
            audit(db, user.id, None, "service_request", r.id, "accept", after={"order": wo.number})
    db.commit()
    return _order_payload(db, wo, full=True, user=user)


@router.get("/orders/{order_id}")
def order_get(order_id: str, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    return _order_payload(db, _get_order(db, user, order_id), full=True, user=user)


@router.get("/engineers")
def engineers(order_id: str = "", user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    """Кого можно назначить: действующие инженеры и руководители сервиса, с числом открытых нарядов.
    order_id — для каждого ещё и недостающие допуски на дату работ этого наряда."""
    users = [u for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()
             if {"engineer", "service_head"} & set(u.roles or [])]
    open_ = dict(db.execute(select(WorkOrder.engineer_id, func.count()).where(WorkOrder.status.not_in(wos.FINAL))
                            .group_by(WorkOrder.engineer_id)).all())
    wo = db.get(WorkOrder, order_id) if order_id else None
    codes, on = (ep.required(db, wo), ep.work_date(wo)) if wo else ([], msk_today())
    return [{"id": u.id, "name": u.display_name, "is_engineer": "engineer" in (u.roles or []), "open_orders": open_.get(u.id, 0),
             "gaps": ep.check(db, u.id, codes, on) if wo else []} for u in users]


class EventIn(BaseModel):
    id: Optional[str] = Field(default=None, min_length=8, max_length=36)   # задаёт отправитель: повтор не применяется второй раз
    kind: str
    base_version: int = Field(ge=0)
    payload: dict = {}
    device_id: str = Field(default="web", max_length=64)
    offline: bool = False
    occurred_at: Optional[datetime] = None


def _submit(db: Session, user: CurrentUser, wo: WorkOrder, ev: EventIn) -> WorkOrderEvent:
    try:
        return wos.submit(db, wo, wos.actor_of(user), event_id=ev.id, kind=ev.kind, base_version=ev.base_version,
                          payload=ev.payload, device_id=ev.device_id, offline=ev.offline, occurred_at=ev.occurred_at)
    except wos.Forbidden as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/orders/{order_id}/events")
def order_event(order_id: str, body: EventIn, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    wo = _get_order(db, user, order_id)
    ev = _submit(db, user, wo, body)
    db.commit()
    return {"event": wos.event_payload(ev), "order": _order_payload(db, wo, full=True, user=user)}


# ─────────────── синхронизация (протокол — docs/operations/21) ───────────────

class PushItem(EventIn):
    order_id: str


class PushIn(BaseModel):
    events: list[PushItem] = Field(max_length=500)


@router.post("/sync/push")
def sync_push(body: PushIn, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """Очередь событий с устройства: применяются по порядку; каждое — в своей точке сохранения, ошибка одного не мешает другим."""
    out = []
    for item in body.events:
        sp = db.begin_nested()
        try:
            wo = _get_order(db, user, item.order_id)
            ev = _submit(db, user, wo, item)
            sp.commit()
            out.append({"id": ev.id, "order_id": wo.id, "result": ev.result, "reason": ev.reason,
                        "new_version": ev.new_version, "order_version": wo.version, "status": wo.status})
        except HTTPException as e:
            sp.rollback()
            out.append({"id": item.id, "order_id": item.order_id, "result": "error", "reason": str(e.detail), "http": e.status_code})
    db.commit()
    return {"results": out}


@router.get("/sync/pull")
def sync_pull(cursor: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
              user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """Что изменилось после курсора: наряды (в правах пользователя) целиком. Курсор — из реестра change_events."""
    events, nxt = change_events.settled(db, after=cursor, limit=limit, entity="work_order")
    ids = list(dict.fromkeys(e.entity_id for e in events))
    vis = {o.id: o for o in db.scalars(_visible(user).where(WorkOrder.id.in_(ids))).all()} if ids else {}
    return {"cursor": nxt, "orders": [_order_payload(db, vis[i], full=True, user=user) for i in ids if i in vis]}


# ─────────────── I-2 (D-195): фото, допуски сотрудников, план ───────────────

PHOTO_LIMIT = 10 * MB                          # решение владельца 27.11.2026
PHOTO_YEARS = 10
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic"}


def _photo_payload(p: WorkOrderPhoto, names: dict[str, str]) -> dict:
    d = {k: v for k, v in serialize(p).items() if k not in ("storage_path", "sha256")}
    d["uploaded_by_name"] = names.get(p.uploaded_by or "", "")
    d["url"] = f"/api/service/photos/{p.id}"
    d["map_url"] = (f"https://yandex.ru/maps/?pt={p.longitude},{p.latitude}&z=16&l=map"
                    if p.latitude is not None and p.longitude is not None else "")
    return d


def _exif_geo(path: str) -> tuple[Optional[float], Optional[float], Optional[datetime]]:
    """Координаты и время съёмки из EXIF (если телефон их записал). Ошибки чтения — просто «нет данных»."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            ex = im.getexif()
            gps = ex.get_ifd(0x8825) if ex else {}
            taken = None
            raw = (ex.get_ifd(0x8769) or {}).get(0x9003) or ex.get(0x0132)
            if raw:
                taken = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=3)))

            def deg(v, ref):
                d, m, sec = (float(x) for x in v)
                out = d + m / 60 + sec / 3600
                return -out if ref in ("S", "W") else out
            if gps and 2 in gps and 4 in gps:
                return deg(gps[2], gps.get(1, "N")), deg(gps[4], gps.get(3, "E")), taken
            return None, None, taken
    except Exception:
        return None, None, None


@router.post("/orders/{order_id}/photos")
def photo_upload(order_id: str, file: UploadFile = File(...), base_version: int = Form(...), event_id: str = Form(""),
                 caption: str = Form(""), latitude: Optional[float] = Form(None), longitude: Optional[float] = Form(None),
                 accuracy_m: Optional[float] = Form(None), taken_at: Optional[datetime] = Form(None),
                 checklist_item_id: str = Form(""), work_id: str = Form(""), device_id: str = Form("web"), offline: bool = Form(False),
                 user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """Фото к наряду: до 10 МБ, хранится 10 лет, с координатами устройства или из EXIF. Событие photo — как у прочих действий."""
    wo = _get_order(db, user, order_id)
    eid = event_id or str(uuid.uuid4())
    prev = db.get(WorkOrderEvent, eid)
    if prev is not None:                       # повтор из очереди: файл второй раз не сохраняем
        return {"event": wos.event_payload(prev), "order": _order_payload(db, wo, full=True, user=user)}
    act = wos.actor_of(user)
    if not (act.is_head or (wo.engineer_id and wo.engineer_id == user.id)):
        raise HTTPException(403, "Наряд назначен другому инженеру")
    name = safe_filename(file.filename, "photo.jpg")
    ext = os.path.splitext(name)[1].lower()
    if ext not in PHOTO_TYPES:
        raise HTTPException(415, "Фото — JPG, PNG, WEBP или HEIC")
    data = read_limited(file, PHOTO_LIMIT)
    if (latitude is None) != (longitude is None) or (latitude is not None and not (-90 <= latitude <= 90 and -180 <= longitude <= 180)):
        raise HTTPException(422, "Координаты — парой, в допустимых пределах")
    items = {i.id for i in db.scalars(select(WorkOrderChecklistItem).where(WorkOrderChecklistItem.work_order_id == wo.id)).all()}
    works = {w.id for w in db.scalars(select(WorkOrderWork).where(WorkOrderWork.work_order_id == wo.id)).all()}
    if (checklist_item_id and checklist_item_id not in items) or (work_id and work_id not in works):
        raise HTTPException(422, "Пункт чек-листа или работа — из этого наряда")
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        geo = "device" if latitude is not None else ""
        if latitude is None or taken_at is None:
            elat, elon, etaken = _exif_geo(path)
            if latitude is None and elat is not None:
                latitude, longitude, geo = elat, elon, "exif"
            taken_at = taken_at or etaken
        pid = str(uuid.uuid4())
        stored = adapters.storage().upload(path, f"Сервис/Наряды/{wo.number}", "07_fact", f"{pid[:8]} {name}")
    finally:
        if os.path.exists(path):
            os.unlink(path)
    today = msk_today()
    try:
        keep = today.replace(year=today.year + PHOTO_YEARS)
    except ValueError:                          # 29 февраля
        keep = today.replace(year=today.year + PHOTO_YEARS, day=28)
    ph = WorkOrderPhoto(id=pid, work_order_id=wo.id, event_id=eid, storage_path=stored.path, filename=name, content_type=PHOTO_TYPES[ext],
                        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), caption=caption[:2000],
                        checklist_item_id=checklist_item_id or None, work_id=work_id or None, taken_at=taken_at,
                        latitude=latitude, longitude=longitude, accuracy_m=accuracy_m, geo_source=geo, uploaded_by=user.id, retain_until=keep)
    db.add(ph)
    db.flush()
    ev = _submit(db, user, wo, EventIn(id=eid, kind="photo", base_version=base_version, device_id=device_id, offline=offline,
                                       payload={"photo_id": pid, "filename": name, "geo": bool(geo)}))
    db.commit()
    return {"event": wos.event_payload(ev), "order": _order_payload(db, wo, full=True, user=user)}


@router.get("/photos/{photo_id}")
def photo_get(photo_id: str, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    p = db.get(WorkOrderPhoto, photo_id)
    if p is None:
        raise HTTPException(404, "Фото не найдено")
    _get_order(db, user, p.work_order_id)       # те же права, что на наряд (инженер — только свои)
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(p.filename)[1])
    os.close(fd)
    adapters.storage().download(p.storage_path, path)
    return file_response(path, p.filename, background=BackgroundTask(os.unlink, path))


# допуски сотрудников

def _permit_payload(p: EngineerPermit, names: dict[str, str], today: date) -> dict:
    d = serialize(p)
    d["permit_name"] = names.get(p.permit_code, p.permit_code)
    d["state"] = ("expired" if p.valid_until and p.valid_until < today else
                  "expiring" if p.valid_until and p.valid_until <= today + timedelta(days=30) else "ok")
    return d


@router.get("/permits")
def permits_list(user_id: str = "", user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """Допуски сотрудников сервиса. Инженер видит только свои; руководитель сервиса и ЗГД — всех."""
    act = wos.actor_of(user)
    if not act.is_head:
        user_id = user.id
    from ..models import HrEmployee
    # D-206: допуски, заведённые в «Персонале» сотруднику со входом в CRM, — тоже его (владелец — учётная запись карточки)
    emp_user = dict(db.execute(select(HrEmployee.id, HrEmployee.user_id).where(HrEmployee.user_id.is_not(None))).all())
    rows = []
    for r in db.scalars(select(EngineerPermit)).all():
        owner = r.user_id or emp_user.get(r.employee_id)
        if owner and (not user_id or owner == user_id):
            rows.append((owner, r))
    names = {p.code: p.name for p in db.scalars(select(ServicePermit)).all()}
    un = _names(db, [o for o, _ in rows])
    today = msk_today()
    return {"items": sorted([{**_permit_payload(r, names, today), "user_id": o, "user_name": un.get(o, "")} for o, r in rows],
                            key=lambda x: (x["user_name"], x["permit_code"])), "can_edit": act.is_head}


class PermitIn(BaseModel):
    user_id: str
    permit_code: str = Field(min_length=1, max_length=32)
    number: str = Field(default="", max_length=64)
    issued_on: Optional[date] = None
    valid_until: Optional[date] = None
    comment: str = ""


@router.put("/permits")
def permit_save(body: PermitIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    """Завести или продлить допуск. Срок не указан — считается от даты выдачи по сроку из справочника (пусто — бессрочно)."""
    ref = db.scalar(select(ServicePermit).where(ServicePermit.code == body.permit_code))
    u = db.get(User, body.user_id)
    if ref is None:
        raise HTTPException(422, "Нет такого допуска в справочнике")
    if u is None or not u.is_active:
        raise HTTPException(422, "Нет такого сотрудника")
    valid = body.valid_until
    if valid is None and body.issued_on and ref.valid_months:
        m = body.issued_on.month - 1 + ref.valid_months
        y, m = body.issued_on.year + m // 12, m % 12 + 1
        import calendar
        valid = date(y, m, min(body.issued_on.day, calendar.monthrange(y, m)[1]))
    if body.issued_on and valid and valid < body.issued_on:
        raise HTTPException(422, "Срок действия раньше даты выдачи")
    from ..services.engineer_permits import best_by_code, owned
    p = best_by_code([x for x in owned(db, user_id=body.user_id) if x.permit_code == body.permit_code]).get(body.permit_code)   # D-206: и заведённый в «Персонале»
    before = serialize(p) if p else None
    if p is None:
        from ..models import HrEmployee
        emp = db.scalar(select(HrEmployee.id).where(HrEmployee.user_id == body.user_id))
        p = EngineerPermit(user_id=body.user_id, employee_id=emp, permit_code=body.permit_code, created_by=user.id)
        db.add(p)
    p.number, p.issued_on, p.valid_until, p.comment = body.number.strip(), body.issued_on, valid, body.comment
    db.flush()
    audit(db, user.id, None, "engineer_permit", p.id, "update" if before else "create", before=before, after=serialize(p))
    db.commit()
    return _permit_payload(p, {ref.code: ref.name}, msk_today())


@router.delete("/permits/{permit_id}")
def permit_delete(permit_id: str, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    p = db.get(EngineerPermit, permit_id)
    if p is None:
        raise HTTPException(404, "Допуск не найден")
    audit(db, user.id, None, "engineer_permit", p.id, "delete", before=serialize(p))
    db.delete(p)
    db.commit()
    return {"ok": True}


# план работ

@router.get("/plan")
def plan(start: Optional[date] = None, days: int = Query(14, ge=1, le=62), user: CurrentUser = Depends(_desk), db: Session = Depends(get_db)):
    """План по инженерам: открытые наряды со сроками в окне [start, start+days) и без сроков — отдельно."""
    start = start or msk_today()
    end = start + timedelta(days=days)
    rows = db.scalars(select(WorkOrder).where(WorkOrder.status.not_in(wos.FINAL))).all()
    engs = [u for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()
            if {"engineer", "service_head"} & set(u.roles or [])]
    clients = {c.id: c.name for c in db.scalars(select(Client).where(Client.id.in_({r.client_id for r in rows}))).all()} if rows else {}

    def brief(o: WorkOrder) -> dict:
        return {"id": o.id, "number": o.number, "status": o.status, "status_title": wos.STATUSES.get(o.status, o.status),
                "client_name": clients.get(o.client_id, ""), "engineer_id": o.engineer_id,
                "start": msk_date(o.planned_start).isoformat() if o.planned_start else None,
                "end": msk_date(o.planned_end or o.planned_start).isoformat() if o.planned_start else None}
    planned = [brief(o) for o in rows if o.planned_start and msk_date(o.planned_start) < end and msk_date(o.planned_end or o.planned_start) >= start]
    return {"start": start.isoformat(), "days": [(start + timedelta(days=i)).isoformat() for i in range(days)],
            "engineers": [{"id": u.id, "name": u.display_name, "orders": [o for o in planned if o["engineer_id"] == u.id]} for u in engs],
            "unassigned": [o for o in planned if not o["engineer_id"]],
            "unplanned": [brief(o) for o in rows if not o.planned_start]}


@router.get("/orders/{order_id}/parts")
def order_parts(order_id: str, q: str = "", user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """J-b (D-196): запчасти для машины наряда — по применимости, со статусом проверки, без цен (видно исполнителю)."""
    wo = _get_order(db, user, order_id)
    m = db.get(ClientMachine, wo.machine_id) if wo.machine_id else None
    if m is None:
        return {"machine": None, "items": []}
    from ..services.applicability import parts_for_machine
    from .parts_fleet import _model_title
    _man, model = _model_title(db, m)
    return {"machine": {"id": m.id, "serial": m.serial}, "items": parts_for_machine(db, m, model, q, limit=200)}


# ─────────────── I-3 (D-198): смета, документы ───────────────

from ..models import Part as _Part, ServiceDocument, ServiceEstimate, ServiceEstimateLine  # noqa: E402
from ..services import service_estimates as se  # noqa: E402


def _no_engineer(user: CurrentUser = Depends(_orders_reader)) -> CurrentUser:
    if wos.actor_of(user).engineer_only:
        raise HTTPException(403, "Смета с ценами инженеру не показывается")
    return user


def _est_payload(db: Session, est: ServiceEstimate) -> dict:
    lines = se.lines_of(db, est)
    d = serialize(est)
    d["status_title"] = se.STATUSES.get(est.status, est.status)
    d["lines"] = []
    for x in lines:
        row = {**serialize(x), "net": se.line_net(x)}
        if x.part_id and (p := db.get(_Part, x.part_id)) is not None:
            row["catalog_price"], row["catalog_currency"] = p.sale_price, p.sale_currency
        d["lines"].append(row)
    d["totals"] = se.totals(est, lines)
    names = _names(db, [est.decided_by, est.created_by])
    d["decided_by_name"], d["created_by_name"] = names.get(est.decided_by or "", ""), names.get(est.created_by or "", "")
    return d


@router.get("/orders/{order_id}/estimates")
def estimates(order_id: str, user: CurrentUser = Depends(_no_engineer), db: Session = Depends(get_db)):
    wo = _get_order(db, user, order_id)
    rows = db.scalars(select(ServiceEstimate).where(ServiceEstimate.work_order_id == wo.id).order_by(ServiceEstimate.version.desc())).all()
    return {"items": [_est_payload(db, e) for e in rows], "statuses": se.STATUSES, "channels": se.CHANNELS, "kinds": se.KINDS,
            "can_edit": wos.actor_of(user).is_head}


class NewEstimateIn(BaseModel):
    reason: str = ""


def _est_err(fn):
    try:
        return fn()
    except se.EstimateError as e:
        raise HTTPException(422, str(e))


@router.post("/orders/{order_id}/estimates", status_code=201)
def estimate_new(order_id: str, body: NewEstimateIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    wo = _get_order(db, user, order_id)
    est = _est_err(lambda: se.create_version(db, wo, user.id, body.reason))
    audit(db, user.id, None, "service_estimate", est.id, "create", after={"order": wo.number, "version": est.version, "reason": est.reason})
    db.commit()
    return _est_payload(db, est)


class EstLineIn(BaseModel):
    kind: str = "other"
    part_id: Optional[str] = None
    work_id: Optional[str] = None
    title: str = ""
    article: str = ""
    qty: float = 1
    unit: str = ""
    price: Optional[float] = None
    discount_pct: float = 0
    note: str = ""


class EstimateIn(BaseModel):
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    vat_pct: float = Field(default=22, ge=0, le=100)
    hour_rate: Optional[float] = Field(default=None, ge=0)
    note: str = ""
    lines: list[EstLineIn] = Field(default_factory=list, max_length=500)


def _est_or_404(db: Session, user: CurrentUser, eid: str) -> ServiceEstimate:
    est = db.get(ServiceEstimate, eid)
    if est is None:
        raise HTTPException(404, "Смета не найдена")
    _get_order(db, user, est.work_order_id)
    return est


@router.put("/estimates/{eid}")
def estimate_save(eid: str, body: EstimateIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    est = _est_or_404(db, user, eid)
    if est.status != "draft":
        raise HTTPException(422, "Править можно только черновик сметы")
    est.currency, est.vat_pct, est.hour_rate, est.note = body.currency.upper(), body.vat_pct, body.hour_rate, body.note
    _est_err(lambda: se.set_lines(db, est, [x.model_dump() for x in body.lines]))
    db.commit()
    return _est_payload(db, est)


@router.post("/estimates/{eid}/fill-works")
def estimate_fill(eid: str, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    """Добавить в черновик работы наряда, которых в смете ещё нет (часы — факт или норматив, цена — ставка нормо-часа)."""
    est = _est_or_404(db, user, eid)
    if est.status != "draft":
        raise HTTPException(422, "Править можно только черновик сметы")
    cur = se.lines_of(db, est)
    have = {x.work_id for x in cur if x.work_id}
    wo = db.get(WorkOrder, est.work_order_id)
    rows = [{k: getattr(x, k) for k in ("kind", "part_id", "work_id", "title", "article", "qty", "unit", "price", "discount_pct", "note")} for x in cur]
    rows += [r for r in se.lines_from_order(db, wo, est.hour_rate) if r["work_id"] not in have]
    _est_err(lambda: se.set_lines(db, est, rows))
    db.commit()
    return _est_payload(db, est)


class SendIn(BaseModel):
    channel: str


@router.post("/estimates/{eid}/send")
def estimate_send(eid: str, body: SendIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    est = _est_or_404(db, user, eid)
    doc = _est_err(lambda: se.send(db, est, user.id, body.channel))
    audit(db, user.id, None, "service_estimate", est.id, "send", after={"total": est.total, "currency": est.currency, "document": doc.number})
    db.commit()
    return _est_payload(db, est)


class DecideIn(BaseModel):
    decision: str
    client_name: str = ""
    basis: str = ""


@router.post("/estimates/{eid}/decide")
def estimate_decide(eid: str, body: DecideIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    est = _est_or_404(db, user, eid)
    _est_err(lambda: se.decide(db, est, user.id, body.decision, body.client_name, body.basis))
    audit(db, user.id, None, "service_estimate", est.id, body.decision, after={"client_name": est.client_name, "basis": est.decision_basis})
    db.commit()
    return _est_payload(db, est)


def _doc_payload(d: ServiceDocument, names: dict[str, str]) -> dict:
    return {"id": d.id, "kind": d.kind, "kind_title": se.DOC_KINDS.get(d.kind, d.kind), "number": d.number, "filename": d.filename,
            "created_at": d.created_at.isoformat() if d.created_at else None, "created_by_name": names.get(d.created_by or "", ""),
            "legal_checked": d.legal_checked, "url": f"/api/service/documents/{d.id}/file"}


@router.get("/orders/{order_id}/documents")
def documents(order_id: str, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    wo = _get_order(db, user, order_id)
    st = select(ServiceDocument).where(ServiceDocument.work_order_id == wo.id)
    if wos.actor_of(user).engineer_only:
        st = st.where(ServiceDocument.kind != "estimate")           # смету с ценами инженер не видит
    rows = db.scalars(st.order_by(ServiceDocument.created_at.desc())).all()
    names = _names(db, [d.created_by for d in rows])
    return {"items": [_doc_payload(d, names) for d in rows], "kinds": se.DOC_KINDS, "can_make": wos.actor_of(user).is_head}


class DocIn(BaseModel):
    kind: str


@router.post("/orders/{order_id}/documents", status_code=201)
def document_make(order_id: str, body: DocIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    wo = _get_order(db, user, order_id)
    if body.kind == "estimate":
        raise HTTPException(422, "Документ сметы формируется при отправке сметы клиенту")
    d = _est_err(lambda: se.make_document(db, wo, body.kind, user.id))
    audit(db, user.id, None, "service_document", d.id, "create", after={"number": d.number, "kind": d.kind, "order": wo.number})
    db.commit()
    return _doc_payload(d, {user.id: user.display_name})


@router.get("/documents/{doc_id}/file")
def document_file(doc_id: str, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    d = db.get(ServiceDocument, doc_id)
    if d is None:
        raise HTTPException(404, "Документ не найден")
    _get_order(db, user, d.work_order_id)
    if d.kind == "estimate" and wos.actor_of(user).engineer_only:
        raise HTTPException(404, "Документ не найден")
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    adapters.storage().download(d.storage_path, path)
    return file_response(path, d.filename, inline=False, background=BackgroundTask(os.unlink, path))


# ─────────────── J-e (D-201): комплекты ТО в наряде ───────────────

@router.get("/orders/{order_id}/kits")
def order_kits(order_id: str, user: CurrentUser = Depends(_orders_reader), db: Session = Depends(get_db)):
    """Комплекты ТО для машины наряда (модель и диапазон заводских номеров): работы и запчасти, без цен."""
    wo = _get_order(db, user, order_id)
    m = db.get(ClientMachine, wo.machine_id) if wo.machine_id else None
    if m is None:
        return {"items": []}
    from .parts_fleet import kits_for_machine
    return {"items": [{"id": k.id, "name": k.name, "interval_hours": k.interval_hours, "works": k.works or [],
                       "lines": [{kk: ln.get(kk) for kk in ("part_id", "article", "name", "qty", "assembly")} for ln in (k.lines or [])],
                       "serial_range": " — ".join(x for x in (k.serial_from, k.serial_to) if x)} for k in kits_for_machine(db, m)]}


class AddKitIn(BaseModel):
    kit_id: str


@router.post("/estimates/{eid}/add-kit")
def estimate_add_kit(eid: str, body: AddKitIn, user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    """Запчасти комплекта ТО — в черновик сметы (цена из каталога, только если валюта совпадает; иначе «не задана»)."""
    from ..models import MaintenanceKit
    est = _est_or_404(db, user, eid)
    if est.status != "draft":
        raise HTTPException(422, "Править можно только черновик сметы")
    k = db.get(MaintenanceKit, body.kit_id)
    if k is None:
        raise HTTPException(404, "Комплект не найден")
    cur = se.lines_of(db, est)
    rows = [{kk: getattr(x, kk) for kk in ("kind", "part_id", "work_id", "title", "article", "qty", "unit", "price", "discount_pct", "note")} for x in cur]
    for ln in k.lines or []:
        p = db.get(_Part, ln["part_id"]) if ln.get("part_id") else None
        price = p.sale_price if p is not None and p.sale_price is not None and (p.sale_currency or "") == est.currency else None
        rows.append({"kind": "part" if p else "other", "part_id": p.id if p else None, "title": p.name_ru if p else (ln.get("name") or ln.get("article") or "запчасть"),
                     "article": ln.get("article") or "", "qty": ln.get("qty") or 1, "price": price, "note": f"комплект {k.name}" + ("" if p else " · нет карточки в каталоге")})
    _est_err(lambda: se.set_lines(db, est, rows))
    db.commit()
    return _est_payload(db, est)
