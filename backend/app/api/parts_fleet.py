"""Запчасти, шаг 7: парк машин клиентов, наработка, ТО-комплекты, напоминания о ТО и заявка на комплект в один клик."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import (Client, ClientMachine, Deal, EquipmentModel, Factory, MachineHours, MachineReminder, MaintenanceKit, OtherMachineModel, Part,
                      PartApplicability, PartRequest, Position, User)
from ..services import settings_service
from ..services.deal_service import audit, notify
from ..services.parts_fleet import avg_per_day, due_date, hours_now, kit_lines, next_to
from ..services.parts_requests import match_lines, next_number, norm_article
from .common import serialize
from .parts_requests import _payload as request_payload, _recalc

router = APIRouter(prefix="/api/parts/fleet", tags=["parts-fleet"])
EDIT_ROLES = (Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD, Role.ADMIN)
READ_ROLES = (*EDIT_ROLES, Role.FD, Role.LOGISTICIAN, Role.PURCHASING)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к парку машин")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Парк машин ведут менеджеры, сервис, КД и ЗГД")
    return user


def _model_title(db: Session, m: ClientMachine) -> tuple[str, str]:
    if m.equipment_model_id:
        em = db.get(EquipmentModel, m.equipment_model_id)
        f = db.get(Factory, em.factory_id) if em else None
        return (m.manufacturer or (em.trademark if em and em.trademark else (f.short_name or f.name) if f else "")), (m.model or (em.model if em else ""))
    if m.other_model_id:
        om = db.get(OtherMachineModel, m.other_model_id)
        return (m.manufacturer or (om.manufacturer if om else "")), (m.model or (om.model if om else ""))
    return m.manufacturer, m.model


def _kits_for(db: Session, m: ClientMachine) -> list[MaintenanceKit]:
    conds = []
    if m.equipment_model_id:
        conds.append(MaintenanceKit.equipment_model_id == m.equipment_model_id)
    if m.other_model_id:
        conds.append(MaintenanceKit.other_model_id == m.other_model_id)
    if not conds:
        return []
    return db.scalars(select(MaintenanceKit).where(or_(*conds), MaintenanceKit.active.is_(True)).order_by(MaintenanceKit.interval_hours)).all()


def _forecast(db: Session, m: ClientMachine, today: Optional[date] = None) -> dict:
    today = today or date.today()
    rd = [(h.on, h.hours) for h in db.scalars(select(MachineHours).where(MachineHours.machine_id == m.id).order_by(MachineHours.on)).all()]
    per_day = avg_per_day(rd, m.commissioned_on, m.hours_per_day)
    now = hours_now(rd, per_day, today)
    kits = _kits_for(db, m)
    out = {"per_day": per_day, "hours_now": now, "last_reading": {"on": rd[-1][0], "hours": rd[-1][1]} if rd else None, "next": None}
    if now is not None and kits:
        nt = next_to(now, [{"id": k.id, "interval_hours": k.interval_hours, "name": k.name} for k in kits])
        if nt:
            out["next"] = {"to_hours": nt[0], "kits": [{"id": k["id"], "name": k["name"], "interval_hours": k["interval_hours"]} for k in nt[1]],
                           "due_on": due_date(now, nt[0], per_day, today)}
    return out


def _manager_id(db: Session, m: ClientMachine) -> Optional[str]:
    deal = db.get(Deal, m.deal_id) if m.deal_id else None
    cl = db.get(Client, m.client_id)
    return (cl.manager_id if cl and cl.manager_id else None) or (deal.manager_id if deal else None)


def _payload(db: Session, m: ClientMachine, full: bool = False) -> dict:
    d = serialize(m)
    cl = db.get(Client, m.client_id)
    deal = db.get(Deal, m.deal_id) if m.deal_id else None
    d["manufacturer"], d["model"] = _model_title(db, m)
    d["client_name"], d["deal_number"] = cl.name if cl else "", deal.number if deal else ""
    d["forecast"] = _forecast(db, m)
    if full:
        d["readings"] = [serialize(h) for h in db.scalars(select(MachineHours).where(MachineHours.machine_id == m.id).order_by(MachineHours.on.desc())).all()]
        d["kits"] = [serialize(k) for k in _kits_for(db, m)]
        d["reminders"] = [serialize(r) for r in db.scalars(select(MachineReminder).where(MachineReminder.machine_id == m.id)
                                                            .order_by(MachineReminder.to_hours.desc())).all()]
        d["requests"] = [{"id": r.id, "number": r.number, "created_at": r.created_at} for r in
                         db.scalars(select(PartRequest).where(PartRequest.machine_id == m.id).order_by(PartRequest.created_at.desc())).all()]
    return d


# ------------------------------------------------------------------ парк

@router.get("/machines")
def machines(client_id: str = "", q: str = "", due_days: Optional[int] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Парк: фильтр по клиенту и строке поиска; due_days — только машины с ТО в ближайшие N дней."""
    stmt = select(ClientMachine).where(ClientMachine.active.is_(True)).order_by(ClientMachine.created_at.desc())
    if client_id:
        stmt = stmt.where(ClientMachine.client_id == client_id)
    out = []
    for m in db.scalars(stmt).all():
        d = _payload(db, m)
        if q and q.lower() not in " ".join([d["client_name"], d["manufacturer"], d["model"], m.serial, m.site, d["deal_number"]]).lower():
            continue
        nx = d["forecast"]["next"]
        if due_days is not None and not (nx and nx["due_on"] and (nx["due_on"] - date.today()).days <= due_days):
            continue
        out.append(d)
    return out


