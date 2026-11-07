"""Приёмка машин на заводе по сделке: по каждой единице позиции — номера, чек-лист, испытания, фото и видео, замечания, решение, акт.

Принятая машина (в том числе с замечаниями) с заводским номером попадает в парк клиента — дальше эксплуатация и сервис.
Когда приняты все машины позиции, этап «Приёмка на заводе» отмечается сам; окончательный расчёт с заводом — только после этого.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import AcceptanceUnit, Client, ClientMachine, Deal, ExecutionStage, Factory, ModelDocument, Position, User
from ..services import acceptance_service as acs
from ..services.deal_service import audit, notify
from .common import get_deal_or_404, serialize
from .model_docs import model_docs, position_model

router = APIRouter(prefix="/api/deals/{deal_id}/acceptance", tags=["acceptance"])
EDIT_ROLES = (Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD)


def _editor(user: CurrentUser) -> None:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Приёмку ведут менеджер, сервис, КД и ЗГД")


def _unit_payload(db: Session, u: AcceptanceUnit, pos: Optional[Position] = None) -> dict:
    d = serialize(u)
    pos = pos or db.get(Position, u.position_id)
    d["designation"] = f"{pos.machine_type} {pos.trademark + ' ' if pos.trademark else ''}{pos.model}".strip() if pos else ""
    d["decision_title"] = acs.DECISIONS.get(u.decision, "не принято")
    d["open_remarks"] = len(acs.open_remarks(u.remarks or []))
    d["checked"] = sum(1 for x in u.checklist or [] if x.get("result")) + sum(1 for x in u.tests or [] if x.get("result"))
    d["to_check"] = len(u.checklist or []) + len(u.tests or [])
    d["files"] = [{"i": i, "name": f.get("name"), "kind": f.get("kind"), "kind_title": acs.FILE_KINDS.get(f.get("kind"), f.get("kind")), "title": f.get("title") or "",
                   "item_id": f.get("item_id"), "at": f.get("at"), "round_no": f.get("round_no") or 1} for i, f in enumerate(u.files or [])]
    act = dict(u.act or {})
    if act.get("file"):
        act["file"] = {"name": act["file"].get("name")}
    d["act"] = act
    u_ = db.get(User, u.decided_by) if u.decided_by else None
    d["decided_by_name"] = u_.display_name if u_ else ""
    d["mode_title"] = acs.MODES.get(u.mode or "factory", u.mode)
    d["prev_fail_count"] = sum(1 for x in [*(u.checklist or []), *(u.tests or [])] if x.get("prev_fail"))
    names = {x.id: x.display_name for x in db.scalars(select(User)).all()} if u.rounds else {}
    hist = []
    for r in reversed(u.rounds or []):
        fails = [x.get("item") for x in r.get("checklist") or [] if x.get("result") == "fail"] + [x.get("name") for x in r.get("tests") or [] if x.get("result") == "fail"]
        hist.append({**{k: v for k, v in r.items() if k != "act"}, "act_number": (r.get("act") or {}).get("number") or "",
                     "mode_title": acs.MODES.get(r.get("mode") or "factory", r.get("mode")), "decision_title": acs.DECISIONS.get(r.get("decision") or "", "без решения"),
                     "decided_by_name": names.get(r.get("decided_by"), ""), "fails": fails,
                     "open_remarks": len(acs.open_remarks(r.get("remarks") or []))})
    d["history"] = hist
    d.pop("rounds", None)
    return d


def _init_units(db: Session, deal: Deal) -> int:
    """Карточки приёмки на каждую машину каждой позиции (кол-во позиции); чек-лист — по модели и сделке."""
    n = 0
    for pos in db.scalars(select(Position).where(Position.deal_id == deal.id)).all():
        have = {u.unit_no for u in db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.position_id == pos.id)).all()}
        need = [k for k in range(1, int(pos.qty or 1) + 1) if k not in have]
        if not need:
            continue
        m = position_model(db, pos)
        kinds = [d.kind for d in model_docs(db, m.id)] if m else []
        rows = [{"name_ru": r.name_ru, "unit": r.unit, "value_ru": r.value_ru, "customer_requirement_ru": r.customer_requirement_ru, "required": r.required,
                 "local_ru": r.local_ru} for r in pos.tech_param_rows or []]
        for k in need:
            db.add(AcceptanceUnit(deal_id=deal.id, position_id=pos.id, unit_no=k,
                                  checklist=acs.build_checklist(m.acceptance_checklist if m else [], rows, pos.zip_items or [], kinds),
                                  tests=acs.default_tests(), components=[{"name": "Двигатель", "serial": ""}, {"name": "Трансмиссия", "serial": ""},
                                                                          {"name": "Мосты", "serial": ""}, {"name": "Гидронасос", "serial": ""}]))
            n += 1
    db.flush()
    return n


@router.get("")
def units(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    rows = db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.deal_id == deal.id).order_by(AcceptanceUnit.position_id, AcceptanceUnit.unit_no)).all()
    positions = {p.id: p for p in db.scalars(select(Position).where(Position.deal_id == deal.id)).all()}
    total = sum(int(p.qty or 1) for p in positions.values())
    return {"units": [_unit_payload(db, u, positions.get(u.position_id)) for u in rows], "machines_total": total,
            "can_edit": user.has(*EDIT_ROLES), "decisions": acs.DECISIONS, "results": acs.RESULTS, "file_kinds": acs.FILE_KINDS,
            "modes": acs.MODES, "to_model_kinds": list(acs.TO_MODEL_KINDS)}


@router.post("/init")
def init(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Создать карточки приёмки для машин сделки (уже созданные не меняются)."""
    _editor(user)
    deal = get_deal_or_404(db, deal_id, user)
    n = _init_units(db, deal)
    db.commit()
    return {"created": n, **units(deal_id, db, user)}


