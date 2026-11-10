"""Правила внутригрупповых цепочек по умолчанию (D-164…D-167).

Менеджер выбирает только конечного поставщика. Через какую компанию группы идёт закупка (Альянс-Импекс в Китае,
компания в Киргизии и т. п.) — решают правила, которые ведут ФД и ЗГД (настройка «intragroup_rules»):

    [{"id", "kind": "gsho" | "parts" | "service" | "*", "country": "CN" | "*", "supplier_id": "" | <id>,
      "route_id": <маршрут> | null (напрямую от Берг-Хауса), "note"}]

Подходящее правило — самое точное: поставщик → страна → «любая страна»; вид сделки — точный, затем «*».
Маршрут правила задаёт цепочку (поставщик → компания группы → … → компания РФ); компания группы, от имени которой
идёт запрос, — покупатель первой ступени маршрута, она должна быть заведена поставщиком с типом «компания группы».
Нет правила — запрос напрямую, как раньше. В сделке ФД или ЗГД может поменять цепочку и до КП должен её утвердить.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Factory, LegalEntity, SupplyRoute
from . import settings_service

KEY = "intragroup_rules"
KINDS = {"gsho": "ГШО", "parts": "Запчасти", "service": "Сервис", "*": "любая сделка"}


def rules(db: Session) -> list[dict]:
    return list(settings_service.get(db, KEY) or [])


def validate(db: Session, rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, r in enumerate(rows, 1):
        kind = (r.get("kind") or "*").strip()
        if kind not in KINDS:
            raise HTTPException(422, f"Правило {i}: вид сделки — ГШО, запчасти, сервис или любая")
        country = (r.get("country") or "*").strip().upper() or "*"
        sid = (r.get("supplier_id") or "").strip()
        if sid:
            sup = db.get(Factory, sid)
            if sup is None:
                raise HTTPException(422, f"Правило {i}: поставщик не найден")
            if sup.supplier_status == "group_company":
                raise HTTPException(422, f"Правило {i}: «{sup.name}» — компания группы, а правило задаётся для конечного поставщика")
        rid = r.get("route_id") or None
        if rid:
            route = db.get(SupplyRoute, rid)
            if route is None or not route.is_active:
                raise HTTPException(422, f"Правило {i}: маршрут не найден или не активен")
            group_factory_for(db, route, i)
        key = (kind, country, sid)
        if key in seen:
            raise HTTPException(422, f"Правило {i}: повторяет другое правило (тот же вид сделки, страна и поставщик)")
        seen.add(key)
        out.append({"id": r.get("id") or uuid.uuid4().hex[:8], "kind": kind, "country": country, "supplier_id": sid, "route_id": rid, "note": (r.get("note") or "").strip()[:200]})
    return out


def save(db: Session, rows: list[dict], user_id: str) -> list[dict]:
    clean = validate(db, rows)
    settings_service.set_value(db, KEY, clean, user_id, "правила внутригрупповых цепочек")
    return clean


def group_factory_for(db: Session, route: SupplyRoute, n: Optional[int] = None) -> Factory:
    """Компания группы — покупатель первой ступени маршрута, заведённая поставщиком с типом «компания группы»."""
    steps = list(route.steps)
    where = f"Правило {n}: " if n else ""
    if len(steps) < 2:
        raise HTTPException(422, f"{where}маршрут «{route.name}» без компании группы — для поставки напрямую выберите «напрямую»")
    ent = db.get(LegalEntity, steps[0].buyer_entity_id)
    f = db.scalars(select(Factory).where(Factory.supplier_status == "group_company", Factory.legal_entity_id == steps[0].buyer_entity_id,
                                         Factory.is_active.is_(True)).order_by(Factory.created_at.desc())).first()   # одна на юрлицо; при дублях — последняя заведённая
    if f is None:
        raise HTTPException(422, f"{where}«{ent.name if ent else 'компания группы'}» не заведена поставщиком с типом «компания группы» — "
                                 "заведите её в «Поставщиках» и привяжите юрлицо")
    return f


def match(db: Session, supplier: Factory, kind: str) -> Optional[dict]:
    """Самое точное правило для поставщика и вида сделки (или None — правил нет, запрос напрямую)."""
    country = (supplier.country or "").upper()
    best, score = None, -1
    for r in rules(db):
        if r["kind"] not in (kind, "*"):
            continue
        if r["supplier_id"] and r["supplier_id"] != supplier.id:
            continue
        if not r["supplier_id"] and r["country"] not in (country, "*"):
            continue
        sc = (4 if r["supplier_id"] else 2 if r["country"] != "*" else 0) + (1 if r["kind"] == kind else 0)
        if sc > score:
            best, score = r, sc
    return best


def resolve(db: Session, supplier: Factory, kind: str) -> tuple[Optional[SupplyRoute], Optional[Factory], Optional[dict]]:
    """Маршрут и компания группы по правилу; (None, None, правило|None) — напрямую."""
    r = match(db, supplier, kind)
    if r is None or not r.get("route_id"):
        return None, None, r
    route = db.get(SupplyRoute, r["route_id"])
    if route is None or not route.is_active:
        return None, None, r
    return route, group_factory_for(db, route), r


def describe(db: Session, r: Optional[dict]) -> str:
    if r is None:
        return "правил нет — напрямую"
    route = db.get(SupplyRoute, r["route_id"]) if r.get("route_id") else None
    sup = db.get(Factory, r["supplier_id"]) if r.get("supplier_id") else None
    who = (sup.short_name or sup.name) if sup else ("любая страна" if r["country"] == "*" else r["country"])
    return f"правило «{KINDS.get(r['kind'], r['kind'])} · {who}» → {route.name if route else 'напрямую'}"


def require_fd_gd(user) -> None:
    from ..core.roles import Role
    if not user.has(Role.FD, Role.GD):
        raise HTTPException(403, "Внутреннюю цепочку меняют и утверждают ФД или ЗГД")


def signature(chain: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(chain, key=lambda x: json.dumps(x, sort_keys=True)), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approval_record(user, chain: list[dict]) -> dict:
    return {"sig": signature(chain), "by": user.id, "by_name": getattr(user, "display_name", ""), "at": datetime.now(timezone.utc).isoformat(), "chain": chain}


def approval_state(stored: Optional[dict], chain: list[dict]) -> dict[str, Any]:
    """Нужна ли виза: цепочка есть и не совпадает с утверждённой."""
    if not chain:
        return {"needed": False, "approved": False, "stale": False}
    ok = bool(stored) and stored.get("sig") == signature(chain)
    return {"needed": not ok, "approved": ok, "stale": bool(stored) and not ok, "by_name": (stored or {}).get("by_name"), "at": (stored or {}).get("at")}
