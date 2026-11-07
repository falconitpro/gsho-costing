"""Запчасти, шаг 3: заявки клиентов с попозиционным учётом, подбор, цена и визы по марже (ТЗ запчастей, разделы 5–6)."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role, filter_payload
from ..db import get_db
from ..models import Client, LegalEntity, Part, PartKp, PartRequest, PartRequestLine, User
from ..services import settings_service
from ..services.deal_service import audit, notify
from ..services.parts_kp import build_kp_lines, diff_kp, release_expired, render_docx, reserve_soft
from ..services.parts_pricing import LINE_STATUS, kp_summary, request_status
from ..services.parts_requests import approvals_pending, line_payload, match_lines, next_number, parse_lines_text, price_lines
from .common import serialize
from .parts_setup import current_policy, latest_fx

router = APIRouter(prefix="/api/parts/requests", tags=["parts-requests"])

CHANNELS = {"email": "почта", "wecom": "WeCom", "phone": "телефон", "excel": "список Excel", "other": "другое"}
CLOSE_KINDS = {"lost": "проиграна", "cancelled": "отменена клиентом", "done": "выполнена"}
EDIT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING)
READ_ROLES = (Role.MANAGER, Role.CD, Role.FD, Role.GD, Role.PURCHASING, Role.SERVICE_HEAD, Role.LOGISTICIAN, Role.ADMIN, Role.OBSERVER)


def _can_see(user: CurrentUser, req: PartRequest) -> bool:
    if user.has(Role.CD, Role.FD, Role.GD, Role.PURCHASING, Role.SERVICE_HEAD, Role.LOGISTICIAN, Role.ADMIN, Role.OBSERVER):
        return True
    return user.has(Role.MANAGER) and user.is_manager_of(req.manager_id)


def _req_or_404(db: Session, rid: str, user: CurrentUser) -> PartRequest:
    req = db.get(PartRequest, rid)
    if req is None or not _can_see(user, req):
        raise HTTPException(404, "Заявка не найдена")
    return req


def _editable(user: CurrentUser, req: PartRequest) -> None:
    if not user.has(*EDIT_ROLES) or (user.has(Role.MANAGER) and not user.has(Role.CD, Role.GD, Role.PURCHASING) and req.manager_id != user.id):
        raise HTTPException(403, "Заявку ведёт её менеджер, закупки, КД или ЗГД")
    if req.closed:
        raise HTTPException(409, "Заявка закрыта")


def _recalc(db: Session, req: PartRequest) -> None:
    pol = current_policy(db)
    price_lines(db, req, latest_fx(db), pol.rules if pol else {})


def _payload(db: Session, req: PartRequest, user: CurrentUser) -> dict:
    d = serialize(req)
    client = db.get(Client, req.client_id)
    mgr = db.get(User, req.manager_id) if req.manager_id else None
    d["client_name"] = client.name if client else ""
    d["manager_name"] = mgr.display_name if mgr else ""
    lines = [line_payload(l, db.get(Part, l.part_id) if l.part_id else None) for l in req.lines]
    d["lines"] = lines
    active = [l for l in lines if l["status"] not in ("cancelled", "unavailable")]
    d["summary"] = kp_summary(active, settings_service.get(db, "parts_min_margin_kp_rub"))
    d["summary"]["approval_needed"] = approvals_pending(req, active)
    d["status"] = request_status(lines)
    d["status_text"] = CLOSE_KINDS.get(req.closed) or d["status"]["text"]
    kps = db.scalars(select(PartKp).where(PartKp.request_id == req.id).order_by(PartKp.revision_no.desc())).all()
    d["kps"] = [{**serialize(k), "expired": k.valid_until < date.today()} for k in kps]
    return filter_payload(d, user)


@router.get("/meta")
def meta(user: CurrentUser = Depends(get_current_user)):
    return {"channels": CHANNELS, "line_status": LINE_STATUS, "close_kinds": CLOSE_KINDS, "can_create": user.has(*EDIT_ROLES)}


@router.get("")
def requests(open_only: bool = True, q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к заявкам на запчасти")
    stmt = select(PartRequest).order_by(PartRequest.created_at.desc())
    if open_only:
        stmt = stmt.where(PartRequest.closed == "")
    clients = {c.id: c.name for c in db.scalars(select(Client)).all()}
    out = []
    for r in db.scalars(stmt).all():
        if not _can_see(user, r):
            continue
        name = clients.get(r.client_id, "")
        if q and q.lower() not in f"{r.number} {name} {r.machine}".lower():
            continue
        lines = [{"status": l.status, "sum_rub": (l.calc or {}).get("sum_rub")} for l in r.lines]
        st = request_status(lines)
        out.append({"id": r.id, "number": r.number, "client_name": name, "machine": r.machine, "urgent": r.urgent, "created_at": r.created_at.isoformat(),
                    "lines": st["total"], "status_text": CLOSE_KINDS.get(r.closed) or st["text"], "closed": r.closed,
                    "price_rub": round(sum(l["sum_rub"] or 0 for l in lines if l["status"] not in ("cancelled", "unavailable")), 2)})
    return filter_payload(out, user)


class LineIn(BaseModel):
    raw_article: str = ""
    raw_name: str = ""
    qty: float = 1
    unit: str = "шт"


class RequestIn(BaseModel):
    client_id: str
    contact: str = ""
    channel: str = "email"
    machine: str = ""
    machine_id: Optional[str] = None      # машина из парка клиента
    urgent: bool = False
    warranty: bool = False
    comment: str = ""
    lines: list[LineIn] = Field(default_factory=list)
    lines_text: str = ""          # вставка из письма или Excel: «артикул <Tab> наименование <Tab> количество»


def _add_lines(req: PartRequest, items: list[dict]) -> None:
    n = max([l.no for l in req.lines] or [0])
    for it in items:
        if not (it.get("raw_article") or "").strip() and not (it.get("raw_name") or "").strip():
            continue
        if float(it.get("qty") or 0) <= 0:
            raise HTTPException(422, f"Количество в строке «{it.get('raw_article') or it.get('raw_name')}» должно быть больше нуля")
        n += 1
        req.lines.append(PartRequestLine(no=n, raw_article=(it.get("raw_article") or "").strip(), raw_name=(it.get("raw_name") or "").strip(),
                                         qty=float(it["qty"]), unit=it.get("unit") or "шт"))


@router.post("", status_code=201)
def create(body: RequestIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Заявки на запчасти заводят менеджеры, закупки, КД и ЗГД")
    if db.get(Client, body.client_id) is None:
        raise HTTPException(422, "Выберите клиента")
    if body.channel not in CHANNELS:
        raise HTTPException(422, "Неизвестный канал заявки")
    machine = body.machine
    if body.machine_id:
        from ..models import ClientMachine
        m = db.get(ClientMachine, body.machine_id)
        if m is None or m.client_id != body.client_id:
            raise HTTPException(422, "Машина не из парка этого клиента")
        machine = machine or " ".join(x for x in (m.manufacturer, m.model, f"зав. № {m.serial}" if m.serial else "") if x)
    req = PartRequest(number=next_number(db), client_id=body.client_id, manager_id=user.id, contact=body.contact, channel=body.channel,
                      machine=machine, machine_id=body.machine_id, urgent=body.urgent, warranty=body.warranty, comment=body.comment)
    _add_lines(req, [l.model_dump() for l in body.lines] + parse_lines_text(body.lines_text))
    if not req.lines:
        raise HTTPException(422, "Добавьте хотя бы одну строку: артикул или наименование")
    db.add(req)
    db.flush()
    match_lines(db, req)
    _recalc(db, req)
    audit(db, user.id, None, "part_request", req.id, "create", None, {"number": req.number, "lines": len(req.lines)})
    db.commit()
    return _payload(db, req, user)


@router.get("/{rid}")
def card(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    if release_expired(db):       # истёкшие мягкие резервы снимаются при обращении
        db.commit()
    return _payload(db, req, user)


class AddLinesIn(BaseModel):
    lines: list[LineIn] = Field(default_factory=list)
    lines_text: str = ""


@router.post("/{rid}/lines")
def add_lines(rid: str, body: AddLinesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    _add_lines(req, [l.model_dump() for l in body.lines] + parse_lines_text(body.lines_text))
    db.flush()
    match_lines(db, req)
    _recalc(db, req)
    db.commit()
    return _payload(db, req, user)


class LinePatch(BaseModel):
    part_id: Optional[str] = None
    clear_part: bool = False
    qty: Optional[float] = None
    source: Optional[str] = None            # stock | order
    route_id: Optional[str] = None
    supplier_id: Optional[str] = None
    lead_days: Optional[int] = None
    price: Optional[float] = None           # ручная цена; 0 — вернуть автоматическую
    currency: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[str] = None


def _line(req: PartRequest, line_id: str) -> PartRequestLine:
    line = next((l for l in req.lines if l.id == line_id), None)
    if line is None:
        raise HTTPException(404, "Строка не найдена")
    return line


@router.patch("/{rid}/lines/{line_id}")
def patch_line(rid: str, line_id: str, body: LinePatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    line = _line(req, line_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("clear_part"):
        line.part_id, line.match_kind = None, ""
    if data.get("part_id"):
        if db.get(Part, data["part_id"]) is None:
            raise HTTPException(422, "Карточка запчасти не найдена")
        line.part_id, line.match_kind = data["part_id"], "manual"
        line.calc = {k: v for k, v in (line.calc or {}).items() if k != "candidates"}
    if "qty" in data:
        if not data["qty"] or data["qty"] <= 0:
            raise HTTPException(422, "Количество должно быть больше нуля")
        line.qty = data["qty"]
    if "source" in data:
        if data["source"] not in ("stock", "order"):
            raise HTTPException(422, "Источник: со склада или под заказ")
        line.source = data["source"]
    for k in ("route_id", "supplier_id", "lead_days", "comment"):
        if k in data:
            setattr(line, k, data[k])
    if "status" in data:
        if data["status"] not in LINE_STATUS:
            raise HTTPException(422, "Неизвестный статус строки")
        line.status = data["status"]
    if "price" in data:
        if data["price"]:
            line.price, line.price_source = data["price"], "manual"
            line.currency = data.get("currency") or line.currency or "RUB"
        else:
            line.price, line.price_source = None, ""
    db.flush()
    _recalc(db, req)
    audit(db, user.id, None, "part_request_line", line.id, "update", None, data)
    db.commit()
    return _payload(db, req, user)


class SplitIn(BaseModel):
    qty: float


@router.post("/{rid}/lines/{line_id}/split")
def split_line(rid: str, line_id: str, body: SplitIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Разделить строку: часть со склада, остаток под заказ (или у другого поставщика). Связь с исходной строкой сохраняется."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    line = _line(req, line_id)
    if not 0 < body.qty < (line.qty or 0):
        raise HTTPException(422, "Отделяемое количество должно быть больше нуля и меньше количества строки")
    line.qty -= body.qty
    req.lines.append(PartRequestLine(no=max(l.no for l in req.lines) + 1, parent_line_id=line.parent_line_id or line.id, raw_article=line.raw_article,
                                     raw_name=line.raw_name, qty=body.qty, unit=line.unit, part_id=line.part_id, match_kind=line.match_kind,
                                     source="order" if line.source == "stock" else "stock"))
    db.flush()
    _recalc(db, req)
    db.commit()
    return _payload(db, req, user)


@router.post("/{rid}/match")
def rematch(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    res = match_lines(db, req)
    _recalc(db, req)
    db.commit()
    return {**_payload(db, req, user), "match_result": res}


@router.post("/{rid}/reprice")
def reprice(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    _recalc(db, req)
    db.commit()
    return _payload(db, req, user)


class DecisionIn(BaseModel):
    comment: str = ""


@router.post("/{rid}/approve")
def approve(rid: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Виза по цене ниже порога маржи: до допуска (5 п. п.) — КД, ниже — ФД (ЗГД — как ФД). ФД получает уведомление о каждой визе КД."""
    req = _req_or_404(db, rid, user)
    if user.has(Role.FD, Role.GD):
        level = "fd"
    elif user.has(Role.CD):
        level = "cd"
    else:
        raise HTTPException(403, "Цену ниже порога утверждают КД или ФД")
    payload = _payload(db, req, Role_all())
    need = payload["summary"]["approval_needed"]
    if not need:
        raise HTTPException(409, "Виза не требуется: все цены не ниже порога маржи или уже утверждены")
    if need == "fd" and level == "cd":
        raise HTTPException(403, "Маржа ниже порога больше чем на допуск — утверждает ФД")
    lines = [{"id": l["id"], "price": l["price"], "margin_pct": l["margin_pct"], "threshold_pct": l["threshold_pct"]}
             for l in payload["lines"] if l.get("approval") and (level == "fd" or l["approval"] == "cd")]
    req.approvals = [*(req.approvals or []), {"level": level, "user_id": user.id, "name": user.display_name, "comment": body.comment,
                                               "at": datetime.now(timezone.utc).isoformat(), "lines": lines}]
    lost = round(sum(((l["threshold_pct"] or 0) - (l["margin_pct"] or 0)) / 100 * (next(x for x in payload["lines"] if x["id"] == l["id"])["sum_rub"] or 0)
                     for l in lines), 2)
    if level == "cd":   # ФД видит все отклонения, в том числе утверждённые КД
        for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
            if "fd" in (u.roles or []):
                notify(db, None, u.id, "parts_margin", None,
                       f"КД утвердил цену ниже порога маржи по заявке {req.number}: строк {len(lines)}, недополучено ≈ {lost:,.0f} ₽".replace(",", " "))
    audit(db, user.id, None, "part_request", req.id, f"approve_{level}", None, {"lines": lines, "lost_margin_rub": lost, "comment": body.comment})
    db.commit()
    return _payload(db, req, user)


