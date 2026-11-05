"""Запчасти, шаг 5б: заказы поставщикам, внутригрупповые заказы по маршруту, подтверждение и статусы поставки по строкам."""
from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import Client, Factory, LegalEntity, Part, PartRequest, PartRequestLine, PoLine, PurchaseOrder, SupplierRfqLine, SupplyRoute
from ..services.deal_service import audit, notify
from ..services.markup_chain import ChainError, simulate
from ..services.parts_po import (ORDERABLE, PO_LINE_STATUS, PO_STATUS, REQUEST_STATUS, build_po_xlsx, can_move, eta, intragroup_steps,
                                 po_status)
from ..services.parts_requests import _route_steps
from ..services.suppliers import mark as mark_supplier
from ..services.parts_supplier import CARD_KEY, supplier_language
from .common import serialize
from .parts_rfq import send_supplier_file
from .parts_setup import current_policy, latest_fx

router = APIRouter(prefix="/api/parts/po", tags=["parts-po"])
EDIT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING)
READ_ROLES = (*EDIT_ROLES, Role.FD, Role.LOGISTICIAN, Role.ADMIN)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к заказам поставщикам")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Заказы поставщикам ведут менеджеры, закупки, КД и ЗГД")
    return user


def _can_edit_lines(user: CurrentUser) -> bool:
    return user.has(*EDIT_ROLES, Role.LOGISTICIAN)        # статусы поставки ведёт и логист


def _next_number(db: Session) -> str:
    prefix = f"ЗК-{date.today().year}-"
    last = db.scalars(select(PurchaseOrder.number).where(PurchaseOrder.number.like(f"{prefix}%"), PurchaseOrder.kind == "external")).all()
    n = max([int(x[len(prefix):]) for x in last if x[len(prefix):].isdigit()] or [0]) + 1
    return f"{prefix}{n:04d}"


def _entity_names(db: Session) -> dict[str, str]:
    return {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}


def _ru_entity(db: Session) -> Optional[LegalEntity]:
    return db.scalars(select(LegalEntity).where(LegalEntity.sells_to_clients.is_(True), LegalEntity.is_active.is_(True))).first() \
        or db.scalars(select(LegalEntity).where(LegalEntity.code == "ru")).first()


def _transit(db: Session, route_id: Optional[str]) -> int:
    r = db.get(SupplyRoute, route_id) if route_id else None
    return sum(s.transit_days or 0 for s in r.steps) if r else 0


def _payload(db: Session, po: PurchaseOrder, user: CurrentUser, names: Optional[dict] = None, children: bool = True) -> dict:
    names = names or _entity_names(db)
    d = serialize(po)
    sup = db.get(Factory, po.supplier_id) if po.supplier_id else None
    d["seller_name"] = (sup.short_name or sup.name) if sup else names.get(po.seller_entity_id, "")
    d["buyer_name"] = names.get(po.buyer_entity_id, "")
    d["status_title"] = PO_STATUS.get(po.status, po.status)
    r = db.get(SupplyRoute, po.route_id) if po.route_id else None
    d["route_name"] = r.name if r else ""
    lines = []
    for l in po.lines:
        x = serialize(l)
        x["status_title"] = PO_LINE_STATUS.get(l.status, l.status)
        rl = db.get(PartRequestLine, l.request_line_id) if l.request_line_id else None
        if rl:
            req = db.get(PartRequest, rl.request_id)
            cl = db.get(Client, req.client_id) if req else None
            x["request_id"], x["request_number"], x["client_name"] = req.id, req.number, (cl.name if cl else "")
        lines.append(x)
    d["lines"] = lines
    if children and po.kind == "external":
        d["intragroup"] = [_payload(db, c, user, names, False) for c in
                           db.scalars(select(PurchaseOrder).where(PurchaseOrder.parent_po_id == po.id).order_by(PurchaseOrder.step_no)).all()]
    # цены внешнего заказа видят те, кому открыты цены завода; цены внутри группы (наценки компаний) — те, кому открыта себестоимость
    hide = not user.can("cost") if po.kind == "intragroup" else not user.can("factory_price_history")
    if hide:
        for k in ("total", "payments"):
            d.pop(k, None)
        for x in d["lines"]:
            x.pop("price", None)
        d["prices_hidden"] = True
    return d