def _unit(db: Session, deal_id: str, uid: str, user: CurrentUser) -> AcceptanceUnit:
    deal = get_deal_or_404(db, deal_id, user)
    u = db.get(AcceptanceUnit, uid)
    if u is None or u.deal_id != deal.id:
        raise HTTPException(404, "Машина не найдена")
    return u


@router.get("/{uid}")
def unit(deal_id: str, uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return _unit_payload(db, _unit(db, deal_id, uid, user))


class UnitIn(BaseModel):
    serial: Optional[str] = None
    engine_no: Optional[str] = None
    frame_no: Optional[str] = None
    components: Optional[list[dict]] = None
    checklist: Optional[list[dict]] = None
    tests: Optional[list[dict]] = None
    remarks: Optional[list[dict]] = None
    inspector: Optional[str] = None
    factory_rep: Optional[str] = None
    place: Optional[str] = None
    inspected_on: Optional[date] = None
    comment: Optional[str] = None
    mode: Optional[str] = None          # online | factory — до решения по приёмке
    is_final: Optional[bool] = None     # онлайн-приёмка окончательная


def _clean(rows: list[dict], key: str) -> list[dict]:
    out = []
    for r in rows:
        if not str(r.get(key) or "").strip():
            continue
        out.append({**r, "id": r.get("id") or uuid.uuid4().hex[:12]})
    return out


@router.put("/{uid}")
def update(deal_id: str, uid: str, body: UnitIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    data = body.model_dump(exclude_unset=True)
    if ("mode" in data or "is_final" in data) and u.decision:
        raise HTTPException(409, "Решение по этой приёмке уже принято — для новой приёмки нажмите «Новая приёмка»")
    if data.get("mode") is not None:
        if data["mode"] not in acs.MODES:
            raise HTTPException(422, "Вид приёмки: онлайн или на заводе")
        if data["mode"] == "factory":
            data["is_final"] = True
        elif "is_final" not in data:
            data["is_final"] = False
    if u.decision in ("accepted", "accepted_remarks") and any(k in data for k in ("serial", "checklist", "tests")) and not user.has(Role.CD, Role.GD):
        raise HTTPException(409, "Машина уже принята — номера и результаты проверок меняют КД или ЗГД")
    for k in ("checklist", "tests", "remarks", "components"):
        if k in data and data[k] is not None:
            data[k] = _clean(data[k], {"checklist": "item", "tests": "name", "remarks": "text", "components": "name"}[k])
    for k, v in data.items():
        if v is not None:
            setattr(u, k, v.strip() if isinstance(v, str) else v)
    if u.machine_id and u.serial:
        m = db.get(ClientMachine, u.machine_id)
        if m and m.serial != u.serial:
            m.serial = u.serial
    db.commit()
    return _unit_payload(db, u)


@router.post("/{uid}/files", status_code=201)
def add_file(deal_id: str, uid: str, file: UploadFile = File(...), kind: str = Form("photo"), title: str = Form(""), language: str = Form("ru"),
             to_model: bool = Form(False), item_id: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Фото, видео испытаний, документы машины (паспорт, руководство, сертификат, протоколы…) — прямо во время приёмки.
    Документы машины видны в карточке машины в парке; руководство / каталог / сертификат можно сделать общими для модели."""
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    deal = db.get(Deal, u.deal_id)
    name = os.path.basename(file.filename or "file").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        stored = adapters.storage().upload(path, deal.number if hasattr(deal, "number") else deal.id, "07_fact",
                                           f"Приёмка {u.unit_no} {len(u.files or []) + 1:02d} {name}")
    finally:
        os.unlink(path)
    size = int(getattr(stored, "size", 0) or 0)
    kind = kind if kind in acs.FILE_KINDS else "doc"
    u.files = [*(u.files or []), {"path": stored.path, "name": name, "kind": kind, "title": title.strip(),
                                   "item_id": item_id.strip() or None,
                                   "at": datetime.now(timezone.utc).isoformat(), "by": user.id, "round_no": u.round_no or 1}]
    if to_model and kind in acs.TO_MODEL_KINDS:
        pos = db.get(Position, u.position_id)
        m = position_model(db, pos, create=True, user_id=user.id) if pos else None
        if m is not None:
            db.add(ModelDocument(model_id=m.id, kind=acs.TO_MODEL_KINDS[kind], title=title.strip() or acs.FILE_KINDS[kind], language=language or "ru",
                                 file_name=name, storage_path=stored.path, size=size, source_deal_id=u.deal_id, comment=f"из приёмки машины № {u.unit_no}",
                                 uploaded_by=user.id))
    db.commit()
    return _unit_payload(db, u)


@router.get("/{uid}/files/{i}")
def get_file(deal_id: str, uid: str, i: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    u = _unit(db, deal_id, uid, user)
    if i < 0 or i >= len(u.files or []):
        raise HTTPException(404)
    f = u.files[i]
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(f.get("name") or "")[1])
    os.close(fd)
    adapters.storage().download(f["path"], path)
    return FileResponse(path, filename=f.get("name") or "file", content_disposition_type="inline", background=BackgroundTask(os.unlink, path))


class DecisionIn(BaseModel):
    decision: str
    comment: str = ""


def _to_park(db: Session, u: AcceptanceUnit, pos: Position, user: CurrentUser) -> None:
    """Принятая машина с заводским номером — в парк клиента (эксплуатация, сервис)."""
    deal = db.get(Deal, u.deal_id)
    m = db.get(ClientMachine, u.machine_id) if u.machine_id else db.scalars(select(ClientMachine).where(
        ClientMachine.deal_id == deal.id, ClientMachine.position_id == pos.id, ClientMachine.unit_no == u.unit_no)).first()
    if m is None:
        f = db.get(Factory, pos.factory_id)
        m = ClientMachine(client_id=deal.client_id, deal_id=deal.id, position_id=pos.id, unit_no=u.unit_no, equipment_model_id=pos.equipment_model_id,
                          manufacturer=pos.trademark or ((f.short_name or f.name) if f else ""), model=pos.model, created_by=user.id)
        db.add(m)
    m.serial = u.serial
    m.comment = f"принята на заводе {date.today():%d.%m.%Y}" + ("" if not m.comment or m.comment.startswith("из сделки") else f"; {m.comment}")
    db.flush()
    u.machine_id = m.id


def _mark_stage(db: Session, deal: Deal, pos: Position, user: CurrentUser) -> bool:
    """Все машины позиции приняты — этап «Приёмка на заводе» выполнен."""
    units = db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.position_id == pos.id)).all()
    full = len(units) >= int(pos.qty or 1)
    done = full and all(x.decision in ("accepted", "accepted_remarks") and x.is_final is not False for x in units)
    pre = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id, ExecutionStage.code == "factory_preacceptance",
                                                  ExecutionStage.position_id == pos.id)).first()
    if pre is not None:   # предварительная (онлайн) приёмка — когда все машины приняты онлайн
        from ..services import execution_service as ex_
        on = [acs.online_accepted_on(serialize(x)) for x in units]
        pre_on = date.fromisoformat(max(on)[:10]) if full and on and all(on) else None
        if pre_on != pre.actual_on:
            pre.actual_on, pre.done_by = pre_on, (user.id if pre_on else None)
            ex_.push_history(pre, "actual", pre_on, "онлайн-приёмка всех машин позиции" if pre_on else "онлайн-приёмка не завершена", user.id)
    st = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id, ExecutionStage.code == "factory_acceptance",
                                                 ExecutionStage.position_id == pos.id)).first()
    if st is not None:
        from ..services import execution_service as ex
        acc_on = max((x.inspected_on for x in units if x.inspected_on), default=None) or date.today()
        was = st.actual_on
        st.actual_on = acc_on if done else None
        st.done_by = user.id if done else None
        if done and was != acc_on:
            ex.push_history(st, "actual", acc_on, "все машины позиции приняты", user.id)
        elif not done:
            # не принята / замечания со сроком устранения → ожидаемая дата окончательной приёмки (сдвигает готовность к отгрузке)
            dues = [date.fromisoformat(str(r["due"])[:10]) for x in units for r in acs.open_remarks(x.remarks or []) if r.get("due")]
            exp = max(dues, default=None)
            if exp and (st.refined_on is None or exp > st.refined_on):
                st.refined_on, st.refined_note = exp, "устранение замечаний приёмки"
                ex.push_history(st, "acceptance", exp, "устранение замечаний приёмки — повторная / окончательная приёмка", user.id)
        if deal.status.value == "execution":
            db.flush()
            ex.rebuild_stages(db, deal)
    return done


@router.post("/{uid}/decision")
def decide(deal_id: str, uid: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    err = acs.decision_error(body.decision, serial=u.serial or "", checklist=u.checklist or [], tests=u.tests or [], remarks=u.remarks or [], comment=body.comment)
    if err:
        raise HTTPException(422, err)
    deal = db.get(Deal, u.deal_id)
    pos = db.get(Position, u.position_id)
    u.decision, u.decided_at, u.decided_by = body.decision, datetime.now(timezone.utc), user.id
    if body.comment:
        u.comment = body.comment
    if body.decision in ("accepted", "accepted_remarks"):
        _to_park(db, u, pos, user)
    all_done = _mark_stage(db, deal, pos, user)
    audit(db, user.id, deal.id, "acceptance_unit", u.id, "decision", None, {"decision": body.decision, "serial": u.serial, "comment": body.comment})
    if deal.manager_id and deal.manager_id != user.id:
        notify(db, deal.id, deal.manager_id, "acceptance", None,
               f"Приёмка на заводе: {pos.model} № {u.unit_no} ({u.serial or 'без номера'}) — {acs.DECISIONS[body.decision]}"
               + (". Все машины позиции приняты — можно окончательный расчёт с заводом" if all_done else ""))
    db.commit()
    return _unit_payload(db, u, pos)


class ActIn(BaseModel):
    number: str = ""
    on: Optional[date] = None
    signed_factory_on: Optional[date] = None
    signed_ours_on: Optional[date] = None


@router.put("/{uid}/act")
def act(deal_id: str, uid: str, body: ActIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    d = body.model_dump(mode="json")
    d["date"] = d.pop("on")
    u.act = {**(u.act or {}), **d}
    db.commit()
    return _unit_payload(db, u)


@router.post("/{uid}/act/file", status_code=201)
def act_file(deal_id: str, uid: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подписанный обеими сторонами акт (скан)."""
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    deal = db.get(Deal, u.deal_id)
    name = os.path.basename(file.filename or "act.pdf").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        stored = adapters.storage().upload(path, deal.number, "07_fact", f"Акт приёмки {u.unit_no} {name}")
    finally:
        os.unlink(path)
    u.act = {**(u.act or {}), "file": {"path": stored.path, "name": name}}
    db.commit()
    return _unit_payload(db, u)


@router.get("/{uid}/act/file")
def act_file_get(deal_id: str, uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    u = _unit(db, deal_id, uid, user)
    f = (u.act or {}).get("file")
    if not f:
        raise HTTPException(404, "Скан акта не загружен")
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(f.get("name") or "")[1])
    os.close(fd)
    adapters.storage().download(f["path"], path)
    return FileResponse(path, filename=f.get("name") or "act", background=BackgroundTask(os.unlink, path))


@router.get("/{uid}/act.docx")
def act_docx(deal_id: str, uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Акт приёмки (Word) для подписания."""
    u = _unit(db, deal_id, uid, user)
    deal = db.get(Deal, u.deal_id)
    pos = db.get(Position, u.position_id)
    cl = db.get(Client, deal.client_id)
    f = db.get(Factory, pos.factory_id)
    path = os.path.join(tempfile.mkdtemp(), f"Акт приёмки {deal.number} {u.unit_no}.docx")
    acs.render_act(path, deal_number=deal.number, client=cl.name if cl else "", factory=f.name if f else "",
                   model=_unit_payload(db, u, pos)["designation"], unit=serialize(u))
    return FileResponse(path, filename=os.path.basename(path), background=BackgroundTask(os.unlink, path))


def position_accepted(db: Session, position_id: str) -> bool:
    pos = db.get(Position, position_id)
    units = db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.position_id == position_id)).all()
    return bool(pos) and len(units) >= int(pos.qty or 1) and all(x.decision in ("accepted", "accepted_remarks") and x.is_final is not False for x in units)


class RoundIn(BaseModel):
    mode: str = "factory"
    is_final: Optional[bool] = None


@router.post("/{uid}/rounds")
def new_round(deal_id: str, uid: str, body: RoundIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Новая (повторная) приёмка машины: онлайн или на заводе. Текущая уходит в историю целиком; пункты, не принятые
    в предыдущих приёмках, отмечены; неустранённые замечания переносятся."""
    _editor(user)
    u = _unit(db, deal_id, uid, user)
    if u.decision == "accepted" and u.is_final is not False and not user.has(Role.CD, Role.GD):
        raise HTTPException(409, "Машина окончательно принята — повторную приёмку начинают КД или ЗГД")
    try:
        nr = acs.new_round(serialize(u), body.mode, body.is_final)
    except ValueError as e:
        raise HTTPException(422, str(e))
    for k, v in nr.items():
        setattr(u, k, v)
    deal = db.get(Deal, u.deal_id)
    pos = db.get(Position, u.position_id)
    db.flush()
    _mark_stage(db, deal, pos, user)
    audit(db, user.id, deal.id, "acceptance_unit", u.id, "new_round", None, {"round_no": u.round_no, "mode": body.mode})
    db.commit()
    return _unit_payload(db, u, pos)
