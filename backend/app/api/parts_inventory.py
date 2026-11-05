"""Запчасти: инвентаризация склада (подсчёт с телефона, утверждение КД, корректировка) и пополнение по минимальному остатку."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import Factory, Part, PartInventory, PartInventoryLine, PartStock, PoLine, PurchaseOrder, User, Warehouse
from ..services.deal_service import audit, notify
from ..services.parts_stock import add_stock, suggest_qty
from .common import serialize

router = APIRouter(prefix="/api/parts/stock", tags=["parts-inventory"])
COUNT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING)
APPROVE_ROLES = (Role.CD, Role.GD)
READ_ROLES = (*COUNT_ROLES, Role.FD, Role.LOGISTICIAN, Role.ADMIN)
STATUS = {"counting": "идёт подсчёт", "submitted": "на утверждении КД", "approved": "утверждена, остатки исправлены", "cancelled": "отменена"}


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к складу запчастей")
    return user


def _counter(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*COUNT_ROLES):
        raise HTTPException(403, "Инвентаризацию проводят менеджеры, закупки, КД и ЗГД")
    return user


def _number(db: Session) -> str:
    p = f"ИН-{date.today().year}-"
    last = db.scalars(select(PartInventory.number).where(PartInventory.number.like(f"{p}%"))).all()
    n = max([int(x[len(p):]) for x in last if x[len(p):].isdigit()] or [0]) + 1
    return f"{p}{n:04d}"


def _payload(db: Session, inv: PartInventory, with_lines: bool = True) -> dict:
    d = serialize(inv)
    w = db.get(Warehouse, inv.warehouse_id)
    d["warehouse_name"] = w.name if w else ""
    d["status_title"] = STATUS.get(inv.status, inv.status)
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    d["created_by_name"], d["approved_by_name"] = names.get(inv.created_by, ""), names.get(inv.approved_by, "")
    counted = [l for l in inv.lines if l.counted is not None]
    diff = [l for l in counted if abs((l.counted or 0) - (l.expected or 0)) > 1e-9]
    d["summary"] = {"lines": len(inv.lines), "counted": len(counted), "diff_lines": len(diff),
                    "surplus": round(sum(max(l.counted - l.expected, 0) for l in diff), 4),
                    "shortage": round(sum(max(l.expected - l.counted, 0) for l in diff), 4)}
    if with_lines:
        parts = {p.id: p for p in db.scalars(select(Part).where(Part.id.in_([l.part_id for l in inv.lines] or [""]))).all()}
        d["lines"] = sorted([{**serialize(l), "article": parts[l.part_id].article if l.part_id in parts else "",
                              "name": parts[l.part_id].name_ru if l.part_id in parts else "", "unit": parts[l.part_id].unit if l.part_id in parts else "",
                              "diff": None if l.counted is None else round(l.counted - l.expected, 4)} for l in inv.lines],
                            key=lambda x: (x["cell"] or "яяя", x["article"]))
    return d


# ------------------------------------------------------------------ инвентаризация

class InventoryIn(BaseModel):
    warehouse_id: str
    cell_prefix: str = ""
    part_group: str = ""
    comment: str = ""


@router.post("/inventories", status_code=201)
def create_inventory(body: InventoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_counter)):
    """Задание на подсчёт: все позиции склада с ненулевым остатком (или в ячейках с префиксом / группе товара); учётный остаток — на сейчас."""
    w = db.get(Warehouse, body.warehouse_id)
    if w is None or w.kind != "physical":
        raise HTTPException(422, "Инвентаризация — по физическому складу")
    if db.scalars(select(PartInventory).where(PartInventory.warehouse_id == w.id, PartInventory.status.in_(("counting", "submitted")))).first():
        raise HTTPException(409, f"По складу {w.name} уже идёт инвентаризация — завершите её")
    stmt = select(PartStock, Part).join(Part, Part.id == PartStock.part_id).where(PartStock.warehouse_id == w.id, PartStock.qty != 0)
    if body.cell_prefix.strip():
        stmt = stmt.where(PartStock.cell.ilike(f"{body.cell_prefix.strip()}%"))
    if body.part_group.strip():
        stmt = stmt.where(Part.part_group.ilike(body.part_group.strip()))
    rows = db.execute(stmt).all()
    inv = PartInventory(number=_number(db), warehouse_id=w.id, cell_prefix=body.cell_prefix.strip(), part_group=body.part_group.strip(),
                        comment=body.comment, created_by=user.id)
    db.add(inv)
    for st, part in rows:
        inv.lines.append(PartInventoryLine(part_id=part.id, cell=st.cell or "", expected=float(st.qty or 0)))
    db.flush()
    audit(db, user.id, None, "part_inventory", inv.id, "create", None, {"warehouse": w.name, "lines": len(rows)})
    db.commit()
    return _payload(db, inv)


@router.get("/inventories")
def inventories(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return [_payload(db, i, False) for i in db.scalars(select(PartInventory).order_by(PartInventory.created_at.desc()).limit(100)).all()]


def _inv_or_404(db: Session, iid: str) -> PartInventory:
    inv = db.get(PartInventory, iid)
    if inv is None:
        raise HTTPException(404, "Инвентаризация не найдена")
    return inv


@router.get("/inventories/{iid}")
def inventory(iid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return _payload(db, _inv_or_404(db, iid))


class CountIn(BaseModel):
    line_id: Optional[str] = None
    article: str = ""                 # позиция, которой нет в задании (найдена на полке)
    counted: Optional[float] = Field(default=None, ge=0)
    cell: Optional[str] = None
    comment: Optional[str] = None


class CountsIn(BaseModel):
    lines: list[CountIn]


@router.put("/inventories/{iid}/counts")
def counts(iid: str, body: CountsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_counter)):
    """Подсчёт: количество по строкам (можно частями, с телефона); найденное сверх учёта добавляется по артикулу."""
    inv = _inv_or_404(db, iid)
    if inv.status != "counting":
        raise HTTPException(409, "Подсчёт закрыт")
    by_id = {l.id: l for l in inv.lines}
    by_part = {l.part_id: l for l in inv.lines}
    now = datetime.now(timezone.utc)
    for x in body.lines:
        if x.line_id:
            l = by_id.get(x.line_id)
            if l is None:
                raise HTTPException(422, "Строка не из этой инвентаризации")
        else:
            art = x.article.strip()
            part = db.scalars(select(Part).where(Part.article == art)).first() if art else None
            if part is None:
                raise HTTPException(422, f"Артикул «{art}» не найден в каталоге — заведите карточку")
            l = by_part.get(part.id)
            if l is None:
                st = db.scalars(select(PartStock).where(PartStock.part_id == part.id, PartStock.warehouse_id == inv.warehouse_id)).first()
                l = PartInventoryLine(part_id=part.id, cell=(st.cell if st else "") or "", expected=float(st.qty or 0) if st else 0.0, added=True)
                inv.lines.append(l)
                by_part[part.id] = l
        if x.counted is not None:
            l.counted, l.counted_by, l.counted_at = x.counted, user.id, now
        if x.cell is not None:
            l.cell = x.cell.strip()
        if x.comment is not None:
            l.comment = x.comment
    db.commit()
    return _payload(db, inv)


@router.post("/inventories/{iid}/submit")
def submit(iid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_counter)):
    """Подсчёт завершён → на утверждение КД. Непосчитанные строки — ноль (с подтверждения в интерфейсе)."""
    inv = _inv_or_404(db, iid)
    if inv.status != "counting":
        raise HTTPException(409, "Подсчёт уже закрыт")
    for l in inv.lines:
        if l.counted is None:
            l.counted, l.counted_by, l.counted_at = 0.0, user.id, datetime.now(timezone.utc)
            l.comment = (l.comment + "; " if l.comment else "") + "не найдено при подсчёте"
    inv.status, inv.submitted_by, inv.submitted_at = "submitted", user.id, datetime.now(timezone.utc)
    s = _payload(db, inv, False)["summary"]
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if "cd" in (u.roles or []) and u.id != user.id:
            notify(db, None, u.id, "parts_inventory", None, f"Инвентаризация {inv.number} на утверждение: расхождений {s['diff_lines']} "
                                                             f"(излишек {s['surplus']:g}, недостача {s['shortage']:g})")
    audit(db, user.id, None, "part_inventory", inv.id, "submit", None, s)
    db.commit()
    return _payload(db, inv)


class ApproveIn(BaseModel):
    approve: bool = True               # False — вернуть на пересчёт
    comment: str = ""


@router.post("/inventories/{iid}/approve")
def approve(iid: str, body: ApproveIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """КД (или ЗГД) утверждает: разница «посчитано − учёт на начало» проводится движением «корректировка»; или возвращает на пересчёт."""
    if not user.has(*APPROVE_ROLES):
        raise HTTPException(403, "Инвентаризацию утверждает КД или ЗГД")
    inv = _inv_or_404(db, iid)
    if inv.status != "submitted":
        raise HTTPException(409, "Утверждается инвентаризация после подсчёта")
    if not body.approve:
        inv.status = "counting"
        inv.comment = (inv.comment + "; " if inv.comment else "") + f"возвращена на пересчёт: {body.comment}".strip()
        db.commit()
        return _payload(db, inv)
    for l in inv.lines:
        delta = round((l.counted or 0) - (l.expected or 0), 4)
        if abs(delta) > 1e-9:
            add_stock(db, part_id=l.part_id, warehouse_id=inv.warehouse_id, qty=delta, kind="adjust", doc_number=inv.number, user_id=user.id, cell=l.cell)
        elif l.cell:
            st = db.scalars(select(PartStock).where(PartStock.part_id == l.part_id, PartStock.warehouse_id == inv.warehouse_id)).first()
            if st is not None:
                st.cell = l.cell
    inv.status, inv.approved_by, inv.approved_at = "approved", user.id, datetime.now(timezone.utc)
    if body.comment:
        inv.comment = (inv.comment + "; " if inv.comment else "") + body.comment
    audit(db, user.id, None, "part_inventory", inv.id, "approve", None, _payload(db, inv, False)["summary"])
    db.commit()
    return _payload(db, inv)


@router.post("/inventories/{iid}/cancel")
def cancel(iid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_counter)):
    inv = _inv_or_404(db, iid)
    if inv.status not in ("counting", "submitted"):
        raise HTTPException(409, "Отменить можно незавершённую инвентаризацию")
    inv.status = "cancelled"
    db.commit()
    return _payload(db, inv)


# ------------------------------------------------------------------ пополнение по минимальному остатку

def _incoming(db: Session) -> dict[tuple[str, str], float]:
    """Ожидается по заказам на пополнение склада (строки без заявки клиента): (part_id, buyer_entity_id) → количество."""
    out: dict[tuple[str, str], float] = {}
    rows = db.execute(select(PoLine.part_id, PoLine.qty, PoLine.received_qty).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id).where(
        PurchaseOrder.kind == "external", PurchaseOrder.status.not_in(("closed", "cancelled")), PoLine.request_line_id.is_(None),
        PoLine.status.not_in(("received", "cancelled")))).all()
    for pid, qty, rec in rows:
        out[(pid, "")] = out.get((pid, ""), 0) + max(float(qty or 0) - float(rec or 0), 0)
    return out


@router.get("/replenishment")
def replenishment(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Позиции ниже минимального остатка (свободный остаток + ожидаемое по заказам на склад < минимум) — по поставщикам."""
    inc = _incoming(db)
    rows = db.execute(select(PartStock, Part).join(Part, Part.id == PartStock.part_id).where(PartStock.min_qty.is_not(None), PartStock.min_qty > 0)).all()
    whs = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    groups: dict[str, dict] = {}
    for st, part in rows:
        free = float(st.qty or 0) - float(st.reserved or 0)
        incoming = inc.get((part.id, ""), 0.0)
        q = suggest_qty(st.min_qty, free, incoming, part.moq, part.multiple)
        if q <= 0:
            continue
        sup = db.get(Factory, part.supplier_id) if part.supplier_id else None
        key = part.supplier_id or ""
        g = groups.setdefault(key, {"supplier_id": part.supplier_id, "supplier_name": (sup.short_name or sup.name) if sup else "поставщик не указан", "lines": []})
        g["lines"].append({"part_id": part.id, "article": part.article, "name": part.name_ru, "unit": part.unit, "warehouse": whs.get(st.warehouse_id, ""),
                           "warehouse_id": st.warehouse_id, "qty": st.qty, "reserved": st.reserved, "free": round(free, 4), "incoming": incoming,
                           "min_qty": st.min_qty, "suggest": q, "moq": part.moq, "multiple": part.multiple,
                           "price": part.purchase_price if user.can("factory_price_history") else None, "currency": part.purchase_currency,
                           "priced": bool(part.purchase_price)})
    return sorted(groups.values(), key=lambda g: (not g["supplier_id"], g["supplier_name"]))


class MinIn(BaseModel):
    part_id: str
    warehouse_id: str
    min_qty: Optional[float] = Field(default=None, ge=0)


@router.put("/min")
def set_min(body: MinIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Минимальный остаток позиции на складе (задаётся вручную; пусто — не контролировать)."""
    if not user.has(Role.CD, Role.GD, Role.PURCHASING, Role.MANAGER):
        raise HTTPException(403, "Минимальный остаток задают менеджеры, закупки, КД и ЗГД")
    if db.get(Part, body.part_id) is None or db.get(Warehouse, body.warehouse_id) is None:
        raise HTTPException(404, "Позиция или склад не найдены")
    st = db.scalars(select(PartStock).where(PartStock.part_id == body.part_id, PartStock.warehouse_id == body.warehouse_id)).first()
    if st is None:
        st = PartStock(part_id=body.part_id, warehouse_id=body.warehouse_id, qty=0, reserved=0)
        db.add(st)
    before = st.min_qty
    st.min_qty = body.min_qty or None
    audit(db, user.id, None, "part_stock", body.part_id, "min_qty", before, st.min_qty)
    db.commit()
    return serialize(st)
