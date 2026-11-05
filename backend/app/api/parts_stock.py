"""Запчасти, шаг 6: склад — приёмка по заказу поставщику, отклонения и претензии, заказ клиента (жёсткий резерв), отгрузка, движения."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import (Client, Factory, Part, PartDeviation, PartReceipt, PartReceiptLine, PartRequest, PartRequestLine, PartReservation,
                      PartShipment, PartShipmentLine, PartStockMove, PoLine, PurchaseOrder, User, Warehouse)
from ..services.deal_service import audit, notify
from ..services.parts_stock import (DECISIONS, DEV_STATUS, DISCREPANCY, add_stock, consume_reservation, deviations_for, harden_soft,
                                    hard_reserved, reserve_hard)
from .common import serialize
from .parts_po import _sync as sync_po
from .parts_requests import _editable, _payload as request_payload, _req_or_404

router = APIRouter(prefix="/api/parts/stock", tags=["parts-stock"])
EDIT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING)
READ_ROLES = (*EDIT_ROLES, Role.FD, Role.LOGISTICIAN, Role.ADMIN)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к складу запчастей")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Приёмку и отгрузку ведут менеджеры, закупки, КД и ЗГД")
    return user


def _number(db: Session, model, prefix: str) -> str:
    p = f"{prefix}-{date.today().year}-"
    last = db.scalars(select(model.number).where(model.number.like(f"{p}%"))).all()
    n = max([int(x[len(p):]) for x in last if x[len(p):].isdigit()] or [0]) + 1
    return f"{p}{n:04d}"


def _physical(db: Session, wid: str) -> Warehouse:
    w = db.get(Warehouse, wid)
    if w is None or not w.is_active:
        raise HTTPException(404, "Склад не найден")
    if w.kind != "physical":
        raise HTTPException(422, "Виртуальный склад: приёмки и отгрузки нет")
    return w


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    whs = db.scalars(select(Warehouse).where(Warehouse.is_active.is_(True), Warehouse.kind == "physical").order_by(Warehouse.name)).all()
    return {"warehouses": [{"id": w.id, "name": w.name} for w in whs], "discrepancy": DISCREPANCY, "decisions": DECISIONS, "dev_status": DEV_STATUS,
            "can_edit": user.has(*EDIT_ROLES), "can_approve": user.has(Role.CD, Role.GD)}


# ------------------------------------------------------------------ приёмка

def _line_info(db: Session, pl: PoLine) -> dict:
    x = {"id": pl.id, "no": pl.no, "number": pl.number, "name": pl.name, "qty": pl.qty, "unit": pl.unit, "received_qty": pl.received_qty or 0,
         "remaining": round(max(pl.qty - (pl.received_qty or 0), 0), 4), "status": pl.status, "eta": pl.eta, "part_id": pl.part_id}
    rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
    if rl:
        req = db.get(PartRequest, rl.request_id)
        cl = db.get(Client, req.client_id)
        x.update(request_number=req.number, client_name=cl.name if cl else "", urgent=req.urgent)
    part = db.get(Part, pl.part_id) if pl.part_id else None
    x["article"] = part.article if part else pl.number
    return x


@router.get("/receivable")
def receivable(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Заказы поставщикам, по которым ждём товар: отправленные, подтверждённые и в поставке, с непринятыми строками."""
    out = []
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.kind == "external", PurchaseOrder.status.in_(("sent", "confirmed", "in_progress")))
                         .order_by(PurchaseOrder.created_at)).all():
        lines = [_line_info(db, l) for l in po.lines if l.status not in ("received", "cancelled") and l.qty - (l.received_qty or 0) > 0]
        if not lines:
            continue
        sup = db.get(Factory, po.supplier_id)
        out.append({"id": po.id, "number": po.number, "supplier_name": (sup.short_name or sup.name) if sup else "", "status": po.status, "lines": lines})
    return out


class ReceiptLineIn(BaseModel):
    po_line_id: str
    qty: float = Field(ge=0)
    qty_bad: float = Field(default=0, ge=0)
    cell: str = ""
    discrepancy: str = ""          # wrong | damaged — для qty_bad
    comment: str = ""


