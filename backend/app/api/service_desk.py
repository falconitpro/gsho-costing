"""Сервисная служба (I-0, D-191): справочники, обращения, заказ-наряды, события и синхронизация.

Права — группы service_orders (обращения и наряды) и service_refs (справочники). Инженер видит только назначенные ему
наряды и меняет их только событиями; назначает, отменяет и решает конфликты руководитель сервиса (решение 5).
Экранов в I-0 нет — они в I-1 (только в новом виде); здесь основа и протокол, одинаковые для веба и приложения без сети.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT, READ
from ..db import get_db
from ..models import (Client, ClientContact, ClientMachine, ClientSite, ServicePermit, ServiceRequest, ServiceSlaLevel,
                      ServiceWorkType, User, WorkOrder, WorkOrderChecklistItem, WorkOrderEvent)
from ..services import change_events
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
def engineers(user: CurrentUser = Depends(_head), db: Session = Depends(get_db)):
    """Кого можно назначить: действующие инженеры и руководители сервиса, с числом открытых нарядов."""
    users = [u for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()
             if {"engineer", "service_head"} & set(u.roles or [])]
    open_ = dict(db.execute(select(WorkOrder.engineer_id, func.count()).where(WorkOrder.status.not_in(wos.FINAL))
                            .group_by(WorkOrder.engineer_id)).all())
    return [{"id": u.id, "name": u.display_name, "is_engineer": "engineer" in (u.roles or []), "open_orders": open_.get(u.id, 0)} for u in users]


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