class MachineIn(BaseModel):
    client_id: str
    equipment_model_id: Optional[str] = None
    other_manufacturer: str = ""          # модель другого производителя — заводится в справочник автоматически
    other_model: str = ""
    machine_type: str = ""
    serial: str = ""
    commissioned_on: Optional[date] = None
    site: str = ""
    hours_per_day: Optional[float] = Field(default=None, ge=0)
    comment: str = ""
    active: bool = True


def _apply(db: Session, m: ClientMachine, body: MachineIn) -> None:
    if db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Выберите клиента")
    m.client_id = body.client_id
    if body.equipment_model_id:
        if db.get(EquipmentModel, body.equipment_model_id) is None:
            raise HTTPException(422, "Модель не найдена в каталоге ГШО")
        m.equipment_model_id, m.other_model_id = body.equipment_model_id, None
    elif body.other_manufacturer.strip() and body.other_model.strip():
        om = db.scalars(select(OtherMachineModel).where(OtherMachineModel.manufacturer.ilike(body.other_manufacturer.strip()),
                                                        OtherMachineModel.model.ilike(body.other_model.strip()))).first()
        if om is None:
            om = OtherMachineModel(manufacturer=body.other_manufacturer.strip(), model=body.other_model.strip(), machine_type=body.machine_type.strip())
            db.add(om)
            db.flush()
        m.other_model_id, m.equipment_model_id = om.id, None
        m.manufacturer, m.model = om.manufacturer, om.model
    elif not m.equipment_model_id and not m.other_model_id:
        raise HTTPException(422, "Укажите модель: из каталога ГШО или производителя и модель")
    for k in ("serial", "commissioned_on", "site", "hours_per_day", "comment", "active"):
        setattr(m, k, getattr(body, k))


@router.post("/machines", status_code=201)
def create_machine(body: MachineIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    m = ClientMachine(created_by=user.id)
    _apply(db, m, body)
    db.add(m)
    db.flush()
    audit(db, user.id, None, "client_machine", m.id, "create", None, {"client": m.client_id, "serial": m.serial})
    db.commit()
    return _payload(db, m, True)


def _m_or_404(db: Session, mid: str) -> ClientMachine:
    m = db.get(ClientMachine, mid)
    if m is None:
        raise HTTPException(404, "Машина не найдена")
    return m


@router.get("/machines/{mid}")
def machine(mid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return _payload(db, _m_or_404(db, mid), True)


def _machine_units(db: Session, m: ClientMachine) -> list:
    from ..models import AcceptanceUnit
    q = select(AcceptanceUnit).where(AcceptanceUnit.machine_id == m.id)
    units = db.scalars(q).all()
    if not units and m.deal_id and m.position_id:
        units = db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.deal_id == m.deal_id, AcceptanceUnit.position_id == m.position_id,
                                                        AcceptanceUnit.unit_no == m.unit_no)).all()
    return units