@router.get("/meta")
def meta(user: CurrentUser = Depends(_reader)):
    return {"status": PO_STATUS, "line_status": PO_LINE_STATUS, "can_edit": user.has(*EDIT_ROLES), "can_track": _can_edit_lines(user)}


# ------------------------------------------------------------------ к заказу: строки заявок с выбранным поставщиком

def _ordered_line_ids(db: Session) -> set[str]:
    rows = db.execute(select(PoLine.request_line_id).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
                      .where(PurchaseOrder.kind == "external", PoLine.status != "cancelled", PoLine.request_line_id.is_not(None))).all()
    return {r[0] for r in rows}


def _unit_price(db: Session, line: PartRequestLine, part: Optional[Part]) -> tuple[Optional[float], str, Optional[int]]:
    """Цена поставщика за единицу: выбранный ответ на запрос, иначе закупочная цена из карточки (если поставщик тот же)."""
    if line.quote_price:
        q = db.get(SupplierRfqLine, line.rfq_line_id) if line.rfq_line_id else None
        return line.quote_price, line.quote_currency, (q.lead_days if q else None)
    if part and part.purchase_price and (not part.supplier_id or part.supplier_id == line.supplier_id):
        return part.purchase_price, part.purchase_currency or "EUR", part.lead_days
    return None, "", None


