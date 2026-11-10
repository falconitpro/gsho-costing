"""Зеркальные запросы цен на запчасти через компанию группы (D-156…D-159), продолжение D-150 для запчастей.

Запрос поставщику-компании группы (Factory.supplier_status = group_company, например «Альянс-Импекс») с выбранными
конечными поставщиками даёт по каждому поставщику X пару запросов SupplierRfq по тем же строкам заявки:

    U — предложение компании группы Берг-Хаусу: supplier_id = компания группы, end_supplier_id = X, route_id.
        Его строки участвуют в сравнении и выборе; цена строки — цена последней ступени маршрута (Берг-Хаусу).
    L — запрос компании группы поставщику X: supplier_id = X, mirror_of_id = U. Файл на языке X уходит
        с почты и WeCom компании группы; ответ X вносится в L (вручную или из файла).

Ответ X пересчитывает строки U по маршруту и политике наценок группы на запчасти (markup_chain.simulate, режим parts:
наценка по группе запчастей и поставщику). Выбор строки U ставит в строку заявки поставщика X, маршрут через компанию
группы и цену X — себестоимость и заказы поставщику (внешний X → компания группы и внутригрупповые) считаются
существующим механизмом маршрута и совпадают с ценой компании группы. Если ответ X меняется после выбора, цена
в заявке не меняется: в строке U ставится отметка «ждёт решения», КД получает уведомление; повторный выбор принимает
новую цену.

Цена X и наценки видны при праве «Цепочка компаний группы» (КД, ФД, ЗГД, логист); менеджер и закупки видят только
цену компании группы.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Factory, LegalEntity, Part, PartRequestLine, SupplierRfq, SupplierRfqLine, SupplyRoute
from .markup_chain import ChainError, simulate
from .mirror_offers import _policy, _steps, group_entity, pick_route

STATE = {"waiting": "ждём ответ поставщика", "ok": "рассчитано", "no_policy": "нет наценок группы", "error": "ошибка расчёта",
         "changed_after_select": "ответ изменился после выбора"}


def is_upper(rfq: Optional[SupplierRfq]) -> bool:
    return bool(rfq and rfq.end_supplier_id and not rfq.mirror_of_id)


def is_lower(rfq: Optional[SupplierRfq]) -> bool:
    return bool(rfq and rfq.mirror_of_id)


def lower_of(db: Session, up: SupplierRfq) -> Optional[SupplierRfq]:
    return db.scalar(select(SupplierRfq).where(SupplierRfq.mirror_of_id == up.id))


def upper_of(db: Session, low: SupplierRfq) -> Optional[SupplierRfq]:
    return db.get(SupplierRfq, low.mirror_of_id) if low.mirror_of_id else None


def resolve(db: Session, group_factory: Factory, supplier_ids: list[str]) -> tuple[LegalEntity, list[tuple[Factory, SupplyRoute]]]:
    """Компания группы и пары (конечный поставщик, маршрут через неё). Ошибки — до создания записей."""
    from .suppliers import require_approved
    entity = group_entity(db, group_factory)
    if entity is None:
        raise HTTPException(422, f"«{group_factory.short_name or group_factory.name}» не привязана к юрлицу группы — привяжите в карточке поставщика")
    if not supplier_ids:
        raise HTTPException(422, f"Выберите конечных поставщиков для запроса через «{entity.name}»")
    out = []
    for sid in dict.fromkeys(supplier_ids):
        sup = db.get(Factory, sid)
        if sup is None:
            raise HTTPException(404, "Поставщик не найден")
        if sup.supplier_status == "group_company":
            raise HTTPException(422, f"«{sup.short_name or sup.name}» — компания группы, а не конечный поставщик")
        require_approved(sup, "запросить у него цену")
        out.append((sup, pick_route(db, entity, sup)))
    return entity, out


def mirror_lines(up: SupplierRfq, low: SupplierRfq) -> list[tuple[SupplierRfqLine, Optional[SupplierRfqLine]]]:
    by_no = {l.no: l for l in low.lines}
    return [(ul, by_no.get(ul.no)) for ul in up.lines]


def _chosen(db: Session, ul: SupplierRfqLine) -> Optional[PartRequestLine]:
    return db.scalar(select(PartRequestLine).where(PartRequestLine.rfq_line_id == ul.id))


def refresh(db: Session, up: SupplierRfq) -> list[dict]:
    """Строки U из ответа L: наличие, срок, партия, аналог — как у X; цена — по маршруту с наценками группы.
    Возвращает изменения по уже выбранным строкам (цена в заявке не меняется)."""
    from .parts_supplier import rfq_status
    low = lower_of(db, up)
    route = db.get(SupplyRoute, up.route_id) if up.route_id else None
    if low is None or route is None:
        up.mirror_state, up.chain = "error", {"error": "нет запроса поставщику или маршрута"}
        return []
    policy, fx = _policy(db)
    names = {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    steps = _steps(route)
    changed: list[dict] = []
    states: set[str] = set()
    for ul, ll in mirror_lines(up, low):
        if ll is None or not ll.availability:
            ul.availability, ul.price, ul.currency, ul.answered_at = "", None, "", None
            ul.chain = {}
            states.add("waiting")
            continue
        for k in ("availability", "lead_days", "moq", "analog_number", "comment", "answered_at"):
            setattr(ul, k, getattr(ll, k))
        new_price, new_cur, chain = None, "", {"supplier_price": ll.price, "supplier_currency": ll.currency}
        if ll.availability != "none" and ll.price:
            part = db.get(Part, ll.part_id) if ll.part_id else None
            st = [{**steps[0], "currency": ll.currency or steps[0]["currency"]}, *steps[1:]]
            try:
                if policy is None:
                    raise ChainError("Нет утверждённой политики наценок группы")
                sim = simulate(float(ll.price), st, policy.rules or {}, fx=fx, part_group=(part.part_group if part else None) or None,
                               supplier_id=low.supplier_id, entity_names=names)
                last = sim["steps"][-1]
                new_price, new_cur = last["price"], last["currency"]
                chain.update({"steps": sim["steps"], "companies": sim["companies"], "warnings": sim["warnings"],
                              "purchase_rub": sim["purchase_rub"], "group_margin_rub": sim["intragroup_markup_rub"]})
                states.add("ok")
            except ChainError as e:
                chain["error"] = str(e)
                states.add("no_policy" if policy is None or "наценк" in str(e) else "error")
        chosen = _chosen(db, ul)
        if chosen is not None and (new_price, new_cur) != (ul.price, ul.currency):
            chain["pending"] = {"price": new_price, "currency": new_cur, "at": datetime.now(timezone.utc).isoformat()}
            changed.append({"no": ul.no, "old": ul.price, "new": new_price, "currency": new_cur or ul.currency})
            states.add("changed_after_select")
        else:
            ul.price, ul.currency = new_price, new_cur
        ul.chain = chain
    for s in ("changed_after_select", "error", "no_policy", "waiting", "ok"):
        if s in states:
            up.mirror_state = s
            break
    up.chain = {"route": route.name, "policy_version": policy.version_no if policy else None,
                "fx": {k: v for k, v in fx.items() if k in {s["currency"] for s in steps} | {l.currency for l in low.lines if l.currency}}}
    if up.status != "closed":
        up.status = rfq_status([{"availability": l.availability} for l in up.lines]) or ("sent" if low.status != "draft" else "draft")
    return changed


def accept_pending(ul: SupplierRfqLine) -> None:
    """Повторный выбор строки U принимает изменённую цену X."""
    p = (ul.chain or {}).get("pending")
    if p:
        ul.price, ul.currency = p.get("price"), p.get("currency") or ul.currency
        ul.chain = {k: v for k, v in (ul.chain or {}).items() if k != "pending"}


def view(db: Session, rfq: SupplierRfq, can_chain: bool) -> Optional[dict[str, Any]]:
    """Сведения о зеркальном запросе для списка и карточки; цепочка — только при праве intragroup_chain."""
    if not (is_upper(rfq) or is_lower(rfq)):
        return None
    up = rfq if is_upper(rfq) else upper_of(db, rfq)
    if up is None:
        return None
    group = db.get(Factory, up.supplier_id)
    end = db.get(Factory, up.end_supplier_id) if up.end_supplier_id else None
    low = rfq if is_lower(rfq) else lower_of(db, up)
    route = db.get(SupplyRoute, up.route_id) if up.route_id else None
    d: dict[str, Any] = {"role": "upper" if rfq is up else "lower", "group_company": (group.short_name or group.name) if group else "",
                         "end_supplier": (end.short_name or end.name) if end else "", "upper_id": up.id, "lower_id": low.id if low else None,
                         "lower_number": low.number if low else "", "upper_number": up.number,
                         "state": up.mirror_state, "state_title": STATE.get(up.mirror_state, up.mirror_state), "route": route.name if route else ""}
    if can_chain:
        d["policy_version"] = (up.chain or {}).get("policy_version")
        d["fx"] = (up.chain or {}).get("fx")
    return d


def strip_chain(payload: dict) -> dict:
    for l in payload.get("lines", []) or []:
        l.pop("chain", None)
    payload.pop("chain", None)
    return payload


def mask_lines(db: Session, lines: list[dict], user) -> None:
    """Строки заявки, выбранные через компанию группы: отметка «через …»; без права «Цепочка компаний группы» —
    поставщик и цена компании группы вместо поставщика X и его цены."""
    from ..core.roles import READ, can
    chain = can(user, "intragroup_chain", READ)
    for d in lines:
        ul = db.get(SupplierRfqLine, d.get("rfq_line_id")) if d.get("rfq_line_id") else None
        up = db.get(SupplierRfq, ul.rfq_id) if ul else None
        if not is_upper(up):
            continue
        group = db.get(Factory, up.supplier_id)
        end = db.get(Factory, up.end_supplier_id)
        d["via_group"] = {"group_company": (group.short_name or group.name) if group else "", "end_supplier": (end.short_name or end.name) if end else "",
                          "price": ul.price, "currency": ul.currency, "rfq_number": up.number, "pending": bool((ul.chain or {}).get("pending"))}
        if not chain:
            d["supplier_id"], d["quote_price"], d["quote_currency"] = up.supplier_id, ul.price, ul.currency


def on_supplier_change(db: Session, line: PartRequestLine, data: dict) -> dict:
    """Ручная смена поставщика в строке, выбранной через компанию группы: та же компания группы или тот же поставщик X —
    без изменений; другой поставщик — связь с предложением компании группы снимается (цена, маршрут — заново)."""
    ul = db.get(SupplierRfqLine, line.rfq_line_id) if line.rfq_line_id else None
    up = db.get(SupplierRfq, ul.rfq_id) if ul else None
    if not is_upper(up):
        return data
    if data.get("supplier_id") in (up.supplier_id, up.end_supplier_id):
        return {k: v for k, v in data.items() if k != "supplier_id"}
    line.rfq_line_id, line.quote_price, line.quote_currency = None, None, ""
    if "route_id" not in data:
        line.route_id = None
    return data
