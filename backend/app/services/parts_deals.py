"""Заявки на запчасти как сделки (единый раздел «Сделки»): строки для воронки, доски, рабочего места и денег.
Этап и блок — по статусам строк (parts_pricing.request_stage); видимость — как у заявок (менеджер видит свои)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import Role
from ..models import Client, PartRequest, User
from .parts_pricing import STAGE_TITLES, request_stage

CLOSE_KINDS = {"lost": "проиграна", "cancelled": "отменена клиентом", "done": "выполнена"}


def can_see(user: CurrentUser, req: PartRequest) -> bool:
    if user.has(Role.CD, Role.FD, Role.GD, Role.PURCHASING, Role.SERVICE_HEAD, Role.LOGISTICIAN, Role.ADMIN, Role.OBSERVER):
        return True
    return user.has(Role.MANAGER) and user.is_manager_of(req.manager_id)


def rows(db: Session, user: CurrentUser, include_closed: bool = True) -> list[dict]:
    if not user.has(Role.MANAGER, Role.CD, Role.FD, Role.GD, Role.PURCHASING, Role.SERVICE_HEAD, Role.LOGISTICIAN, Role.ADMIN, Role.OBSERVER):
        return []
    stmt = select(PartRequest).order_by(PartRequest.created_at.desc())
    if not include_closed:
        stmt = stmt.where(PartRequest.closed == "")
    clients = {c.id: c.name for c in db.scalars(select(Client)).all()}
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out = []
    for r in db.scalars(stmt).all():
        if not can_see(user, r):
            continue
        lines = [{"status": l.status, "sum_rub": (l.calc or {}).get("sum_rub"), "shipped_qty": l.shipped_qty, "qty": l.qty} for l in r.lines]
        st = request_stage(lines, r.closed)
        active = [l for l in lines if l["status"] not in ("cancelled", "unavailable", "replaced")]
        ordered = [l for l in active if l["status"] in ("ordered", "po", "transit", "in_stock")]
        out.append({"id": r.id, "number": r.number, "kind": "parts", "client_id": r.client_id, "client_name": clients.get(r.client_id, ""), "machine": r.machine, "urgent": r.urgent,
                    "manager_id": r.manager_id, "manager_name": names.get(r.manager_id or "", ""), "created_at": r.created_at, "updated_at": r.updated_at, "closed": r.closed,
                    "lines": len(active), **st, "status_text": CLOSE_KINDS.get(r.closed) or st["stage_title"],
                    "price_rub": round(sum(l["sum_rub"] or 0 for l in active), 2),
                    "ordered_rub": round(sum(l["sum_rub"] or 0 for l in ordered), 2),            # заказано клиентом, ещё не отгружено — ожидаемые поступления
                    "url": f"/deals/parts/{r.id}"})
    return out


def as_of(r: dict) -> date:
    v = r.get("updated_at") or r.get("created_at")
    return v.date() if isinstance(v, datetime) else (v or date.today())


__all__ = ["rows", "can_see", "as_of", "STAGE_TITLES", "CLOSE_KINDS"]
