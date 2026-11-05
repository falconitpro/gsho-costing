"""Запчасти: отчёты первой очереди (ТЗ запчастей, раздел 11).

Продажи и маржа (по клиентам, группам товара, поставщикам), сервис (доля строк со склада, срок до КП), точность сроков поставщиков,
неликвиды и оборачиваемость, запрашиваемые, но отсутствующие позиции, гарантийные претензии, история отгрузок.
Себестоимость и маржу видят роли с доступом к себестоимости (КД, ФД, ЗГД).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import (Client, Factory, Part, PartKp, PartReceipt, PartReceiptLine, PartRequest, PartRequestLine, PartShipment, PartShipmentLine,
                      PartStock, PartStockMove, PoLine, PurchaseOrder, Warehouse, WarrantyClaim)
from ..services.parts_requests import norm_article

router = APIRouter(prefix="/api/parts/reports", tags=["parts-reports"])
READ_ROLES = (Role.MANAGER, Role.CD, Role.FD, Role.GD, Role.PURCHASING, Role.SERVICE_HEAD, Role.LOGISTICIAN, Role.ADMIN)


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*READ_ROLES):
        raise HTTPException(403, "Нет доступа к отчётам по запчастям")
    return user


def _period(date_from: Optional[date], date_to: Optional[date]) -> tuple[date, date]:
    to = date_to or date.today()
    return (date_from or to - timedelta(days=365)), to


def _dt(d: date, end: bool = False) -> datetime:
    return datetime.combine(d + timedelta(days=1) if end else d, datetime.min.time(), tzinfo=timezone.utc)


@router.get("/sales")
def sales(by: str = "client", date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db),
          user: CurrentUser = Depends(_reader)):
    """Отгружено клиентам за период: выручка по цене строки заявки, себестоимость и маржа (по клиентам, группам товара, поставщикам)."""
    if by not in ("client", "group", "supplier"):
        raise HTTPException(422, "Разрез: client, group или supplier")
    f, t = _period(date_from, date_to)
    rows = db.execute(select(PartShipmentLine, PartRequestLine, PartShipment).join(PartShipment, PartShipment.id == PartShipmentLine.shipment_id)
                      .join(PartRequestLine, PartRequestLine.id == PartShipmentLine.request_line_id)
                      .where(PartShipment.shipped_on >= f, PartShipment.shipped_on <= t)).all()
    see_cost = user.can("cost")
    agg: dict[str, dict] = defaultdict(lambda: {"qty": 0.0, "revenue_rub": 0.0, "cost_rub": 0.0, "uncosted": 0, "lines": 0})
    names: dict[str, str] = {}
    for sl, rl, sh in rows:
        calc = rl.calc or {}
        part = db.get(Part, sl.part_id)
        if by == "client":
            req = db.get(PartRequest, rl.request_id)
            key = req.client_id if req else ""
            if key not in names:
                c = db.get(Client, key) if key else None
                names[key] = c.name if c else "—"
        elif by == "group":
            key = (part.part_group if part else "") or "без группы"
            names[key] = key
        else:
            key = rl.supplier_id or (part.supplier_id if part else "") or ""
            if key not in names:
                s = db.get(Factory, key) if key else None
                names[key] = (s.short_name or s.name) if s else "поставщик не указан"
        a = agg[key]
        a["qty"] += sl.qty
        a["lines"] += 1
        a["revenue_rub"] += float(calc.get("price_rub") or 0) * sl.qty
        if calc.get("cost") is not None:
            a["cost_rub"] += float(calc["cost"]) * sl.qty
        else:
            a["uncosted"] += 1
    out = []
    for k, a in agg.items():
        row = {"key": k, "name": names.get(k, k), "lines": a["lines"], "qty": round(a["qty"], 3), "revenue_rub": round(a["revenue_rub"], 2)}
        if see_cost:
            row.update(cost_rub=round(a["cost_rub"], 2), margin_rub=round(a["revenue_rub"] - a["cost_rub"], 2),
                       margin_pct=round((a["revenue_rub"] - a["cost_rub"]) / a["revenue_rub"] * 100, 1) if a["revenue_rub"] else None, uncosted=a["uncosted"])
        out.append(row)
    out.sort(key=lambda x: -x["revenue_rub"])
    total = {"revenue_rub": round(sum(x["revenue_rub"] for x in out), 2)}
    if see_cost:
        total["margin_rub"] = round(sum(x["margin_rub"] for x in out), 2)
        total["margin_pct"] = round(total["margin_rub"] / total["revenue_rub"] * 100, 1) if total["revenue_rub"] else None
    return {"from": f, "to": t, "by": by, "rows": out, "total": total}


@router.get("/service")
def service(date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Заявки за период: доля строк со склада, средний срок до первого КП, доля строк, ушедших в КП."""
    f, t = _period(date_from, date_to)
    reqs = db.scalars(select(PartRequest).where(PartRequest.created_at >= _dt(f), PartRequest.created_at < _dt(t, True))).all()
    lines = stock = in_kp = 0
    hours = []
    for r in reqs:
        for l in r.lines:
            if l.status == "cancelled":
                continue
            lines += 1
            stock += l.source == "stock"
            in_kp += l.status not in ("new", "rfq", "quoted", "unavailable", "replaced")
        first = db.scalar(select(func.min(PartKp.created_at)).where(PartKp.request_id == r.id))
        if first:
            hours.append((first - r.created_at).total_seconds() / 3600)
    return {"from": f, "to": t, "requests": len(reqs), "lines": lines, "from_stock_pct": round(stock / lines * 100, 1) if lines else None,
            "in_kp_pct": round(in_kp / lines * 100, 1) if lines else None, "kp_count": len(hours),
            "avg_hours_to_kp": round(sum(hours) / len(hours), 1) if hours else None,
            "urgent": sum(1 for r in reqs if r.urgent)}