class CloseIn(BaseModel):
    closed: str
    reason: str = ""


@router.post("/{rid}/close")
def close(rid: str, body: CloseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    if body.closed not in CLOSE_KINDS:
        raise HTTPException(422, "Итог заявки: проиграна, отменена клиентом или выполнена")
    if body.closed in ("lost", "cancelled") and not body.reason.strip():
        raise HTTPException(422, "Укажите причину")
    req.closed, req.close_reason = body.closed, body.reason
    audit(db, user.id, None, "part_request", req.id, "close", None, body.model_dump())
    db.commit()
    return _payload(db, req, user)


@router.post("/{rid}/reopen")
def reopen(rid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    req = _req_or_404(db, rid, user)
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Нет прав")
    req.closed, req.close_reason = "", ""
    db.commit()
    return _payload(db, req, user)


# ------------------------------------------------------------------ КП (частичная отправка, редакции, мягкий резерв)

class KpIn(BaseModel):
    line_ids: list[str]
    currency: str = "RUB"
    remove_handling_fee: bool = False
    comment: str = ""


@router.post("/{rid}/kp", status_code=201)
def create_kp(rid: str, body: KpIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Редакция КП по выбранным строкам: цены в валюте КП по курсу ЦБ, «Обработка заказа», срок действия, мягкий резерв строк «со склада»."""
    req = _req_or_404(db, rid, user)
    _editable(user, req)
    release_expired(db)
    _recalc(db, req)
    full = _payload(db, req, Role_all())
    chosen = [l for l in full["lines"] if l["id"] in set(body.line_ids)]
    if not chosen:
        raise HTTPException(422, "Отметьте строки, которые войдут в КП")
    bad = [str(l["no"]) for l in chosen if l["price"] is None or l["status"] in ("cancelled", "unavailable")]
    if bad:
        raise HTTPException(422, f"В КП нельзя включить строки без цены или отменённые: {', '.join(bad)}")
    need = approvals_pending(req, chosen)
    if need:
        raise HTTPException(409, f"Есть цены ниже порога маржи — сначала виза {'ФД' if need == 'fd' else 'КД'}")
    fx = latest_fx(db)
    try:
        snap = build_kp_lines(chosen, body.currency, fx)
    except ValueError as e:
        raise HTTPException(422, str(e))
    summ = kp_summary(chosen, settings_service.get(db, "parts_min_margin_kp_rub"))
    rate = 1.0 if body.currency == "RUB" else fx[body.currency]
    fee = round(summ["handling_fee_rub"] / rate, 2)
    removed_by = None
    if fee and body.remove_handling_fee:
        if not user.has(Role.CD, Role.GD):
            raise HTTPException(403, "Убрать «Обработку заказа» может КД или ЗГД")
        removed_by, fee = user.id, 0.0
    prev = db.scalars(select(PartKp).where(PartKp.request_id == req.id).order_by(PartKp.revision_no.desc())).first()
    rev = (prev.revision_no if prev else 0) + 1
    today = date.today()
    kp = PartKp(request_id=req.id, revision_no=rev, number=f"КП-{req.number}/{rev}", currency=body.currency, fx_rate=None if body.currency == "RUB" else rate,
                lines=snap, handling_fee=fee, handling_fee_removed_by=removed_by, total=round(sum(l["sum"] for l in snap) + fee, 2),
                valid_until=today + timedelta(days=int(settings_service.get(db, "parts_kp_validity_days") or 30)),
                changes=diff_kp(prev.lines, snap) if prev else [], comment=body.comment, created_by=user.id)
    db.add(kp)
    db.flush()
    short = []
    for line in req.lines:
        if line.id not in set(body.line_ids):
            continue
        if line.status in ("new", "quoted"):
            line.status = "in_kp"
        if line.source == "stock" and line.part_id:
            got = reserve_soft(db, line, kp.id, kp.valid_until)
            if got < (line.qty or 0):
                short.append(f"{line.no} (зарезервировано {got:g} из {line.qty:g})")
    if removed_by:   # ФД видит каждое снятие «Обработки заказа»
        for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
            if "fd" in (u.roles or []):
                notify(db, None, u.id, "parts_margin", None, f"В КП {kp.number} убрана «Обработка заказа» на {summ['handling_fee_rub']:,.0f} ₽".replace(",", " "))
    audit(db, user.id, None, "part_kp", kp.id, "create", None, {"number": kp.number, "lines": len(snap), "total": kp.total, "currency": kp.currency,
                                                                 "handling_fee_removed": bool(removed_by)})
    db.commit()
    out = _payload(db, req, user)
    out["reserve_short"] = short
    return out


def _kp_or_404(db: Session, kid: str, user: CurrentUser) -> tuple[PartKp, PartRequest]:
    kp = db.get(PartKp, kid)
    if kp is None:
        raise HTTPException(404, "КП не найдено")
    return kp, _req_or_404(db, kp.request_id, user)


@router.get("/kp/{kid}/docx")
def kp_docx(kid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    kp, req = _kp_or_404(db, kid, user)
    client = db.get(Client, req.client_id)
    seller = db.scalar(select(LegalEntity).where(LegalEntity.sells_to_clients.is_(True)))
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    render_docx({"number": kp.number, "date": (kp.created_at or datetime.now(timezone.utc)).strftime("%d.%m.%Y"), "valid_until": kp.valid_until.strftime("%d.%m.%Y"),
                 "currency": kp.currency, "fx_rate": kp.fx_rate, "lines": kp.lines, "handling_fee": kp.handling_fee, "total": kp.total, "machine": req.machine},
                client.name if client else "", path, seller.name if seller else "")
    name = kp.number.replace("/", "-") + ".docx"
    return FileResponse(path, filename=name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        background=BackgroundTask(os.unlink, path))


@router.post("/kp/{kid}/sent")
def kp_sent(kid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    kp, req = _kp_or_404(db, kid, user)
    _editable(user, req)
    kp.sent_at, kp.sent_by = datetime.now(timezone.utc), user.id
    audit(db, user.id, None, "part_kp", kp.id, "sent", None, {"number": kp.number})
    db.commit()
    return _payload(db, req, user)


def Role_all():   # noqa: N802 — полный набор прав для внутренней проверки виз (данные не уходят пользователю)
    return {Role.CD, Role.FD, Role.GD}
