"""Склад запчастей: приёмка по заказу поставщику, отклонения, жёсткий резерв, отгрузка и движения (ТЗ запчастей, разделы 7–8).

Остаток склада — PartStock.qty: снимок из переноса МойСклад плюс движения модуля (PartStockMove).
Принятое под заявку клиента сразу резервируется под неё (жёсткий резерв, без срока); отгрузка снимает резерв.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

DISCREPANCY = {"short": "недопоставка", "over": "излишек", "wrong": "пересорт", "damaged": "брак"}
DECISIONS = {"wait": "ждать допоставку", "replace": "замена поставщиком", "cancel": "отменить остаток", "accept": "принять как есть"}
DEV_STATUS = {"open": "открыто", "claim": "претензия поставщику", "closed": "закрыто"}


def deviations_for(remaining: float, qty: float, qty_bad: float, discrepancy: str = "") -> list[tuple[str, float]]:
    """Отклонения строки приёмки: недопоставка (пришло меньше ожидаемого), излишек, брак или пересорт (qty_bad)."""
    out: list[tuple[str, float]] = []
    remaining, qty, qty_bad = max(float(remaining), 0.0), float(qty or 0), float(qty_bad or 0)
    if qty_bad > 0:
        out.append(("wrong" if discrepancy == "wrong" else "damaged", qty_bad))
    if qty + qty_bad < remaining:
        out.append(("short", round(remaining - qty - qty_bad, 4)))
    elif qty > remaining:
        out.append(("over", round(qty - remaining, 4)))
    return out


# ------------------------------------------------------------------ БД

def add_stock(db, *, part_id: str, warehouse_id: str, qty: float, kind: str, doc_number: str, user_id: Optional[str],
              cell: str = "", receipt_line_id: Optional[str] = None, shipment_line_id: Optional[str] = None):
    """Движение остатка и обновление PartStock по складу (создаётся при первой приёмке)."""
    from sqlalchemy import select
    from ..models import PartStock, PartStockMove
    st = db.scalars(select(PartStock).where(PartStock.part_id == part_id, PartStock.warehouse_id == warehouse_id)).first()
    if st is None:
        st = PartStock(part_id=part_id, warehouse_id=warehouse_id, qty=0, reserved=0)
        db.add(st)
    st.qty = round(float(st.qty or 0) + float(qty), 4)
    if cell:
        st.cell = cell
    st.as_of = datetime.now(timezone.utc)
    db.add(PartStockMove(part_id=part_id, warehouse_id=warehouse_id, qty=qty, kind=kind, doc_number=doc_number, user_id=user_id,
                         receipt_line_id=receipt_line_id, shipment_line_id=shipment_line_id))
    db.flush()
    return st


def hard_reserved(db, line_id: str, warehouse_id: Optional[str] = None) -> float:
    from sqlalchemy import func, select
    from ..models import PartReservation
    q = select(func.coalesce(func.sum(PartReservation.qty), 0)).where(PartReservation.line_id == line_id, PartReservation.kind == "hard",
                                                                      PartReservation.released_at.is_(None))
    if warehouse_id:
        q = q.where(PartReservation.warehouse_id == warehouse_id)
    return float(db.scalar(q) or 0)


def free_qty(db, part_id: str, warehouse_id: str) -> float:
    from sqlalchemy import select
    from ..models import PartStock
    st = db.scalars(select(PartStock).where(PartStock.part_id == part_id, PartStock.warehouse_id == warehouse_id)).first()
    return float((st.qty or 0) - (st.reserved or 0)) if st else 0.0


def reserve_hard(db, line, warehouse_id: str, qty: float) -> float:
    """Жёсткий резерв под строку заявки на складе — не больше свободного остатка и не больше, чем осталось отгрузить."""
    from ..models import PartReservation
    from .parts_kp import recompute_reserved
    need = float(line.qty or 0) - float(line.shipped_qty or 0) - hard_reserved(db, line.id)
    q = min(float(qty), need, free_qty(db, line.part_id, warehouse_id))
    if q <= 0:
        return 0.0
    db.add(PartReservation(part_id=line.part_id, warehouse_id=warehouse_id, line_id=line.id, qty=round(q, 4), kind="hard"))
    db.flush()
    recompute_reserved(db, [line.part_id])
    return q


def harden_soft(db, line) -> float:
    """Заказ клиента: мягкий резерв КП становится жёстким (без срока). Если мягкого нет — резерв со складов со свободным остатком."""
    from sqlalchemy import select
    from ..models import PartReservation, PartStock
    from .parts_kp import recompute_reserved
    got = 0.0
    for r in db.scalars(select(PartReservation).where(PartReservation.line_id == line.id, PartReservation.kind == "soft",
                                                      PartReservation.released_at.is_(None))).all():
        r.kind, r.expires_at = "hard", None
        got += r.qty
    db.flush()
    if got < float(line.qty or 0) - float(line.shipped_qty or 0):
        stocks = sorted(db.scalars(select(PartStock).where(PartStock.part_id == line.part_id)).all(),
                        key=lambda s: (s.qty or 0) - (s.reserved or 0), reverse=True)
        for s in stocks:
            got += reserve_hard(db, line, s.warehouse_id, float(line.qty) - got)
    recompute_reserved(db, [line.part_id])
    return got


def consume_reservation(db, line, warehouse_id: str, qty: float) -> None:
    """Отгрузка: жёсткий резерв строки на складе уменьшается на отгруженное (полностью использованный — снимается)."""
    from sqlalchemy import select
    from ..models import PartReservation
    from .parts_kp import recompute_reserved
    left = float(qty)
    now = datetime.now(timezone.utc)
    for r in db.scalars(select(PartReservation).where(PartReservation.line_id == line.id, PartReservation.warehouse_id == warehouse_id,
                                                      PartReservation.released_at.is_(None)).order_by(PartReservation.kind)).all():
        if left <= 0:
            break
        take = min(r.qty, left)
        r.qty = round(r.qty - take, 4)
        left -= take
        if r.qty <= 0:
            r.qty, r.released_at = take, now      # в истории — сколько было снято
    db.flush()
    recompute_reserved(db, [line.part_id])