class ReceiptIn(BaseModel):
    po_id: str
    warehouse_id: str
    received_on: Optional[date] = None
    comment: str = ""
    lines: list[ReceiptLineIn]


def _receipt_payload(db: Session, r: PartReceipt) -> dict:
    d = serialize(r)
    po = db.get(PurchaseOrder, r.po_id)
    w = db.get(Warehouse, r.warehouse_id)
    d["po_number"], d["warehouse_name"] = po.number if po else "", w.name if w else ""
    d["lines"] = [serialize(l) for l in r.lines]
    d["deviations"] = [_dev_payload(db, x) for x in db.scalars(select(PartDeviation).where(
        PartDeviation.receipt_line_id.in_([l.id for l in r.lines] or [""]))).all()]
    return d


@router.post("/receipts", status_code=201)
def create_receipt(body: ReceiptIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Приёмка: годное — в остаток склада и сразу в жёсткий резерв под заявку клиента; расхождения — отклонения с решением."""
    po = db.get(PurchaseOrder, body.po_id)
    if po is None or po.kind != "external":
        raise HTTPException(404, "Заказ поставщику не найден")
    if po.status not in ("sent", "confirmed", "in_progress"):
        raise HTTPException(409, "Принимать можно по отправленному поставщику заказу")
    wh = _physical(db, body.warehouse_id)
    lines = {l.id: l for l in po.lines}
    items = [x for x in body.lines if x.qty > 0 or x.qty_bad > 0]
    if not items:
        raise HTTPException(422, "Укажите принятое количество")
    rc = PartReceipt(number=_number(db, PartReceipt, "ПР"), po_id=po.id, warehouse_id=wh.id, received_on=body.received_on or date.today(),
                     comment=body.comment, created_by=user.id)
    db.add(rc)
    db.flush()
    devs: list[PartDeviation] = []
    notify_lines: dict[str, list[str]] = {}
    now = datetime.now(timezone.utc).isoformat()
    for x in items:
        pl = lines.get(x.po_line_id)
        if pl is None:
            raise HTTPException(422, "Строка не из этого заказа")
        if pl.status in ("received", "cancelled"):
            raise HTTPException(409, f"Строка {pl.no}: уже {('принята' if pl.status == 'received' else 'отменена')}")
        if x.discrepancy and x.discrepancy not in ("wrong", "damaged"):
            raise HTTPException(422, "Расхождение по качеству — брак или пересорт")
        remaining = pl.qty - (pl.received_qty or 0)
        rl_line = PartReceiptLine(po_line_id=pl.id, part_id=pl.part_id, qty_expected=remaining, qty=x.qty, qty_bad=x.qty_bad, cell=x.cell.strip(),
                                  discrepancy=x.discrepancy if x.qty_bad else "", comment=x.comment)
        rc.lines.append(rl_line)
        db.flush()
        if x.qty > 0 and pl.part_id:
            add_stock(db, part_id=pl.part_id, warehouse_id=wh.id, qty=x.qty, kind="receipt", doc_number=rc.number, user_id=user.id,
                      cell=rl_line.cell, receipt_line_id=rl_line.id)
        pl.received_qty = round((pl.received_qty or 0) + x.qty, 4)
        if pl.received_qty >= pl.qty:
            pl.status = "received"
            if pl.warranty_claim_id:         # гарантийная замена пришла — компенсация деталью по претензии получена
                from .parts_warranty import part_compensation_received
                part_compensation_received(db, pl, rc.received_on)
            for od in db.scalars(select(PartDeviation).where(PartDeviation.po_line_id == pl.id, PartDeviation.kind == "short",
                                                             PartDeviation.status == "open", PartDeviation.decision.in_(("", "wait")))).all():
                od.status, od.closed_at, od.comment = "closed", datetime.now(timezone.utc), (od.comment + "; " if od.comment else "") + f"допоставлено, {rc.number}"
        pl.history = [*(pl.history or []), {"at": now, "status": pl.status, "by": user.id, "comment": f"приёмка {rc.number}: {x.qty:g}"}]
        rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
        if rl and x.qty > 0 and rl.status not in ("cancelled", "shipped"):
            got = reserve_hard(db, rl, wh.id, x.qty)
            rl_line.reserved_for_line_id = rl.id if got else None
            req = db.get(PartRequest, rl.request_id)
            if req.manager_id and req.manager_id != user.id:
                notify_lines.setdefault(req.manager_id, []).append(f"{req.number}, строка {rl.no} ({pl.number}): принято {x.qty:g}, в резерве {got:g}")
        for kind, q in deviations_for(remaining, x.qty, x.qty_bad, x.discrepancy):
            d = PartDeviation(number=_number(db, PartDeviation, "ОТ"), po_id=po.id, po_line_id=pl.id, receipt_line_id=rl_line.id, kind=kind, qty=q,
                              comment=x.comment, created_by=user.id)
            db.add(d)
            db.flush()
            devs.append(d)
    sync_po(db, po)
    for uid, rows in notify_lines.items():
        notify(db, None, uid, "parts_receipt", None, f"Приёмка {rc.number} на склад {wh.name}: " + "; ".join(rows))
    audit(db, user.id, None, "part_receipt", rc.id, "create", None, {"po": po.number, "lines": len(items), "deviations": [d.number for d in devs]})
    db.commit()
    return _receipt_payload(db, rc)


@router.get("/receipts")
def receipts(po_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PartReceipt).order_by(PartReceipt.created_at.desc()).limit(200)
    if po_id:
        stmt = stmt.where(PartReceipt.po_id == po_id)
    return [_receipt_payload(db, r) for r in db.scalars(stmt).all()]


# ------------------------------------------------------------------ отклонения и претензии

def _dev_payload(db: Session, d: PartDeviation) -> dict:
    x = serialize(d)
    po = db.get(PurchaseOrder, d.po_id)
    pl = db.get(PoLine, d.po_line_id)
    sup = db.get(Factory, po.supplier_id) if po and po.supplier_id else None
    x.update(po_number=po.number if po else "", supplier_name=(sup.short_name or sup.name) if sup else "", kind_title=DISCREPANCY.get(d.kind, d.kind),
             decision_title=DECISIONS.get(d.decision, ""), status_title=DEV_STATUS.get(d.status, d.status),
             line={"no": pl.no, "number": pl.number, "name": pl.name, "qty": pl.qty, "received_qty": pl.received_qty, "unit": pl.unit} if pl else None)
    x["photos"] = [{"i": i, "name": p.get("name", ""), "at": p.get("at")} for i, p in enumerate(d.photos or [])]
    return x


@router.get("/deviations")
def deviations(open_only: bool = True, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PartDeviation).order_by(PartDeviation.created_at.desc())
    if open_only:
        stmt = stmt.where(PartDeviation.status != "closed")
    return [_dev_payload(db, d) for d in db.scalars(stmt).all()]


def _dev_or_404(db: Session, did: str) -> PartDeviation:
    d = db.get(PartDeviation, did)
    if d is None:
        raise HTTPException(404, "Отклонение не найдено")
    return d


class DecisionIn(BaseModel):
    decision: str
    claim_text: str = ""
    comment: str = ""


@router.put("/deviations/{did}")
def decide(did: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Решение по отклонению: ждать допоставку, замена поставщиком (претензия), отменить остаток строки, принять как есть."""
    d = _dev_or_404(db, did)
    if d.status == "closed":
        raise HTTPException(409, "Отклонение закрыто")
    if body.decision not in DECISIONS:
        raise HTTPException(422, "Решение: ждать, замена, отменить или принять")
    if body.decision == "cancel" and d.kind != "short":
        raise HTTPException(422, "Отменить можно недопоставленный остаток")
    po = db.get(PurchaseOrder, d.po_id)
    pl = db.get(PoLine, d.po_line_id)
    d.decision, d.comment = body.decision, body.comment or d.comment
    if body.claim_text:
        d.claim_text = body.claim_text
    now = datetime.now(timezone.utc)
    if body.decision == "cancel":
        pl.qty = round(max(pl.received_qty or 0, 0), 4)
        pl.status = "received" if pl.received_qty else "cancelled"
        pl.history = [*(pl.history or []), {"at": now.isoformat(), "status": pl.status, "by": user.id, "comment": f"{d.number}: остаток отменён"}]
        rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
        if rl and pl.received_qty and pl.received_qty < rl.qty:
            rl.comment = (rl.comment + "; " if rl.comment else "") + f"поставщик допоставил {pl.received_qty:g} из {rl.qty:g} ({d.number}) — дозаказать"
        sync_po(db, po)
        d.status, d.closed_at = "closed", now
    elif body.decision == "accept":
        d.status, d.closed_at = "closed", now
    elif body.decision == "replace":
        d.status = "claim"
    audit(db, user.id, None, "part_deviation", d.id, "decide", None, body.model_dump())
    db.commit()
    return _dev_payload(db, d)


@router.post("/deviations/{did}/claim-sent")
def claim_sent(did: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    d = _dev_or_404(db, did)
    if not d.claim_text.strip():
        raise HTTPException(422, "Напишите текст претензии")
    d.claim_sent_at, d.status = datetime.now(timezone.utc), "claim"
    db.commit()
    return _dev_payload(db, d)


@router.post("/deviations/{did}/close")
def close_dev(did: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    d = _dev_or_404(db, did)
    d.status, d.closed_at = "closed", datetime.now(timezone.utc)
    db.commit()
    return _dev_payload(db, d)


@router.post("/deviations/{did}/photos")
def add_photo(did: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    d = _dev_or_404(db, did)
    po = db.get(PurchaseOrder, d.po_id)
    name = os.path.basename(file.filename or "photo.jpg").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        stored = adapters.storage().upload(path, f"Запчасти/{po.number.replace('/', '-')}", "07_fact", f"{d.number} {len(d.photos or []) + 1} {name}")
    finally:
        os.unlink(path)
    d.photos = [*(d.photos or []), {"path": stored.path, "name": name, "at": datetime.now(timezone.utc).isoformat()}]
    db.commit()
    return _dev_payload(db, d)


@router.get("/deviations/{did}/photos/{i}")
def photo(did: str, i: int, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    d = _dev_or_404(db, did)
    if i < 0 or i >= len(d.photos or []):
        raise HTTPException(404, "Фото не найдено")
    p = d.photos[i]
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(p.get("name", ""))[1])
    os.close(fd)
    adapters.storage().download(p["path"], path)
    return FileResponse(path, filename=p.get("name") or "photo", background=BackgroundTask(os.unlink, path))


# ------------------------------------------------------------------ заказ клиента и отгрузка

class LinesIn(BaseModel):
    line_ids: list[str]


@router.post("/requests/{rid}/client-order")
def client_order(rid: str, body: LinesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Клиент заказал строки: статус «заказана клиентом», мягкий резерв КП становится жёстким (строки «со склада»)."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    sel = [l for l in req.lines if l.id in set(body.line_ids)]
    if not sel:
        raise HTTPException(422, "Отметьте строки")
    short = []
    for l in sel:
        if l.status in ("cancelled", "unavailable", "shipped"):
            raise HTTPException(422, f"Строка {l.no}: статус не позволяет заказ")
        if l.price is None:
            raise HTTPException(422, f"Строка {l.no}: нет цены")
        if l.status in ("new", "rfq", "quoted", "in_kp", "replaced"):
            l.status = "ordered"
        if l.source == "stock" and l.part_id:
            got = harden_soft(db, l)
            if got + (l.shipped_qty or 0) < l.qty:
                short.append(l.no)
    audit(db, user.id, None, "part_request", req.id, "client_order", None, {"lines": [l.no for l in sel]})
    db.commit()
    return {**request_payload(db, req, user), "reserve_short": short}


@router.get("/requests/{rid}/shippable")
def shippable(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Что можно отгрузить по заявке: жёсткий резерв строк по складам."""
    req = _req_or_404(db, rid, user)
    out = []
    whs = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    for l in req.lines:
        if not l.part_id or l.status in ("cancelled", "shipped"):
            continue
        rows = db.execute(select(PartReservation.warehouse_id, PartReservation.qty).where(
            PartReservation.line_id == l.id, PartReservation.kind == "hard", PartReservation.released_at.is_(None))).all()
        by_wh: dict[str, float] = {}
        for wid, q in rows:
            by_wh[wid] = by_wh.get(wid, 0) + q
        part = db.get(Part, l.part_id)
        out.append({"line_id": l.id, "no": l.no, "article": part.article, "name": part.name_ru, "qty": l.qty, "unit": l.unit, "shipped_qty": l.shipped_qty or 0,
                    "status": l.status, "reserved": [{"warehouse_id": w, "warehouse": whs.get(w, ""), "qty": q} for w, q in by_wh.items()]})
    return out


class ShipLineIn(BaseModel):
    line_id: str
    qty: float = Field(gt=0)


class ShipmentIn(BaseModel):
    warehouse_id: str
    lines: list[ShipLineIn]
    shipped_on: Optional[date] = None
    comment: str = ""


@router.post("/requests/{rid}/shipments", status_code=201)
def ship(rid: str, body: ShipmentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Отгрузка клиенту со склада: по жёсткому резерву строки; остаток и резерв уменьшаются; полностью отгруженная строка — «отгружена»."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    wh = _physical(db, body.warehouse_id)
    by_id = {l.id: l for l in req.lines}
    sh = PartShipment(number=_number(db, PartShipment, "ОГ"), request_id=req.id, warehouse_id=wh.id, shipped_on=body.shipped_on or date.today(),
                      comment=body.comment, created_by=user.id)
    db.add(sh)
    db.flush()
    for x in body.lines:
        l = by_id.get(x.line_id)
        if l is None or not l.part_id:
            raise HTTPException(422, "Строка не из этой заявки")
        if l.status not in ("ordered", "in_stock", "po", "transit"):
            raise HTTPException(422, f"Строка {l.no}: отгрузка — после заказа клиента")
        res = hard_reserved(db, l.id, wh.id)
        if x.qty > res + 1e-9:
            raise HTTPException(409, f"Строка {l.no}: в резерве на складе {wh.name} {res:g}, отгрузить {x.qty:g} нельзя")
        if (l.shipped_qty or 0) + x.qty > l.qty + 1e-9:
            raise HTTPException(422, f"Строка {l.no}: больше, чем в заявке")
        sl = PartShipmentLine(request_line_id=l.id, part_id=l.part_id, qty=x.qty)
        sh.lines.append(sl)
        db.flush()
        consume_reservation(db, l, wh.id, x.qty)
        add_stock(db, part_id=l.part_id, warehouse_id=wh.id, qty=-x.qty, kind="shipment", doc_number=sh.number, user_id=user.id, shipment_line_id=sl.id)
        l.shipped_qty = round((l.shipped_qty or 0) + x.qty, 4)
        if l.shipped_qty >= l.qty:
            l.status = "shipped"
    if not sh.lines:
        raise HTTPException(422, "Укажите количество к отгрузке")
    audit(db, user.id, None, "part_shipment", sh.id, "create", None, {"request": req.number, "lines": len(sh.lines)})
    db.commit()
    return {**request_payload(db, req, user), "shipment": {**serialize(sh), "lines": [serialize(l) for l in sh.lines], "warehouse_name": wh.name}}


@router.get("/shipments")
def shipments(request_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PartShipment).order_by(PartShipment.created_at.desc()).limit(200)
    if request_id:
        stmt = stmt.where(PartShipment.request_id == request_id)
    out = []
    for s in db.scalars(stmt).all():
        req = db.get(PartRequest, s.request_id)
        w = db.get(Warehouse, s.warehouse_id)
        out.append({**serialize(s), "request_number": req.number if req else "", "warehouse_name": w.name if w else "", "lines": [serialize(l) for l in s.lines]})
    return out


@router.get("/moves")
def moves(part_id: str = "", warehouse_id: str = "", limit: int = 200, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PartStockMove).order_by(PartStockMove.at.desc()).limit(min(limit, 1000))
    if part_id:
        stmt = stmt.where(PartStockMove.part_id == part_id)
    if warehouse_id:
        stmt = stmt.where(PartStockMove.warehouse_id == warehouse_id)
    whs = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    users = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out = []
    for m in db.scalars(stmt).all():
        p = db.get(Part, m.part_id)
        out.append({**serialize(m), "article": p.article if p else "", "name": p.name_ru if p else "", "warehouse": whs.get(m.warehouse_id, ""),
                    "user": users.get(m.user_id, "")})
    return out
