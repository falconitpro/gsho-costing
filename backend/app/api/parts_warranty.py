"""Запчасти: гарантийные претензии к заводу и сборные партии у экспедитора (ТЗ запчастей, разделы 7 и 9)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
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
from ..models import (Client, ClientMachine, Deal, Factory, Part, PartBatch, PartRequest, PartRequestLine, PoLine, PurchaseOrder, SupplyRoute, User)
from ..services.deal_service import audit
from ..services.parts_pricing import to_rub
from ..services.parts_supplier import supplier_language
from ..services.parts_warranty import (BATCH_LINE_STATUS, BATCH_STATUS, CLAIM_STATUS, COMP_KINDS, allocate, build_claim_xlsx,
                                       status_after_compensation)
from .common import serialize
from .parts_po import PoCreateIn, StockLineIn, _set_eta, _sync, create as create_po
from .parts_setup import latest_fx

router = APIRouter(prefix="/api/parts", tags=["parts-warranty"])
EDIT_ROLES = (Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD, Role.PURCHASING)
READ_ROLES = (*EDIT_ROLES, Role.FD, Role.LOGISTICIAN, Role.ADMIN)
BATCH_ROLES = (Role.LOGISTICIAN, Role.MANAGER, Role.PURCHASING, Role.CD, Role.GD)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Претензии ведут менеджеры, сервис, закупки, КД и ЗГД")
    return user


def _number(db: Session, model, prefix: str) -> str:
    p = f"{prefix}-{date.today().year}-"
    last = db.scalars(select(model.number).where(model.number.like(f"{p}%"))).all()
    n = max([int(x[len(p):]) for x in last if x[len(p):].isdigit()] or [0]) + 1
    return f"{p}{n:04d}"


# ------------------------------------------------------------------ гарантийные претензии

def _claim_payload(db: Session, c) -> dict:
    d = serialize(c)
    sup = db.get(Factory, c.supplier_id)
    cl = db.get(Client, c.client_id) if c.client_id else None
    deal = db.get(Deal, c.deal_id) if c.deal_id else None
    m = db.get(ClientMachine, c.machine_id) if c.machine_id else None
    req = db.get(PartRequest, c.request_id) if c.request_id else None
    d.update(supplier_name=(sup.short_name or sup.name) if sup else "", client_name=cl.name if cl else "", deal_number=deal.number if deal else "",
             machine_title=" ".join(x for x in ((m.manufacturer, m.model, f"зав. № {m.serial}" if m.serial else "") if m else ()) if x),
             request_number=req.number if req else "", status_title=CLAIM_STATUS.get(c.status, c.status),
             cost_rub=round(sum(float(l.get("cost_rub") or 0) * float(l.get("qty") or 1) for l in c.lines or []), 2),
             compensated_rub=round(sum(float(x.get("amount_rub") or 0) for x in c.compensations or [] if x.get("received_on")), 2),
             overdue=bool(c.status == "sent" and c.answer_due and c.answer_due < date.today()))
    d["photos"] = [{"i": i, "name": p.get("name", "")} for i, p in enumerate(c.photos or [])]
    return d


class ClaimLineIn(BaseModel):
    part_id: Optional[str] = None
    article: str = ""
    name: str = ""
    qty: float = Field(default=1, gt=0)
    cost_rub: Optional[float] = None


class ClaimIn(BaseModel):
    supplier_id: str
    client_id: Optional[str] = None
    machine_id: Optional[str] = None
    deal_id: Optional[str] = None
    request_id: Optional[str] = None           # гарантийная заявка — строки и клиент берутся из неё
    failure_on: Optional[date] = None
    machine_hours: Optional[float] = None
    description: str = ""
    answer_days: int = 14
    lines: list[ClaimLineIn] = Field(default_factory=list)


def _lines_from(db: Session, body: ClaimIn) -> list[dict]:
    out = []
    if body.request_id and not body.lines:
        req = db.get(PartRequest, body.request_id)
        for l in req.lines if req else []:
            p = db.get(Part, l.part_id) if l.part_id else None
            out.append({"part_id": l.part_id, "article": p.article if p else l.raw_article, "name": p.name_ru if p else l.raw_name, "qty": l.qty,
                        "cost_rub": (l.calc or {}).get("cost")})
        return out
    for l in body.lines:
        p = db.get(Part, l.part_id) if l.part_id else (db.scalars(select(Part).where(Part.article == l.article.strip())).first() if l.article.strip() else None)
        out.append({"part_id": p.id if p else None, "article": p.article if p else l.article.strip(), "name": (p.name_ru if p else "") or l.name,
                    "qty": l.qty, "cost_rub": l.cost_rub})
    return out


@router.post("/claims", status_code=201)
def create_claim(body: ClaimIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    from ..models import WarrantyClaim
    if db.get(Factory, body.supplier_id) is None:
        raise HTTPException(422, "Выберите завод")
    client_id, machine_id, deal_id = body.client_id, body.machine_id, body.deal_id
    req = db.get(PartRequest, body.request_id) if body.request_id else None
    if req:
        client_id, machine_id = client_id or req.client_id, machine_id or req.machine_id
    m = db.get(ClientMachine, machine_id) if machine_id else None
    if m:
        client_id, deal_id = client_id or m.client_id, deal_id or m.deal_id
    c = WarrantyClaim(number=_number(db, WarrantyClaim, "ГП"), supplier_id=body.supplier_id, client_id=client_id, machine_id=machine_id, deal_id=deal_id,
                      request_id=body.request_id, failure_on=body.failure_on, machine_hours=body.machine_hours, description=body.description,
                      lines=_lines_from(db, body), created_by=user.id, answer_due=None)
    if not c.lines:
        raise HTTPException(422, "Добавьте детали претензии")
    db.add(c)
    db.flush()
    audit(db, user.id, c.deal_id, "warranty_claim", c.id, "create", None, {"number": c.number})
    db.commit()
    return _claim_payload(db, c)


@router.get("/claims")
def claims(open_only: bool = True, supplier_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    from ..models import WarrantyClaim
    stmt = select(WarrantyClaim).order_by(WarrantyClaim.created_at.desc())
    if open_only:
        stmt = stmt.where(WarrantyClaim.status.not_in(("closed",)))
    if supplier_id:
        stmt = stmt.where(WarrantyClaim.supplier_id == supplier_id)
    return [_claim_payload(db, c) for c in db.scalars(stmt).all()]


def _claim_or_404(db: Session, cid: str):
    from ..models import WarrantyClaim
    c = db.get(WarrantyClaim, cid)
    if c is None:
        raise HTTPException(404, "Претензия не найдена")
    return c


@router.get("/claims/{cid}")
def claim(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return _claim_payload(db, _claim_or_404(db, cid))


@router.put("/claims/{cid}")
def update_claim(cid: str, body: ClaimIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = _claim_or_404(db, cid)
    if c.status != "draft":
        raise HTTPException(409, "Отправленную претензию не меняют — добавьте комментарий в ответ завода")
    for k in ("supplier_id", "client_id", "machine_id", "deal_id", "failure_on", "machine_hours", "description"):
        setattr(c, k, getattr(body, k))
    c.lines = _lines_from(db, body) or c.lines
    db.commit()
    return _claim_payload(db, c)


@router.post("/claims/{cid}/photos")
def claim_photo(cid: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = _claim_or_404(db, cid)
    name = os.path.basename(file.filename or "photo.jpg").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        stored = adapters.storage().upload(path, f"Запчасти/Претензии/{c.number}", "07_fact", f"{len(c.photos or []) + 1:02d} {name}")
    finally:
        os.unlink(path)
    c.photos = [*(c.photos or []), {"path": stored.path, "name": name, "at": datetime.now(timezone.utc).isoformat()}]
    db.commit()
    return _claim_payload(db, c)


@router.get("/claims/{cid}/photos/{i}")
def claim_photo_get(cid: str, i: int, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    c = _claim_or_404(db, cid)
    if i < 0 or i >= len(c.photos or []):
        raise HTTPException(404, "Фото не найдено")
    p = c.photos[i]
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(p.get("name", ""))[1])
    os.close(fd)
    adapters.storage().download(p["path"], path)
    return FileResponse(path, filename=p.get("name") or "photo", content_disposition_type="inline", background=BackgroundTask(os.unlink, path))


@router.get("/claims/{cid}/xlsx")
def claim_xlsx(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    c = _claim_or_404(db, cid)
    d = _claim_payload(db, c)
    sup = db.get(Factory, c.supplier_id)
    path = os.path.join(tempfile.mkdtemp(), f"{c.number}.xlsx")
    build_claim_xlsx(path, number=c.number, supplier=sup.name if sup else "", client=d["client_name"], machine=d["machine_title"], hours=c.machine_hours,
                     failure_on=c.failure_on.strftime("%d.%m.%Y") if c.failure_on else "", description=c.description, lines=c.lines,
                     lang=supplier_language(sup.country if sup else "", sup.language if sup else ""))
    return FileResponse(path, filename=os.path.basename(path), background=BackgroundTask(os.unlink, path))


class SentIn(BaseModel):
    answer_days: int = Field(default=14, ge=1, le=120)


@router.post("/claims/{cid}/sent")
def claim_sent(cid: str, body: SentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Претензия отправлена заводу (WeCom, почта — с фото и наработкой); срок ответа завода — с напоминанием."""
    c = _claim_or_404(db, cid)
    if c.status != "draft":
        raise HTTPException(409, "Претензия уже отправлена")
    c.status, c.sent_at, c.answer_due = "sent", datetime.now(timezone.utc), date.today() + timedelta(days=body.answer_days)
    db.commit()
    return _claim_payload(db, c)