@router.get("/lead-accuracy")
def lead_accuracy(date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Точность сроков поставщиков: дата «на складе» при заказе против фактической приёмки (строки, принятые за период)."""
    f, t = _period(date_from, date_to)
    rows = db.execute(select(PoLine, PurchaseOrder, func.max(PartReceipt.received_on)).join(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
                      .join(PartReceiptLine, PartReceiptLine.po_line_id == PoLine.id).join(PartReceipt, PartReceipt.id == PartReceiptLine.receipt_id)
                      .where(PoLine.status == "received", PurchaseOrder.kind == "external")
                      .group_by(PoLine.id, PurchaseOrder.id)).all()
    agg: dict[str, dict] = defaultdict(lambda: {"lines": 0, "on_time": 0, "delays": []})
    for pl, po, received in rows:
        if not received or received < f or received > t:
            continue
        planned = next((h.get("eta") for h in pl.history or [] if h.get("eta")), None)
        if not planned:
            continue
        delay = (received - date.fromisoformat(planned)).days
        a = agg[po.supplier_id]
        a["lines"] += 1
        a["on_time"] += delay <= 0
        a["delays"].append(delay)
    out = []
    for sid, a in agg.items():
        s = db.get(Factory, sid)
        late = [d for d in a["delays"] if d > 0]
        out.append({"supplier_id": sid, "supplier_name": (s.short_name or s.name) if s else "", "lines": a["lines"],
                    "on_time_pct": round(a["on_time"] / a["lines"] * 100, 1), "avg_delay_days": round(sum(a["delays"]) / len(a["delays"]), 1),
                    "max_delay_days": max(late) if late else 0})
    return {"from": f, "to": t, "rows": sorted(out, key=lambda x: x["on_time_pct"])}


@router.get("/stock")
def stock(idle_days: int = 180, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Неликвиды и оборачиваемость: остаток без движения дольше idle_days; отгружено за год к текущему остатку."""
    today = date.today()
    year_ago = _dt(today - timedelta(days=365))
    last = dict(db.execute(select(PartStockMove.part_id, func.max(PartStockMove.at)).group_by(PartStockMove.part_id)).all())
    shipped = dict(db.execute(select(PartStockMove.part_id, func.sum(-PartStockMove.qty)).where(PartStockMove.kind == "shipment", PartStockMove.at >= year_ago)
                              .group_by(PartStockMove.part_id)).all())
    whs = {w.id: w.name for w in db.scalars(select(Warehouse)).all()}
    see_cost = user.can("cost") or user.can("factory_price_history")
    idle, turn = [], []
    for st, p in db.execute(select(PartStock, Part).join(Part, Part.id == PartStock.part_id).where(PartStock.qty > 0)).all():
        moved = last.get(p.id) or st.as_of
        days = (today - moved.date()).days if moved else None
        row = {"part_id": p.id, "article": p.article, "name": p.name_ru, "group": p.part_group, "warehouse": whs.get(st.warehouse_id, ""), "qty": st.qty,
               "idle_days": days, "shipped_year": round(float(shipped.get(p.id) or 0), 3)}
        if see_cost and p.purchase_price:
            row["purchase_value"] = round(p.purchase_price * st.qty, 2)
            row["currency"] = p.purchase_currency
        row["turnover"] = round(row["shipped_year"] / st.qty, 2) if st.qty else None
        if days is None or days >= idle_days:
            idle.append(row)
        turn.append(row)
    idle.sort(key=lambda x: -(x["idle_days"] or 10 ** 6))
    turn.sort(key=lambda x: -(x["turnover"] or 0))
    return {"idle_days": idle_days, "idle": idle[:500], "idle_count": len(idle), "top_turnover": turn[:100], "positions": len(turn)}


@router.get("/missing")
def missing(date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Запрашиваемые, но отсутствующие на складе позиции — кандидаты в складской ассортимент."""
    f, t = _period(date_from, date_to)
    rows = db.execute(select(PartRequestLine, PartRequest).join(PartRequest, PartRequest.id == PartRequestLine.request_id)
                      .where(PartRequest.created_at >= _dt(f), PartRequest.created_at < _dt(t, True), PartRequestLine.status != "cancelled")).all()
    agg: dict[str, dict] = {}
    for l, r in rows:
        if l.part_id and l.source == "stock":
            continue
        part = db.get(Part, l.part_id) if l.part_id else None
        key = part.id if part else norm_article(l.raw_article) or l.raw_name.strip().lower()
        if not key:
            continue
        a = agg.setdefault(key, {"part_id": part.id if part else None, "article": part.article if part else l.raw_article, "name": part.name_ru if part else l.raw_name,
                                 "in_catalog": bool(part), "requests": set(), "clients": set(), "qty": 0.0})
        a["requests"].add(r.id)
        a["clients"].add(r.client_id)
        a["qty"] += l.qty
    out = [{**{k: v for k, v in a.items() if k not in ("requests", "clients")}, "requests": len(a["requests"]), "clients": len(a["clients"]),
            "qty": round(a["qty"], 3)} for a in agg.values()]
    out.sort(key=lambda x: (-x["requests"], -x["qty"]))
    return {"from": f, "to": t, "rows": out[:300]}


@router.get("/warranty")
def warranty(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    """Гарантийные претензии по заводам: доля признанных и средний срок компенсации."""
    agg: dict[str, dict] = defaultdict(lambda: {"claims": 0, "accepted": 0, "rejected": 0, "answered": 0, "days": [], "cost_rub": 0.0, "compensated_rub": 0.0})
    for c in db.scalars(select(WarrantyClaim).where(WarrantyClaim.status != "draft")).all():
        a = agg[c.supplier_id]
        a["claims"] += 1
        if c.status in ("accepted", "partial", "compensated") or (c.status == "closed" and c.compensations):
            a["accepted"] += 1
        if c.status == "rejected" or (c.status == "closed" and c.answered_at and not c.compensations):
            a["rejected"] += 1
        a["answered"] += bool(c.answered_at)
        a["cost_rub"] += sum(float(l.get("cost_rub") or 0) * float(l.get("qty") or 1) for l in c.lines or [])
        got = [x for x in c.compensations or [] if x.get("received_on")]
        a["compensated_rub"] += sum(float(x.get("amount_rub") or 0) for x in got)
        if got and c.sent_at:
            a["days"].append((max(date.fromisoformat(x["received_on"]) for x in got) - c.sent_at.date()).days)
    out = []
    for sid, a in agg.items():
        s = db.get(Factory, sid)
        out.append({"supplier_id": sid, "supplier_name": (s.short_name or s.name) if s else "", "claims": a["claims"], "answered": a["answered"],
                    "accepted_pct": round(a["accepted"] / a["answered"] * 100, 1) if a["answered"] else None, "rejected": a["rejected"],
                    "avg_days_to_compensation": round(sum(a["days"]) / len(a["days"]), 1) if a["days"] else None,
                    **({"cost_rub": round(a["cost_rub"], 2), "compensated_rub": round(a["compensated_rub"], 2)} if user.can("cost") else {})})
    return {"rows": sorted(out, key=lambda x: -x["claims"])}


@router.get("/shipments")
def shipments(date_from: Optional[date] = None, date_to: Optional[date] = None, client_id: str = "", db: Session = Depends(get_db),
              user: CurrentUser = Depends(_reader)):
    """История отгрузок клиентам по строкам."""
    f, t = _period(date_from, date_to)
    stmt = select(PartShipment).where(PartShipment.shipped_on >= f, PartShipment.shipped_on <= t).order_by(PartShipment.shipped_on.desc())
    out = []
    for s in db.scalars(stmt).all():
        req = db.get(PartRequest, s.request_id)
        if client_id and (not req or req.client_id != client_id):
            continue
        cl = db.get(Client, req.client_id) if req else None
        w = db.get(Warehouse, s.warehouse_id)
        for l in s.lines:
            p = db.get(Part, l.part_id)
            rl = db.get(PartRequestLine, l.request_line_id)
            out.append({"shipped_on": s.shipped_on, "number": s.number, "request_number": req.number if req else "", "client_name": cl.name if cl else "",
                        "warehouse": w.name if w else "", "article": p.article if p else "", "name": p.name_ru if p else "", "qty": l.qty,
                        "sum_rub": round(float((rl.calc or {}).get("price_rub") or 0) * l.qty, 2) if rl else None})
    return {"from": f, "to": t, "rows": out[:2000]}