@router.get("/candidates")
def candidates(db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Строки заявок «под заказ» с выбранным поставщиком, ещё не заказанные, — по поставщикам."""
    done = _ordered_line_ids(db)
    rows = db.scalars(select(PartRequestLine).where(PartRequestLine.supplier_id.is_not(None), PartRequestLine.status.in_(ORDERABLE),
                                                    PartRequestLine.part_id.is_not(None))).all()
    out: dict[str, dict] = {}
    for l in rows:
        if l.id in done or l.source == "stock":
            continue
        req = db.get(PartRequest, l.request_id)
        if req is None or req.closed or (user.has(Role.MANAGER) and not user.has(Role.CD, Role.GD, Role.PURCHASING) and req.manager_id != user.id):
            continue
        part = db.get(Part, l.part_id)
        price, cur, lead = _unit_price(db, l, part)
        sup = db.get(Factory, l.supplier_id)
        g = out.setdefault(l.supplier_id, {"supplier_id": l.supplier_id, "supplier_name": (sup.short_name or sup.name) if sup else "", "lines": []})
        cl = db.get(Client, req.client_id)
        g["lines"].append({"id": l.id, "request_id": req.id, "request_number": req.number, "client_name": cl.name if cl else "", "urgent": req.urgent,
                           "article": part.article, "name": part.name_ru, "qty": l.qty, "unit": l.unit, "status": l.status,
                           "price": price if user.can("factory_price_history") else None, "currency": cur, "lead_days": lead, "route_id": l.route_id,
                           "priced": price is not None})
    return sorted(out.values(), key=lambda g: g["supplier_name"])


# ------------------------------------------------------------------ создание заказа и внутригрупповых заказов

class StockLineIn(BaseModel):
    part_id: str
    qty: float = Field(gt=0)
    price: Optional[float] = None
    currency: str = ""
    warranty_claim_id: Optional[str] = None      # гарантийная замена по претензии — цена 0
    comment: str = ""


class PoCreateIn(BaseModel):
    supplier_id: str
    request_line_ids: list[str] = Field(default_factory=list)
    stock_lines: list[StockLineIn] = Field(default_factory=list)      # пополнение склада
    route_id: Optional[str] = None                                    # для строк пополнения (по умолчанию — маршрут поставщика)
    planned_ship_date: Optional[date] = None
    comment: str = ""


@router.post("", status_code=201)
def create(body: PoCreateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Заказ поставщику по выбранным строкам: отдельный заказ на каждый маршрут и валюту; по ступеням маршрута — внутригрупповые заказы."""
    sup = db.get(Factory, body.supplier_id)
    if sup is None:
        raise HTTPException(404, "Поставщик не найден")
    if not body.request_line_ids and not body.stock_lines:
        raise HTTPException(422, "Отметьте строки для заказа")
    mark_supplier(db, sup.id, "parts")
    done = _ordered_line_ids(db)
    lang = supplier_language(sup.country, sup.language)
    today = date.today()
    items: list[dict] = []
    for lid in dict.fromkeys(body.request_line_ids):
        l = db.get(PartRequestLine, lid)
        if l is None:
            raise HTTPException(404, "Строка заявки не найдена")
        req = db.get(PartRequest, l.request_id)
        if req.closed:
            raise HTTPException(409, f"Заявка {req.number} закрыта")
        if user.has(Role.MANAGER) and not user.has(Role.CD, Role.GD, Role.PURCHASING) and req.manager_id != user.id:
            raise HTTPException(403, f"Заявку {req.number} ведёт другой менеджер")
        if l.supplier_id != sup.id:
            raise HTTPException(422, f"{req.number}, строка {l.no}: выбран другой поставщик")
        if l.id in done:
            raise HTTPException(409, f"{req.number}, строка {l.no}: уже в заказе поставщику")
        if l.status not in ORDERABLE:
            raise HTTPException(422, f"{req.number}, строка {l.no}: статус «{l.status}» — заказать нельзя")
        part = db.get(Part, l.part_id) if l.part_id else None
        if part is None:
            raise HTTPException(422, f"{req.number}, строка {l.no}: не подобрана карточка")
        price, cur, lead = _unit_price(db, l, part)
        if price is None:
            raise HTTPException(422, f"{req.number}, строка {l.no}: нет цены поставщика — выберите ответ на запрос")
        items.append({"line": l, "part": part, "qty": l.qty, "unit": l.unit, "price": price, "currency": cur,
                      "lead": lead if lead is not None else part.lead_days, "route_id": l.route_id or sup.default_route_id or part.route_id})
    for s in body.stock_lines:
        part = db.get(Part, s.part_id)
        if part is None:
            raise HTTPException(404, "Карточка не найдена")
        price = 0.0 if s.warranty_claim_id else (s.price if s.price is not None else part.purchase_price)
        if not price and not s.warranty_claim_id:
            raise HTTPException(422, f"{part.article}: укажите цену поставщика")
        items.append({"line": None, "part": part, "qty": s.qty, "unit": part.unit or "шт", "price": price, "warranty_claim_id": s.warranty_claim_id,
                      "comment": s.comment or ("гарантийная замена" if s.warranty_claim_id else ""),
                      "currency": s.currency or part.purchase_currency or sup.default_currency or "CNY", "lead": part.lead_days,
                      "route_id": body.route_id or sup.default_route_id or part.route_id})

    pol = current_policy(db)
    rules, fx, names = (pol.rules if pol else {}), latest_fx(db), _entity_names(db)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        groups[(it["route_id"], it["currency"])].append(it)
    created: list[PurchaseOrder] = []
    ru = _ru_entity(db)
    for (route_id, cur), its in groups.items():
        steps = _route_steps(db, route_id)
        route = db.get(SupplyRoute, route_id) if route_id else None
        if steps and steps[0]["currency"] != cur:
            steps = [{**steps[0], "currency": cur}, *steps[1:]]
        transit = _transit(db, route_id)
        po = PurchaseOrder(number=_next_number(db), kind="external", step_no=1, route_id=route_id, supplier_id=sup.id, currency=cur,
                           buyer_entity_id=steps[0]["buyer_entity_id"] if steps else (ru.id if ru else None),
                           incoterms=steps[0]["incoterms"] if steps else "", language=lang, planned_ship_date=body.planned_ship_date,
                           comment=body.comment, created_by=user.id)
        db.add(po)
        db.flush()
        child_lines: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
        for i, it in enumerate(its, start=1):
            part = it["part"]
            when = eta(today, it["lead"], transit)
            pl = PoLine(no=i, request_line_id=it["line"].id if it["line"] else None, part_id=part.id,
                        number=next((c.number for c in part.cross_refs if c.kind == "supplier" and c.supplier_id == sup.id), None) or part.oem_number or part.article,
                        name=part.name_ru, name_foreign=(part.names or {}).get(CARD_KEY.get(lang, ""), "") or "", qty=it["qty"], unit=it["unit"],
                        price=round(float(it["price"]), 2), lead_days=it["lead"], eta=when,
                        history=[{"at": datetime.now(timezone.utc).isoformat(), "status": "ordered", "eta": when.isoformat(), "by": user.id}],
                        warranty_claim_id=it.get("warranty_claim_id"), comment=it.get("comment") or "")
            po.lines.append(pl)
            db.flush()
            if it["line"]:
                rl = it["line"]
                rl.status, rl.eta = "po", when
            if len(steps) > 1 and not it["price"]:       # гарантийная замена: по ступеням маршрута — тоже по нулевой цене
                for no, st in enumerate(steps[1:], start=2):
                    child_lines[no].append(({"no": no, "seller_entity_id": st["seller_entity_id"], "buyer_entity_id": st["buyer_entity_id"],
                                             "currency": st["currency"], "incoterms": st.get("incoterms") or "", "price": 0.0}, {"pl": pl, "it": it}))
            elif len(steps) > 1:
                try:
                    sim = simulate(it["price"], steps, rules, fx=fx, part_group=part.part_group or None, supplier_id=sup.id, entity_names=names)
                except ChainError as e:
                    raise HTTPException(422, f"{part.article}: внутригрупповые цены не рассчитаны — {e}")
                for st in intragroup_steps(sim):
                    child_lines[st["no"]].append((st, {"pl": pl, "it": it}))
        po.total = round(sum(l.price * l.qty for l in po.lines), 2)
        for no, pairs in sorted(child_lines.items()):
            st = pairs[0][0]
            child = PurchaseOrder(number=f"{po.number}/{no}", kind="intragroup", parent_po_id=po.id, step_no=no, route_id=route_id,
                                  seller_entity_id=st["seller_entity_id"], buyer_entity_id=st["buyer_entity_id"], currency=st["currency"],
                                  incoterms=st["incoterms"], language="ru", status="confirmed", confirmed_at=datetime.now(timezone.utc),
                                  comment=f"Внутригрупповой заказ к {po.number}" + (f", маршрут {route.name}" if route else ""), created_by=user.id)
            db.add(child)
            for j, (s, x) in enumerate(pairs, start=1):
                pl = x["pl"]
                child.lines.append(PoLine(no=j, request_line_id=pl.request_line_id, parent_line_id=pl.id, part_id=pl.part_id, number=x["it"]["part"].article,
                                          name=pl.name, qty=pl.qty, unit=pl.unit, price=s["price"], lead_days=pl.lead_days, eta=pl.eta, status=pl.status))
            child.total = round(sum(l.price * l.qty for l in child.lines), 2)
        created.append(po)
    db.flush()
    audit(db, user.id, None, "purchase_order", created[0].id, "create", None, {"numbers": [p.number for p in created], "supplier": sup.name})
    db.commit()
    return [_payload(db, p, user, names) for p in created]


# ------------------------------------------------------------------ список, карточка, файл, отправка

@router.get("")
def list_pos(open_only: bool = True, supplier_id: str = "", request_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    stmt = select(PurchaseOrder).where(PurchaseOrder.kind == "external").order_by(PurchaseOrder.created_at.desc())
    if open_only:
        stmt = stmt.where(PurchaseOrder.status.not_in(("closed", "cancelled")))
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    names = _entity_names(db)
    out = []
    for po in db.scalars(stmt).all():
        if request_id:
            ids = [l.request_line_id for l in po.lines if l.request_line_id]
            if not any(getattr(db.get(PartRequestLine, i), "request_id", None) == request_id for i in ids):
                continue
        d = _payload(db, po, user, names)
        etas = [l.eta for l in po.lines if l.eta and l.status not in ("received", "cancelled")]
        d["eta"] = min(etas).isoformat() if etas else None
        d["overdue"] = any(l.eta and l.eta < date.today() and l.status not in ("received", "cancelled") for l in po.lines)
        d["lines_count"] = len(po.lines)
        out.append(d)
    return out


def _po_or_404(db: Session, pid: str) -> PurchaseOrder:
    po = db.get(PurchaseOrder, pid)
    if po is None:
        raise HTTPException(404, "Заказ не найден")
    return po


@router.get("/{pid}")
def card(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return _payload(db, _po_or_404(db, pid), user)


def _file(db: Session, po: PurchaseOrder) -> str:
    names = _entity_names(db)
    sup = db.get(Factory, po.supplier_id) if po.supplier_id else None
    seller = sup.name if sup else names.get(po.seller_entity_id, "")
    d = tempfile.mkdtemp()
    return build_po_xlsx(os.path.join(d, f"{po.number.replace('/', '-')}.xlsx"), number=po.number, seller=seller, buyer=names.get(po.buyer_entity_id, ""),
                         lang=po.language, currency=po.currency, incoterms=po.incoterms,
                         ship_date=po.planned_ship_date.strftime("%d.%m.%Y") if po.planned_ship_date else "",
                         lines=[serialize(l) for l in po.lines if l.status != "cancelled"])


@router.get("/{pid}/xlsx")
def po_xlsx(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    po = _po_or_404(db, pid)
    if not user.can("cost" if po.kind == "intragroup" else "factory_price_history"):
        raise HTTPException(403, "Файл заказа содержит цены — нет доступа")
    path = _file(db, po)
    return FileResponse(path, filename=os.path.basename(path), background=BackgroundTask(os.unlink, path))


class SendIn(BaseModel):
    via_mail: bool = True
    via_wecom: bool = True


@router.post("/{pid}/send")
def send(pid: str, body: SendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    po = _po_or_404(db, pid)
    if po.kind != "external":
        raise HTTPException(422, "Внутригрупповой заказ поставщику не отправляется")
    if po.status == "cancelled":
        raise HTTPException(409, "Заказ отменён")
    sup = db.get(Factory, po.supplier_id)
    path = _file(db, po)
    subj = f"Заказ {po.number}"
    try:
        results = send_supplier_file(sup, subj, path, f"{subj}: строк {len(po.lines)}, сумма {po.total:,.2f} {po.currency}. Файл отправлен на почту.",
                                     po.number, body.via_mail, body.via_wecom, roles=("rfq", "sales"))
    finally:
        os.unlink(path)
    now = datetime.now(timezone.utc)
    po.send_log = [*(po.send_log or []), *({**r, "at": now.isoformat()} for r in results)]
    if any(r["ok"] for r in results):
        po.sent_at = po.sent_at or now
        if po.status == "draft":
            po.status = "sent"
    audit(db, user.id, None, "purchase_order", po.id, "send", None, {"results": results})
    db.commit()
    return {**_payload(db, po, user), "send_results": results}


@router.post("/{pid}/mark-sent")
def mark_sent(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    po = _po_or_404(db, pid)
    now = datetime.now(timezone.utc)
    po.sent_at = po.sent_at or now
    if po.status == "draft":
        po.status = "sent"
    po.send_log = [*(po.send_log or []), {"at": now.isoformat(), "via": "manual", "ok": True}]
    db.commit()
    return _payload(db, po, user)


# ------------------------------------------------------------------ подтверждение, график оплат, статусы поставки

class PaymentIn(BaseModel):
    due: date
    amount: float = Field(gt=0)
    comment: str = ""
    paid_on: Optional[date] = None


class ConfirmIn(BaseModel):
    supplier_ref: str = ""
    planned_ship_date: Optional[date] = None
    payments: Optional[list[PaymentIn]] = None
    prices: dict[str, float] = Field(default_factory=dict)        # поставщик изменил цену при подтверждении: {po_line_id: price}
    comment: str = ""


def _set_eta(db: Session, pl: PoLine, new: Optional[date], user: CurrentUser, reason: str) -> None:
    """Новая ожидаемая дата строки: в строку заявки и во внутригрупповые строки; менеджеру заявки — уведомление."""
    if new is None or pl.eta == new:
        return
    old = pl.eta
    pl.eta = new
    pl.history = [*(pl.history or []), {"at": datetime.now(timezone.utc).isoformat(), "status": pl.status, "eta": new.isoformat(), "by": user.id,
                                        "comment": reason}]
    for c in db.scalars(select(PoLine).where(PoLine.parent_line_id == pl.id)).all():
        c.eta = new
    rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
    if rl:
        rl.eta = new
        req = db.get(PartRequest, rl.request_id)
        if req and req.manager_id and req.manager_id != user.id and old and new > old:
            notify(db, None, req.manager_id, "parts_eta", None,
                   f"{req.number}, строка {rl.no} ({pl.number}): срок поставки сдвинулся с {old:%d.%m.%Y} на {new:%d.%m.%Y}. {reason}".strip())


@router.post("/{pid}/confirm")
def confirm(pid: str, body: ConfirmIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Подтверждение поставщика: номер подтверждения/счёта, плановая дата отгрузки, график оплат; строки — «подтверждено»."""
    po = _po_or_404(db, pid)
    if po.kind != "external" or po.status in ("cancelled", "closed"):
        raise HTTPException(409, "Подтвердить можно открытый заказ поставщику")
    po.supplier_ref = body.supplier_ref or po.supplier_ref
    if body.payments is not None:
        if not user.can("factory_price_history"):
            raise HTTPException(403, "График оплат ведут те, кому открыты цены завода")
        po.payments = [p.model_dump(mode="json") for p in body.payments]
    for pl in po.lines:
        if pl.id in body.prices and user.can("factory_price_history"):
            pl.price = round(float(body.prices[pl.id]), 2)
        if pl.status == "ordered":
            pl.status = "confirmed"
            pl.history = [*(pl.history or []), {"at": datetime.now(timezone.utc).isoformat(), "status": "confirmed", "by": user.id}]
    if body.planned_ship_date:
        po.planned_ship_date = body.planned_ship_date
        r = db.get(SupplyRoute, po.route_id) if po.route_id else None
        transit = sum(s.transit_days or 0 for s in r.steps) if r else 0
        for pl in po.lines:
            if pl.status not in ("cancelled", "received"):
                _set_eta(db, pl, eta(body.planned_ship_date, 0, transit), user, "дата отгрузки по подтверждению поставщика")
    po.total = round(sum(l.price * l.qty for l in po.lines if l.status != "cancelled"), 2)
    po.confirmed_at = po.confirmed_at or datetime.now(timezone.utc)
    po.status = po_status("confirmed" if po.status in ("draft", "sent") else po.status, [l.status for l in po.lines])
    _sync(db, po)
    audit(db, user.id, None, "purchase_order", po.id, "confirm", None, body.model_dump(mode="json"))
    db.commit()
    return _payload(db, po, user)


class LinesStatusIn(BaseModel):
    line_ids: list[str]
    status: Optional[str] = None
    eta: Optional[date] = None
    actual_date: Optional[date] = None        # фактическая дата отгрузки поставщиком
    comment: str = ""


def _sync(db: Session, po: PurchaseOrder) -> None:
    """Статусы строк → внутригрупповые строки и строки заявок клиентов; статус заказа — по строкам."""
    for pl in po.lines:
        for c in db.scalars(select(PoLine).where(PoLine.parent_line_id == pl.id)).all():
            c.status = pl.status
        rl = db.get(PartRequestLine, pl.request_line_id) if pl.request_line_id else None
        if rl and rl.status not in ("cancelled", "shipped"):
            rl.status = REQUEST_STATUS.get(pl.status, "quoted" if pl.status == "cancelled" else rl.status)
            if pl.status == "cancelled":
                rl.eta = None
    po.status = po_status(po.status, [l.status for l in po.lines])
    for c in db.scalars(select(PurchaseOrder).where(PurchaseOrder.parent_po_id == po.id)).all():
        c.status = po.status if po.status in ("closed", "cancelled", "in_progress") else c.status
        c.total = round(sum(l.price * l.qty for l in c.lines if l.status != "cancelled"), 2)


@router.post("/{pid}/lines/status")
def lines_status(pid: str, body: LinesStatusIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Статус поставки и/или новая ожидаемая дата по отмеченным строкам заказа."""
    if not _can_edit_lines(user):
        raise HTTPException(403, "Статусы поставки ведут менеджеры, закупки, логист, КД и ЗГД")
    po = _po_or_404(db, pid)
    if po.kind != "external":
        raise HTTPException(422, "Статусы ведутся по заказу поставщику — внутригрупповые обновятся сами")
    if po.status == "draft" and body.status and body.status != "cancelled":
        raise HTTPException(409, "Сначала отправьте заказ поставщику")
    if body.status and body.status not in PO_LINE_STATUS:
        raise HTTPException(422, "Неизвестный статус")
    if body.status == "received":
        raise HTTPException(422, "«На складе» ставится приёмкой: «Склад» → «Приёмка»")
    sel = [l for l in po.lines if l.id in set(body.line_ids)]
    if not sel:
        raise HTTPException(422, "Отметьте строки")
    now = datetime.now(timezone.utc).isoformat()
    for pl in sel:
        if body.status and body.status != pl.status:
            if not can_move(pl.status, body.status):
                raise HTTPException(409, f"Строка {pl.no}: из «{PO_LINE_STATUS[pl.status]}» в «{PO_LINE_STATUS[body.status]}» нельзя")
            pl.status = body.status
            pl.history = [*(pl.history or []), {"at": now, "status": body.status, "by": user.id, "comment": body.comment}]
        if body.eta:
            _set_eta(db, pl, body.eta, user, body.comment)
    if body.actual_date and body.status == "shipped":
        po.actual_ship_date = body.actual_date
    _sync(db, po)
    audit(db, user.id, None, "purchase_order", po.id, "lines_status", None, body.model_dump(mode="json"))
    db.commit()
    return _payload(db, po, user)


@router.post("/{pid}/cancel")
def cancel(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Отмена заказа до отгрузки: строки заявок возвращаются к заказу, внутригрупповые заказы отменяются."""
    po = _po_or_404(db, pid)
    if po.kind != "external":
        raise HTTPException(422, "Отменяется заказ поставщику целиком")
    if any(l.status not in ("ordered", "confirmed", "cancelled") for l in po.lines):
        raise HTTPException(409, "Часть строк уже отгружена поставщиком — отменяйте строки по отдельности")
    for pl in po.lines:
        pl.status = "cancelled"
    _sync(db, po)
    po.status = "cancelled"
    for c in db.scalars(select(PurchaseOrder).where(PurchaseOrder.parent_po_id == po.id)).all():
        c.status = "cancelled"
    audit(db, user.id, None, "purchase_order", po.id, "cancel", None, None)
    db.commit()
    return _payload(db, po, user)