class AnswerIn(BaseModel):
    status: str                 # accepted | partial | rejected
    answer: str = ""


@router.post("/claims/{cid}/answer")
def claim_answer(cid: str, body: AnswerIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = _claim_or_404(db, cid)
    if body.status not in ("accepted", "partial", "rejected"):
        raise HTTPException(422, "Ответ завода: признана, частично или отказ")
    if c.status not in ("sent", "accepted", "partial", "rejected"):
        raise HTTPException(409, "Сначала отметьте, что претензия отправлена")
    c.status, c.answer, c.answered_at = body.status, body.answer, datetime.now(timezone.utc)
    if body.status == "rejected":
        c.close_reason = c.close_reason or "отказ завода — расход остаётся на гарантийном резерве"
    db.commit()
    return _claim_payload(db, c)


class CompIn(BaseModel):
    kind: str                         # part | money | discount
    qty: Optional[float] = None
    amount: Optional[float] = None
    currency: str = "CNY"
    note: str = ""
    part_ids: list[str] = Field(default_factory=list)   # деталью — какие детали (по умолчанию все из претензии)


@router.post("/claims/{cid}/compensations")
def add_comp(cid: str, body: CompIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Компенсация: деталью — строки «гарантийная замена» по цене 0 в новом заказе заводу; деньгами или скидкой — сумма к получению."""
    c = _claim_or_404(db, cid)
    if c.status not in ("accepted", "partial"):
        raise HTTPException(409, "Компенсация — после признания претензии заводом")
    if body.kind not in COMP_KINDS:
        raise HTTPException(422, "Способ: деталью, деньгами или скидкой")
    comp = {"id": uuid.uuid4().hex, "kind": body.kind, "note": body.note, "received_on": None, "at": datetime.now(timezone.utc).isoformat()}
    if body.kind == "part":
        lines = [l for l in c.lines if l.get("part_id") and (not body.part_ids or l["part_id"] in body.part_ids)]
        if not lines:
            raise HTTPException(422, "В претензии нет деталей из каталога — заведите карточки")
        pos = create_po(PoCreateIn(supplier_id=c.supplier_id, stock_lines=[StockLineIn(part_id=l["part_id"], qty=l["qty"], warranty_claim_id=c.id,
                                                                                        comment=f"гарантийная замена, {c.number}") for l in lines],
                                   comment=f"Гарантийная замена по претензии {c.number}"), db, user)
        comp.update(po_ids=[p["id"] for p in pos], po_numbers=[p["number"] for p in pos], qty=sum(float(l["qty"]) for l in lines),
                    amount_rub=round(sum(float(l.get("cost_rub") or 0) * float(l["qty"]) for l in lines), 2))
        c = _claim_or_404(db, cid)
    else:
        if not body.amount:
            raise HTTPException(422, "Укажите сумму")
        comp.update(amount=body.amount, currency=body.currency, amount_rub=round(to_rub(body.amount, body.currency, latest_fx(db)) or 0, 2))
    c.compensations = [*(c.compensations or []), comp]
    db.commit()
    return _claim_payload(db, c)


class ReceivedIn(BaseModel):
    received_on: Optional[date] = None


@router.post("/claims/{cid}/compensations/{comp_id}/received")
def comp_received(cid: str, comp_id: str, body: ReceivedIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Деньги поступили или скидка применена (вводит ФД или менеджер); деталь — отмечается сама при приёмке заказа."""
    c = _claim_or_404(db, cid)
    comps = [dict(x) for x in c.compensations or []]
    x = next((x for x in comps if x["id"] == comp_id), None)
    if x is None:
        raise HTTPException(404, "Компенсация не найдена")
    x["received_on"] = (body.received_on or date.today()).isoformat()
    c.compensations = comps
    c.status = status_after_compensation(c.status, comps)
    db.commit()
    return _claim_payload(db, c)


def part_compensation_received(db: Session, pl: PoLine, on: date) -> None:
    """Приёмка строки «гарантийная замена»: компенсация деталью по претензии получена, когда приняты все её строки."""
    from ..models import WarrantyClaim
    c = db.get(WarrantyClaim, pl.warranty_claim_id)
    if c is None:
        return
    comps = [dict(x) for x in c.compensations or []]
    for x in comps:
        if x["kind"] != "part" or x.get("received_on") or pl.po_id not in (x.get("po_ids") or []):
            continue
        lines = db.scalars(select(PoLine).where(PoLine.po_id.in_(x["po_ids"]), PoLine.warranty_claim_id == c.id)).all()
        if all(l.status == "received" or l.id == pl.id for l in lines):
            x["received_on"] = on.isoformat()
    c.compensations = comps
    c.status = status_after_compensation(c.status, comps)


class CloseIn(BaseModel):
    reason: str = ""


@router.post("/claims/{cid}/close")
def close_claim(cid: str, body: CloseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = _claim_or_404(db, cid)
    c.status, c.closed_at = "closed", datetime.now(timezone.utc)
    c.close_reason = body.reason or c.close_reason
    db.commit()
    return _claim_payload(db, c)


# ------------------------------------------------------------------ сборные партии у экспедитора

def _sees_costs(user: CurrentUser) -> bool:
    return user.can("cost") or user.has(Role.LOGISTICIAN)


def _batch_payload(db: Session, b: PartBatch, user: CurrentUser) -> dict:
    d = serialize(b)
    d["status_title"] = BATCH_STATUS.get(b.status, b.status)
    r = db.get(SupplyRoute, b.route_id) if b.route_id else None
    d["route_name"] = r.name if r else ""
    lines = []
    for pl in db.scalars(select(PoLine).where(PoLine.batch_id == b.id)).all():
        po = db.get(PurchaseOrder, pl.po_id)
        part = db.get(Part, pl.part_id) if pl.part_id else None
        rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
        req = db.get(PartRequest, rl.request_id) if rl else None
        lines.append({"id": pl.id, "po_number": po.number if po else "", "no": pl.no, "number": pl.number, "name": pl.name, "qty": pl.qty, "unit": pl.unit,
                      "status": pl.status, "eta": pl.eta, "weight_kg": round((part.weight_kg or 0) * pl.qty, 3) if part and part.weight_kg else None,
                      "request_number": req.number if req else "", "extra_cost_rub": pl.extra_cost_rub if _sees_costs(user) else None})
    d["lines"] = lines
    d["total_weight_kg"] = round(sum(l["weight_kg"] or 0 for l in lines), 3)
    if not _sees_costs(user):
        d.pop("costs", None)
    else:
        d["costs_rub"] = round(sum(to_rub(x.get("amount") or 0, x.get("currency") or "RUB", latest_fx(db)) or 0 for x in b.costs or []), 2)
    return d


def _reallocate(db: Session, b: PartBatch) -> None:
    lines = db.scalars(select(PoLine).where(PoLine.batch_id == b.id)).all()
    fx = latest_fx(db)
    total = sum(to_rub(x.get("amount") or 0, x.get("currency") or "RUB", fx) or 0 for x in b.costs or [])
    rows = []
    for pl in lines:
        part = db.get(Part, pl.part_id) if pl.part_id else None
        po = db.get(PurchaseOrder, pl.po_id)
        rows.append({"id": pl.id, "weight": (part.weight_kg or 0) * pl.qty if part else 0,
                     "value": (to_rub(pl.price * pl.qty, po.currency, fx) or 0) if po else 0})
    shares = allocate(rows, round(total, 2), b.allocation)
    for pl in lines:
        pl.extra_cost_rub = shares.get(pl.id, 0.0)


class BatchIn(BaseModel):
    forwarder: str = ""
    route_id: Optional[str] = None
    po_line_ids: list[str] = Field(default_factory=list)
    eta: Optional[date] = None
    tracking: str = ""
    allocation: str = "weight"
    comment: str = ""


def _batcher(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*BATCH_ROLES):
        raise HTTPException(403, "Партии ведут логист, менеджеры, закупки, КД и ЗГД")
    return user


def _attach(db: Session, b: PartBatch, ids: list[str]) -> None:
    for pid in ids:
        pl = db.get(PoLine, pid)
        if pl is None:
            raise HTTPException(404, "Строка заказа не найдена")
        po = db.get(PurchaseOrder, pl.po_id)
        if po.kind != "external" or pl.status in ("received", "cancelled", "ordered"):
            raise HTTPException(422, f"{po.number}, строка {pl.no}: в партию — подтверждённые или отгруженные поставщиком строки")
        if pl.batch_id and pl.batch_id != b.id:
            raise HTTPException(409, f"{po.number}, строка {pl.no}: уже в другой партии")
        pl.batch_id = b.id


@router.get("/batches/candidates")
def batch_candidates(db: Session = Depends(get_db), user: CurrentUser = Depends(_batcher)):
    """Строки заказов поставщикам, которые можно собрать в партию: подтверждены или отгружены, ещё не в партии."""
    rows = db.execute(select(PoLine, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id).where(
        PurchaseOrder.kind == "external", PoLine.batch_id.is_(None), PoLine.status.in_(("confirmed", "shipped", "consolidation")))).all()
    out = []
    for pl, po in rows:
        sup = db.get(Factory, po.supplier_id)
        out.append({"id": pl.id, "po_number": po.number, "supplier_name": (sup.short_name or sup.name) if sup else "", "route_id": po.route_id, "no": pl.no,
                    "number": pl.number, "name": pl.name, "qty": pl.qty, "unit": pl.unit, "status": pl.status, "eta": pl.eta})
    return out


@router.post("/batches", status_code=201)
def create_batch(body: BatchIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_batcher)):
    if body.allocation not in ("weight", "value"):
        raise HTTPException(422, "Распределение — по весу или по стоимости")
    b = PartBatch(number=_number(db, PartBatch, "ПТ"), forwarder=body.forwarder, route_id=body.route_id, eta=body.eta, tracking=body.tracking,
                  allocation=body.allocation, comment=body.comment, created_by=user.id)
    db.add(b)
    db.flush()
    _attach(db, b, body.po_line_ids)
    _apply_status(db, b, user)
    db.commit()
    return _batch_payload(db, b, user)


@router.get("/batches")
def batches(open_only: bool = True, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PartBatch).order_by(PartBatch.created_at.desc())
    if open_only:
        stmt = stmt.where(PartBatch.status != "closed")
    return [_batch_payload(db, b, user) for b in db.scalars(stmt).all()]


def _batch_or_404(db: Session, bid: str) -> PartBatch:
    b = db.get(PartBatch, bid)
    if b is None:
        raise HTTPException(404, "Партия не найдена")
    return b


@router.get("/batches/{bid}")
def batch(bid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return _batch_payload(db, _batch_or_404(db, bid), user)


class BatchUpdateIn(BaseModel):
    add_line_ids: list[str] = Field(default_factory=list)
    remove_line_ids: list[str] = Field(default_factory=list)
    status: Optional[str] = None
    eta: Optional[date] = None
    shipped_on: Optional[date] = None
    forwarder: Optional[str] = None
    tracking: Optional[str] = None
    costs: Optional[list[dict]] = None
    allocation: Optional[str] = None
    comment: str = ""


def _apply_status(db: Session, b: PartBatch, user: CurrentUser, comment: str = "") -> None:
    """Статус партии → статус строк заказов (консолидация, в пути, таможня); сроки строк — по дате прибытия партии."""
    target = BATCH_LINE_STATUS.get(b.status)
    pos = set()
    now = datetime.now(timezone.utc).isoformat()
    for pl in db.scalars(select(PoLine).where(PoLine.batch_id == b.id)).all():
        if target and pl.status not in ("received", "cancelled") and pl.status != target:
            from ..services.parts_po import can_move
            if can_move(pl.status, target):
                pl.status = target
                pl.history = [*(pl.history or []), {"at": now, "status": target, "by": user.id, "comment": f"партия {b.number}"}]
        if b.eta and pl.status not in ("received", "cancelled"):
            _set_eta(db, pl, b.eta, user, comment or f"партия {b.number}")
        pos.add(pl.po_id)
    for pid in pos:
        _sync(db, db.get(PurchaseOrder, pid))


@router.put("/batches/{bid}")
def update_batch(bid: str, body: BatchUpdateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_batcher)):
    b = _batch_or_404(db, bid)
    if b.status == "closed":
        raise HTTPException(409, "Партия закрыта")
    _attach(db, b, body.add_line_ids)
    for pid in body.remove_line_ids:
        pl = db.get(PoLine, pid)
        if pl and pl.batch_id == b.id:
            pl.batch_id, pl.extra_cost_rub = None, 0
    if body.status:
        if body.status not in BATCH_STATUS:
            raise HTTPException(422, "Неизвестный статус партии")
        order = list(BATCH_STATUS)
        if order.index(body.status) < order.index(b.status):
            raise HTTPException(409, "Статус партии назад не переводится")
        b.status = body.status
        if body.status == "transit" and not b.shipped_on:
            b.shipped_on = body.shipped_on or date.today()
    for k in ("eta", "shipped_on", "forwarder", "tracking"):
        v = getattr(body, k)
        if v is not None:
            setattr(b, k, v)
    if body.allocation:
        b.allocation = body.allocation
    if body.costs is not None:
        if not _sees_costs(user):
            raise HTTPException(403, "Расходы партии вводят логист, КД, ФД и ЗГД")
        b.costs = [{"item": str(x.get("item") or ""), "amount": float(x.get("amount") or 0), "currency": str(x.get("currency") or "RUB")}
                   for x in body.costs if float(x.get("amount") or 0)]
    if body.comment:
        b.comment = (b.comment + "; " if b.comment else "") + body.comment
    db.flush()
    _apply_status(db, b, user, body.comment)
    _reallocate(db, b)
    audit(db, user.id, None, "part_batch", b.id, "update", None, body.model_dump(mode="json"))
    db.commit()
    return _batch_payload(db, b, user)