@router.get("/machines/{mid}/documents")
def machine_documents(mid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Документы машины: приложенные при приёмке (паспорт, руководство, сертификаты, протоколы, акт), фото / видео и документы модели."""
    from ..models import ModelDocument, User
    from ..services import acceptance_service as acs
    m = _m_or_404(db, mid)
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    docs, media = [], []
    for u in _machine_units(db, m):
        for i, f in enumerate(u.files or []):
            row = {"name": f.get("name"), "kind": f.get("kind"), "kind_title": acs.FILE_KINDS.get(f.get("kind"), f.get("kind")), "title": f.get("title") or "",
                   "at": f.get("at"), "by": names.get(f.get("by"), ""), "round_no": f.get("round_no") or 1,
                   "url": f"/api/parts/fleet/machines/{m.id}/files/{u.id}/{i}"}
            (media if f.get("kind") in ("photo", "video") else docs).append(row)
        if (u.act or {}).get("file"):
            docs.append({"name": u.act["file"].get("name"), "kind": "act", "kind_title": "Акт приёмки (скан)", "title": (u.act or {}).get("number") or "",
                         "at": (u.act or {}).get("date"), "by": "", "round_no": u.round_no or 1, "url": f"/api/parts/fleet/machines/{m.id}/files/{u.id}/act"})
    model_docs = []
    if m.equipment_model_id:
        for d in db.scalars(select(ModelDocument).where(ModelDocument.model_id == m.equipment_model_id, ModelDocument.active.is_(True))).all():
            model_docs.append({"name": d.file_name, "kind": d.kind, "kind_title": acs.DOC_KINDS.get(d.kind, d.kind), "title": d.title, "language": d.language,
                               "version": d.version, "at": d.uploaded_at.isoformat() if d.uploaded_at else None, "url": f"/api/catalog/documents/{d.id}"})
    return {"documents": docs, "media": media, "model_documents": model_docs}


@router.get("/machines/{mid}/files/{uid}/{i}")
def machine_file(mid: str, uid: str, i: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    import os
    import tempfile
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from .. import adapters
    m = _m_or_404(db, mid)
    u = next((x for x in _machine_units(db, m) if x.id == uid), None)
    if u is None:
        raise HTTPException(404)
    if i == "act":
        f = (u.act or {}).get("file")
    else:
        k = int(i) if i.isdigit() else -1
        f = (u.files or [])[k] if 0 <= k < len(u.files or []) else None
    if not f:
        raise HTTPException(404)
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(f.get("name") or "")[1])
    os.close(fd)
    adapters.storage().download(f["path"], path)
    return file_response(path, f.get("name") or "file", background=BackgroundTask(os.unlink, path))


@router.put("/machines/{mid}")
def update_machine(mid: str, body: MachineIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    m = _m_or_404(db, mid)
    _apply(db, m, body)
    audit(db, user.id, None, "client_machine", m.id, "update", None, body.model_dump(mode="json"))
    db.commit()
    return _payload(db, m, True)


def sync_from_deals(db: Session, deal_id: Optional[str] = None, user_id: Optional[str] = None) -> int:
    """Машины из закрытых сделок ГШО: по одной на каждую единицу позиции (серийный номер заполняется потом)."""
    stmt = select(Deal).where(Deal.execution_closed_at.is_not(None))
    if deal_id:
        stmt = stmt.where(Deal.id == deal_id)
    n = 0
    for deal in db.scalars(stmt).all():
        for pos in db.scalars(select(Position).where(Position.deal_id == deal.id)).all():
            existing = {m.unit_no: m for m in db.scalars(select(ClientMachine).where(ClientMachine.deal_id == deal.id, ClientMachine.position_id == pos.id)).all()}
            for m in existing.values():      # машина из приёмки на заводе — дата ввода по закрытию сделки
                if not m.commissioned_on and deal.execution_closed_at:
                    m.commissioned_on = deal.execution_closed_at.date() if hasattr(deal.execution_closed_at, "date") else deal.execution_closed_at
            have = set(existing)
            f = db.get(Factory, pos.factory_id)
            for unit in range(1, int(pos.qty or 1) + 1):
                if unit in have:
                    continue
                db.add(ClientMachine(client_id=deal.client_id, deal_id=deal.id, position_id=pos.id, unit_no=unit, equipment_model_id=pos.equipment_model_id,
                                     manufacturer=(f.short_name or f.name) if f else "", model=pos.model, commissioned_on=deal.execution_closed_at,
                                     created_by=user_id, comment="из сделки ГШО — укажите серийный номер"))
                n += 1
    db.flush()
    return n


@router.post("/sync-deals")
def sync_deals(db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Добавить в парк машины закрытых сделок ГШО, которых ещё нет."""
    n = sync_from_deals(db, user_id=user.id)
    db.commit()
    return {"created": n}


class HoursIn(BaseModel):
    on: date
    hours: float = Field(ge=0)
    comment: str = ""


@router.post("/machines/{mid}/hours", status_code=201)
def add_hours(mid: str, body: HoursIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    m = _m_or_404(db, mid)
    if body.on > date.today():
        raise HTTPException(422, "Дата замера не может быть в будущем")
    prev = db.scalars(select(MachineHours).where(MachineHours.machine_id == m.id, MachineHours.on <= body.on).order_by(MachineHours.on.desc())).first()
    if prev and body.hours < prev.hours:
        raise HTTPException(422, f"Наработка меньше прошлого замера ({prev.hours:g} м·ч на {prev.on:%d.%m.%Y})")
    db.add(MachineHours(machine_id=m.id, on=body.on, hours=body.hours, comment=body.comment, entered_by=user.id))
    db.commit()
    return _payload(db, m, True)


# ------------------------------------------------------------------ ТО-комплекты

class KitLineIn(BaseModel):
    article: str
    qty: float = Field(default=1, gt=0)


class KitIn(BaseModel):
    equipment_model_id: Optional[str] = None
    other_model_id: Optional[str] = None
    name: str = Field(min_length=1)
    interval_hours: float = Field(gt=0)
    lines: list[KitLineIn] = Field(default_factory=list)
    active: bool = True


def _kit_lines(db: Session, lines: list[KitLineIn]) -> list[dict]:
    out = []
    for l in lines:
        art = l.article.strip()
        p = db.scalars(select(Part).where(Part.article == art)).first()
        if p is None:
            na = norm_article(art)
            p = next((x for x in db.scalars(select(Part).where(Part.article.ilike(f"%{art[-4:]}%"))).all() if norm_article(x.article) == na), None) if na else None
        out.append({"part_id": p.id if p else None, "article": p.article if p else art, "name": p.name_ru if p else "", "qty": l.qty})
    return out


@router.get("/kits")
def kits(equipment_model_id: str = "", other_model_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(MaintenanceKit).order_by(MaintenanceKit.interval_hours)
    if equipment_model_id:
        stmt = stmt.where(MaintenanceKit.equipment_model_id == equipment_model_id)
    if other_model_id:
        stmt = stmt.where(MaintenanceKit.other_model_id == other_model_id)
    return [serialize(k) for k in db.scalars(stmt).all()]


@router.post("/kits", status_code=201)
def create_kit(body: KitIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    if bool(body.equipment_model_id) == bool(body.other_model_id):
        raise HTTPException(422, "Комплект — к модели каталога ГШО или к модели другого производителя")
    k = MaintenanceKit(equipment_model_id=body.equipment_model_id, other_model_id=body.other_model_id, name=body.name.strip(),
                       interval_hours=body.interval_hours, lines=_kit_lines(db, body.lines), active=body.active)
    db.add(k)
    db.commit()
    return serialize(k)


@router.put("/kits/{kid}")
def update_kit(kid: str, body: KitIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    k = db.get(MaintenanceKit, kid)
    if k is None:
        raise HTTPException(404, "Комплект не найден")
    k.name, k.interval_hours, k.lines, k.active, k.source = body.name.strip(), body.interval_hours, _kit_lines(db, body.lines), body.active, "manual"
    db.commit()
    return serialize(k)


@router.post("/kits/from-deal/{deal_id}")
def kits_from_deal(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """ТО-комплекты из листа «ЗИП на ТО» расчёта сервиса сделки: по позиции — комплект на каждую периодичность."""
    deal = db.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "Сделка не найдена")
    made = 0
    for pos in db.scalars(select(Position).where(Position.deal_id == deal.id)).all():
        if not pos.equipment_model_id:
            continue
        rows = [r for r in ((pos.service_calc or {}).get("spare_parts") or []) if r.get("interval_hours") and (r.get("part_no") or r.get("name"))]
        rows += [{"part_no": z.get("article"), "name": z.get("name"), "interval_hours": z.get("interval_hours"), "qty_per_service": z.get("qty")}
                 for z in pos.zip_items or [] if z.get("kind") == "to" and z.get("interval_hours") and (z.get("article") or z.get("name"))]
        by_int: dict[float, list[KitLineIn]] = {}
        for r in rows:
            by_int.setdefault(float(r["interval_hours"]), []).append(KitLineIn(article=r.get("part_no") or r.get("name"), qty=float(r.get("qty_per_service") or 1)))
        for interval, lines in sorted(by_int.items()):
            k = db.scalars(select(MaintenanceKit).where(MaintenanceKit.equipment_model_id == pos.equipment_model_id,
                                                        MaintenanceKit.interval_hours == interval)).first()
            if k and k.source == "manual":
                continue          # ручной комплект не перезаписываем
            if k is None:
                k = MaintenanceKit(equipment_model_id=pos.equipment_model_id, interval_hours=interval, name=f"ТО-{interval:g}", source="deal_zip")
                db.add(k)
            k.lines = _kit_lines(db, lines)
            made += 1
    db.commit()
    return {"kits": made}


# ------------------------------------------------------------------ подбор по машине, напоминания, заявка на комплект

@router.get("/machines/{mid}/parts")
def machine_parts(mid: str, q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Запчасти, применимые к машине (по модели каталога ГШО или модели другого производителя)."""
    m = _m_or_404(db, mid)
    man, model = _model_title(db, m)
    conds = []
    if m.equipment_model_id:
        conds.append(PartApplicability.equipment_model_id == m.equipment_model_id)
    if model:
        conds.append(PartApplicability.other_model.ilike(model))
    if not conds:
        return []
    rows = db.execute(select(Part, PartApplicability.assembly).join(PartApplicability, PartApplicability.part_id == Part.id).where(or_(*conds))).all()
    out, seen = [], set()
    for p, assembly in rows:
        if p.id in seen or (q and q.lower() not in f"{p.article} {p.name_ru} {assembly}".lower()):
            continue
        seen.add(p.id)
        out.append({"id": p.id, "article": p.article, "name_ru": p.name_ru, "assembly": assembly, "stock_total": sum((s.qty or 0) - (s.reserved or 0) for s in p.stock)})
    return sorted(out, key=lambda x: (x["assembly"], x["article"]))[:300]


def check_reminders(db: Session, today: Optional[date] = None) -> int:
    """Раз в час: машинам с ТО ближе, чем за parts_to_reminder_days дней, — уведомление менеджеру (одно на машину и ТО)."""
    today = today or date.today()
    ahead = int(settings_service.get(db, "parts_to_reminder_days") or 30)
    n = 0
    for m in db.scalars(select(ClientMachine).where(ClientMachine.active.is_(True))).all():
        fc = _forecast(db, m, today)
        nx = fc["next"]
        if not nx or not nx["due_on"] or (nx["due_on"] - today).days > ahead:
            continue
        if db.scalars(select(MachineReminder).where(MachineReminder.machine_id == m.id, MachineReminder.to_hours == nx["to_hours"])).first():
            continue
        mgr = _manager_id(db, m)
        db.add(MachineReminder(machine_id=m.id, to_hours=nx["to_hours"], due_on=nx["due_on"], kit_ids=[k["id"] for k in nx["kits"]], manager_id=mgr))
        if mgr:
            cl = db.get(Client, m.client_id)
            man, model = _model_title(db, m)
            notify(db, None, mgr, "parts_to", None, f"ТО {nx['to_hours']:g} м·ч ≈ {nx['due_on']:%d.%m.%Y}: {cl.name if cl else ''}, {man} {model}"
                                                   f"{f' зав. № {m.serial}' if m.serial else ''}. Предложите комплект ({', '.join(k['name'] for k in nx['kits'])}).")
        n += 1
    db.flush()
    return n


@router.get("/reminders")
def reminders(open_only: bool = True, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(MachineReminder).order_by(MachineReminder.due_on)
    if open_only:
        stmt = stmt.where(MachineReminder.request_id.is_(None), MachineReminder.dismissed.is_(False))
    out = []
    for r in db.scalars(stmt).all():
        m = db.get(ClientMachine, r.machine_id)
        if m is None:
            continue
        if user.has(Role.MANAGER) and not user.has(Role.CD, Role.GD, Role.SERVICE_HEAD) and r.manager_id not in (None, user.id):
            continue
        d = _payload(db, m)
        u = db.get(User, r.manager_id) if r.manager_id else None
        out.append({**serialize(r), "machine": d, "manager_name": u.display_name if u else ""})
    return out


@router.post("/reminders/run")
def run_reminders(db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    n = check_reminders(db)
    db.commit()
    return {"created": n}


@router.post("/reminders/{rid}/dismiss")
def dismiss(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    r = db.get(MachineReminder, rid)
    if r is None:
        raise HTTPException(404, "Напоминание не найдено")
    r.dismissed = True
    db.commit()
    return serialize(r)


class KitRequestIn(BaseModel):
    kit_ids: list[str] = Field(default_factory=list)      # пусто — комплекты ближайшего ТО
    reminder_id: Optional[str] = None


@router.post("/machines/{mid}/kit-request", status_code=201)
def kit_request(mid: str, body: KitRequestIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Заявка клиента на ТО-комплект: строки комплекта подобраны и оценены, дальше — КП как обычно."""
    if not user.has(Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING):
        raise HTTPException(403, "Заявки на запчасти заводят менеджеры, закупки, КД и ЗГД")
    m = _m_or_404(db, mid)
    ids = body.kit_ids
    nx = _forecast(db, m)["next"]
    if not ids:
        if not nx:
            raise HTTPException(422, "Нет ближайшего ТО: внесите наработку и ТО-комплекты модели")
        ids = [k["id"] for k in nx["kits"]]
    ks = [k for k in (db.get(MaintenanceKit, i) for i in ids) if k is not None]
    rows = kit_lines([{"lines": k.lines} for k in ks])
    if not rows:
        raise HTTPException(422, "В комплектах нет строк")
    man, model = _model_title(db, m)
    from ..models import PartRequestLine
    req = PartRequest(number=next_number(db), client_id=m.client_id, manager_id=user.id, channel="email", machine_id=m.id,
                      machine=" ".join(x for x in (man, model, f"зав. № {m.serial}" if m.serial else "") if x),
                      comment=f"ТО-комплект: {', '.join(k.name for k in ks)}" + (f" (ТО {nx['to_hours']:g} м·ч)" if nx and not body.kit_ids else ""))
    for i, r in enumerate(rows, start=1):
        req.lines.append(PartRequestLine(no=i, raw_article=r["article"], raw_name=r["name"], qty=r["qty"], unit="шт"))
    db.add(req)
    db.flush()
    match_lines(db, req)
    _recalc(db, req)
    rem = db.get(MachineReminder, body.reminder_id) if body.reminder_id else None
    if rem is None and nx:
        rem = db.scalars(select(MachineReminder).where(MachineReminder.machine_id == m.id, MachineReminder.to_hours == nx["to_hours"])).first()
    if rem is not None:
        rem.request_id = req.id
    audit(db, user.id, None, "part_request", req.id, "create_from_kit", None, {"machine": m.id, "kits": [k.name for k in ks]})
    db.commit()
    return request_payload(db, req, user)


@router.get("/models")
def models(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Модели для парка: каталог ГШО (производитель → модель) и модели других производителей."""
    facts = {f.id: (f.short_name or f.name) for f in db.scalars(select(Factory)).all()}
    gsho = [{"id": m.id, "title": f"{m.trademark or facts.get(m.factory_id, '')} {m.model}".strip(), "machine_type": m.machine_type}
            for m in db.scalars(select(EquipmentModel).where(EquipmentModel.status != "archived")).all()]
    other = [{"id": o.id, "title": f"{o.manufacturer} {o.model}", "manufacturer": o.manufacturer, "model": o.model, "machine_type": o.machine_type}
             for o in db.scalars(select(OtherMachineModel)).all()]
    return {"gsho": sorted(gsho, key=lambda x: x["title"]), "other": sorted(other, key=lambda x: x["title"])}
