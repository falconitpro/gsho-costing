"""Зеркальные запросы ГШО через компанию группы (D-150…D-155).

Запрос поставщику-компании группы (Factory.supplier_status = group_company, например «Альянс-Импекс») с выбранными
конечными поставщиками даёт по каждому поставщику X пару записей FactoryOffer в той же сделке:

    U  — предложение компании группы Берг-Хаусу: factory_id = компания группы, end_supplier_id = X, route_id, chain.
         Оно и есть «ответ завода» в сравнении и отборе; его цена — «Цена завода» в расчёте сделки.
    L  — запрос компании группы поставщику X: factory_id = X, mirror_of_id = U. Отправляется с почты и WeCom
         компании группы; ответ X вносится в L (вручную или импортом).

Ответ в L пересчитывает U по маршруту поставки и политике наценок группы (markup_chain.simulate, режим gsho):
цена X → наценка каждой компании группы на её ступени → цена последней ступени Берг-Хаусу, в валюте и на условиях
маршрута. Параметры, срок, фото — из ответа X. После отбора U цена в сделке молча не меняется: изменение ответа X
помечается (mirror_state = changed_after_select) с уведомлением КД.

Зеркальные записи — внутри основной сделки, отдельных сделок не создаётся. Цена X, наценка и маржа группы видны
при праве «Цепочка компаний группы» (КД, ФД, ЗГД, логист); остальные видят только предложение компании группы.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..models import Deal, Factory, FactoryOffer, FactoryPriceHistory, FeedEntry, LegalEntity, Position, SupplyRoute
from .deal_service import audit, notify
from .markup_chain import ChainError, simulate

COPY_FIELDS = ("model", "basis", "production_term", "factory_payments", "params", "images")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def group_entity(db: Session, factory: Optional[Factory]) -> Optional[LegalEntity]:
    """Компания группы, если «поставщик» — она (supplier_status = group_company с привязанным юрлицом)."""
    if factory is None or factory.supplier_status != "group_company" or not factory.legal_entity_id:
        return None
    return db.get(LegalEntity, factory.legal_entity_id)


def is_mirror(o: Optional[FactoryOffer]) -> bool:
    return bool(o and (o.mirror_of_id or o.end_supplier_id))


def pick_route(db: Session, entity: LegalEntity, supplier: Factory) -> SupplyRoute:
    """Маршрут: первая ступень — покупатель компания группы, последняя — компания, продающая клиенту.
    При нескольких — совпадающий по стране поставщика, затем самый короткий."""
    cands = []
    for r in db.scalars(select(SupplyRoute).where(SupplyRoute.is_active.is_(True))).all():
        st = list(r.steps)
        if len(st) < 2 or st[0].buyer_entity_id != entity.id:
            continue
        last = db.get(LegalEntity, st[-1].buyer_entity_id)
        if last is None or not last.sells_to_clients:
            continue
        cands.append(r)
    if not cands:
        raise HTTPException(409, f"Нет маршрута поставки через «{entity.name}»: заведите маршрут «поставщик → {entity.name} → компания РФ» "
                                 "в разделе «Снабжение и склад → Настройки»")
    country = (supplier.country or "").upper()
    cands.sort(key=lambda r: (0 if (r.supplier_country or "").upper() == country else 1, len(r.steps), r.name))
    return cands[0]


def _steps(route: SupplyRoute) -> list[dict]:
    return [{"seller_entity_id": s.seller_entity_id, "buyer_entity_id": s.buyer_entity_id, "currency": s.currency, "incoterms": s.incoterms,
             "place": s.place} for s in route.steps]


def lower_of(db: Session, upper: FactoryOffer) -> Optional[FactoryOffer]:
    return db.scalar(select(FactoryOffer).where(FactoryOffer.mirror_of_id == upper.id))


def upper_of(db: Session, lower: FactoryOffer) -> Optional[FactoryOffer]:
    return db.get(FactoryOffer, lower.mirror_of_id) if lower.mirror_of_id else None


# ------------------------------------------------------------------ отправка запроса через компанию группы

def send_from_entity(db: Session, deal: Deal, pos: Position, entity: LegalEntity, supplier: Factory, file_path: str, image_paths: list[str],
                     user_id: Optional[str], via_wecom: bool, via_mail: bool) -> dict:
    """Запрос от имени компании группы: её почта и WeCom (профили в LegalEntity.channels). Каналы Берг-Хауса не подставляются."""
    from ..adapters.mail import OutgoingMail
    from ..models import Attachment
    from .deal_service import ensure_folders
    ch = entity.channels or {}
    res: dict[str, Any] = {"wecom": None, "mail": None, "sender": entity.name}
    text = (f"{entity.name}: запрос технических характеристик и цены — {pos.designation} × {pos.qty} (ref. {deal.number}). "
            "技术参数及价格询价，请填写附件并回复。")
    if via_wecom:
        if not supplier.wecom_external_ids:
            res["wecom"] = "не отправлено: в карточке поставщика нет контакта WeCom"
        else:
            m = adapters.messenger_for(ch.get("wecom_profile", ""))
            if m is None:
                res["wecom"] = f"не отправлено: WeCom «{entity.name}» не настроен"
            else:
                try:
                    res["wecom"] = m.send_to_external(supplier.wecom_external_ids[0], text, deal.number)
                except Exception as e:   # pragma: no cover
                    res["wecom"] = f"ошибка: {e}"
    contacts = supplier.contacts or []
    emails = [c.get("email") for c in ([c for c in contacts if "rfq" in (c.get("roles") or [])] or contacts) if c.get("email")]
    if via_mail:
        if not emails:
            res["mail"] = "не отправлено: в карточке поставщика нет e-mail"
        else:
            m = adapters.mail_for(ch.get("mail_profile", ""))
            if m is None:
                res["mail"] = f"не отправлено: почта «{entity.name}» не настроена"
            else:
                try:
                    res["mail"] = m.send(OutgoingMail(to=emails, subject=f"{entity.name} — request for quotation / 询价 — {deal.number}",
                                                      body_html=f"<p>{text}</p>", attachments=[file_path] + image_paths, sender=ch.get("mail_from", "")))
                except Exception as e:   # pragma: no cover
                    res["mail"] = f"ошибка: {e}"
    try:
        ensure_folders(db, deal)
        name = f"запрос_{entity.code}_{os.path.basename(file_path)}"
        stored = adapters.storage().upload(file_path, deal.storage_folder, "01_rfq", name)
        db.add(Attachment(deal_id=deal.id, folder="01_rfq", entity="position", entity_id=pos.id, file_name=name, storage_path=stored.path,
                          storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user_id))
        res["file"] = stored.path
    except Exception as e:   # pragma: no cover
        res["file"] = f"ошибка хранилища: {e}"
    db.add(FeedEntry(deal_id=deal.id, kind="mail_out", direction="out", counterparty=supplier.name,
                     subject=f"Запрос от {entity.name}: {pos.designation}", body=text, user_id=user_id))
    return res


def request_via_group(db: Session, deal: Deal, pos: Position, group_factory: Factory, supplier_ids: list[str], user_id: Optional[str],
                      image_paths: list[str], via_wecom: bool, via_mail: bool, request_images: bool) -> list[dict]:
    """Запрос в компанию группы с выбранными конечными поставщиками: пара U/L на каждого поставщика, запрос L — от имени компании группы."""
    from . import tech_params_service as tps
    from .suppliers import require_approved
    entity = group_entity(db, group_factory)
    if entity is None:
        raise HTTPException(422, f"«{group_factory.name}» не отмечена как компания группы")
    if not supplier_ids:
        raise HTTPException(422, f"Выберите конечного поставщика, которому «{entity.name}» отправит запрос")
    out = []
    for sid in dict.fromkeys(supplier_ids):
        sup = db.get(Factory, sid)
        if sup is None:
            raise HTTPException(404, "Поставщик не найден")
        if group_entity(db, sup) is not None:
            raise HTTPException(422, f"«{sup.name}» — компания группы; конечным поставщиком должна быть внешняя компания")
        require_approved(sup, "отправить ему запрос")
        route = pick_route(db, entity, sup)
        up = db.scalar(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id, FactoryOffer.source_position_id == pos.id,
                                                  FactoryOffer.factory_id == group_factory.id, FactoryOffer.end_supplier_id == sup.id))
        if up is None:
            up = FactoryOffer(deal_id=deal.id, source_position_id=pos.id, factory_id=group_factory.id, end_supplier_id=sup.id,
                              currency=route.steps[-1].currency, basis=route.steps[-1].incoterms or "", mirror_state="waiting")
            db.add(up)
            db.flush()
        up.route_id = up.route_id or route.id
        lo = lower_of(db, up)
        if lo is None:
            lo = FactoryOffer(deal_id=deal.id, source_position_id=pos.id, factory_id=sup.id, mirror_of_id=up.id, currency=sup.default_currency or "CNY")
            db.add(lo)
            db.flush()
        path, machine = tps.build_request_xlsx(db, pos, sup, deal.number, user_id, request_images)
        sent = send_from_entity(db, deal, pos, entity, sup, path, image_paths, user_id, via_wecom, via_mail)
        up.request_sent_at = lo.request_sent_at = _now()
        db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Запрос в {entity.name}", body=f"{pos.designation}: конечный поставщик {sup.name}", user_id=user_id))
        audit(db, user_id, deal.id, "factory_offer", up.id, "request_via_group", after={"entity": entity.id, "supplier": sup.id, "route": route.id, "lower": lo.id})
        out.append({"factory_id": group_factory.id, "factory": entity.name, "offer_id": up.id, "lower_offer_id": lo.id, "end_supplier": sup.name,
                    "route": route.name, "file": sent.get("file"), "wecom": sent.get("wecom"), "mail": sent.get("mail"), "sender": entity.name,
                    "machine_translations": machine})
    return out


# ------------------------------------------------------------------ ответ поставщика → предложение компании группы

def _policy(db: Session):
    from ..api.parts_setup import current_policy, latest_fx
    return current_policy(db), latest_fx(db)


def refresh(db: Session, up: FactoryOffer, user_id: Optional[str]) -> FactoryOffer:
    """Пересчитать U из ответа L: цена по маршруту и наценкам группы; параметры, срок, фото — из ответа поставщика."""
    lo = lower_of(db, up)
    if lo is None:
        return up
    route = db.get(SupplyRoute, up.route_id) if up.route_id else None
    if route is None:
        up.mirror_state, up.chain = "error", {"error": "маршрут поставки не найден"}
        return up
    pos = db.get(Position, up.source_position_id) if up.source_position_id else None
    names = {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    steps = _steps(route)
    new_price = None
    chain: dict[str, Any] = {"route": {"id": route.id, "name": route.name}, "supplier_id": lo.factory_id, "lower_offer_id": lo.id}
    if lo.unit_price:
        steps[0] = {**steps[0], "currency": lo.currency or steps[0]["currency"]}
        if up.transfer_price:
            steps[-1] = {**steps[-1], "transfer_price": up.transfer_price}
        policy, fx = _policy(db)
        try:
            sim = simulate(float(lo.unit_price), steps, policy.rules if policy else {}, fx=fx, supplier_id=lo.factory_id, entity_names=names,
                           mode="gsho", machine_type=(pos.machine_type if pos else None))
            new_price = sim["steps"][-1]["price"]
            chain.update({"steps": sim["steps"], "companies": sim["companies"], "warnings": sim["warnings"], "purchase_rub": sim["purchase_rub"],
                          "group_margin_rub": sim["intragroup_markup_rub"], "policy_version": policy.version_no if policy else None,
                          "fx": {k: v for k, v in fx.items() if k in {s["currency"] for s in steps}}, "transfer_price": up.transfer_price,
                          "supplier_price": lo.unit_price, "supplier_currency": lo.currency, "computed_at": _now().isoformat()})
            state = "ok"
        except ChainError as e:
            chain["error"] = str(e)
            state = "no_policy" if "наценка" in str(e) or policy is None else "error"
    else:
        state = "waiting"
    if up.status == "selected":
        same = new_price is not None and up.unit_price is not None and abs(new_price - up.unit_price) < 0.005
        if not same and lo.answer_received_at:
            chain["pending_price"] = new_price
            up.chain, up.mirror_state = {**(up.chain or {}), "pending": chain}, "changed_after_select"
            deal = db.get(Deal, up.deal_id)
            db.add(FeedEntry(deal_id=up.deal_id, kind="system", subject="Ответ конечного поставщика изменился после отбора",
                             body="Цена предложения компании группы в сделке не изменена — повторите отбор или зафиксируйте трансфертную цену", user_id=user_id))
            from .legal_contracts import role_users
            for u in role_users(db, "cd"):
                notify(db, deal, u.id, "mirror_changed", ["app"], f"{deal.number}: ответ поставщика по предложению компании группы изменился после отбора")
        return up
    prev = up.unit_price
    for f in COPY_FIELDS:
        setattr(up, f, getattr(lo, f))
    up.unit_price = new_price
    up.currency = steps[-1]["currency"]
    up.basis = steps[-1]["incoterms"] or up.basis
    up.chain, up.mirror_state = chain, state
    if lo.answer_received_at and new_price is not None:   # без цены (нет ставки, курса) предложение компании группы ещё не готово
        changed = prev != new_price or up.status != "answered"
        up.status, up.answer_received_at = "answered", up.answer_received_at or lo.answer_received_at
        if changed:
            up.revision = (up.revision or 0) + 1
            deal = db.get(Deal, up.deal_id)
            entity = next((s["seller"] for s in chain.get("steps", [])[-1:]), "компания группы")
            notify(db, deal, deal.manager_id, "mirror_offer", ["app"], f"{deal.number}: получено предложение {entity} (редакция {up.revision})")
    audit(db, user_id, up.deal_id, "factory_offer", up.id, "mirror_refresh", after={"state": state, "revision": up.revision, "lower": lo.id})
    return up


def on_lower_changed(db: Session, lo: FactoryOffer, user_id: Optional[str]) -> Optional[FactoryOffer]:
    up = upper_of(db, lo)
    return refresh(db, up, user_id) if up is not None else None


def set_transfer_price(db: Session, up: FactoryOffer, price: Optional[float], note: str, user_id: str) -> FactoryOffer:
    if not up.end_supplier_id:
        raise HTTPException(422, "Трансфертная цена задаётся только для предложения компании группы")
    if price is not None and price <= 0:
        raise HTTPException(422, "Трансфертная цена больше нуля")
    if price is not None and len((note or "").strip()) < 3:
        raise HTTPException(422, "Укажите основание трансфертной цены")
    before = {"transfer_price": up.transfer_price}
    up.transfer_price, up.transfer_note = price, (note or "").strip()
    if up.status == "selected":
        raise HTTPException(409, "Предложение уже отобрано — сначала верните сделку к отбору")
    refresh(db, up, user_id)
    audit(db, user_id, up.deal_id, "factory_offer", up.id, "transfer_price", before=before, after={"transfer_price": price, "note": up.transfer_note})
    return up


# ------------------------------------------------------------------ отбор и представление

def on_selected(db: Session, selected: list[FactoryOffer], deal_id: str, user_id: Optional[str]) -> None:
    """Отбор U: запрос L тоже отобран (в ту же заявку, что и U), его цена — в историю цен конечного поставщика;
    L отклонённых U — отклонены."""
    sel = {o.id: o for o in selected if o.end_supplier_id}
    if sel:
        for lo in db.scalars(select(FactoryOffer).where(FactoryOffer.mirror_of_id.in_(list(sel)))).all():
            up = sel[lo.mirror_of_id]
            lo.status, lo.selected_deal_id, lo.selected_by = "selected", up.selected_deal_id or deal_id, user_id
            if lo.unit_price:
                db.add(FactoryPriceHistory(factory_id=lo.factory_id, model=lo.model or "", unit_price=lo.unit_price, currency=lo.currency, basis=lo.basis,
                                           production_term=lo.production_term or {}, factory_payments=lo.factory_payments or [], on=date.today(),
                                           deal_id=up.selected_deal_id or deal_id, source="via_group"))
    for lo in db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal_id, FactoryOffer.mirror_of_id.is_not(None))).all():
        up = db.get(FactoryOffer, lo.mirror_of_id)
        if up is not None and up.status == "rejected" and lo.status in ("requested", "answered"):
            lo.status = "rejected"


def view(db: Session, o: FactoryOffer, can_chain: bool) -> dict:
    """Что показать о зеркальной паре: компания группы и конечный поставщик — всем; цена поставщика, наценки, маржа — с правом цепочки."""
    if not o.end_supplier_id:
        return {}
    sup = db.get(Factory, o.end_supplier_id)
    entity = group_entity(db, db.get(Factory, o.factory_id))
    lo = lower_of(db, o)
    out: dict[str, Any] = {"group_company": entity.name if entity else "", "end_supplier": sup.name if sup else "", "end_supplier_id": o.end_supplier_id,
                           "end_supplier_country": sup.country if sup else "", "state": o.mirror_state, "revision": o.revision or 0,
                           "lower_offer_id": lo.id if lo else None, "lower_status": lo.status if lo else None,
                           "lower_answered": bool(lo and lo.answer_received_at), "route": (o.chain or {}).get("route")}
    if can_chain:
        c = dict(o.chain or {})
        out.update({"supplier_price": lo.unit_price if lo else None, "supplier_currency": lo.currency if lo else None, "steps": c.get("steps"),
                    "companies": c.get("companies"), "group_margin_rub": c.get("group_margin_rub"), "warnings": c.get("warnings") or [],
                    "error": c.get("error"), "policy_version": c.get("policy_version"), "transfer_price": o.transfer_price, "transfer_note": o.transfer_note,
                    "pending": c.get("pending"), "fx": c.get("fx")})
    elif o.mirror_state in ("no_policy", "error"):
        out["error"] = "цена компании группы ещё не рассчитана"
    return out


# ------------------------------------------------------------------ зеркальные договоры: компания группы ↔ конечный поставщик (D-160)

class MirrorPosition:
    """Позиция сделки глазами договора компании группы с конечным поставщиком: цена, валюта, базис, срок и оплата — из
    ответа поставщика (L); остальное (модель, количество, характеристики, опции РФ) — из позиции сделки."""

    def __init__(self, pos: Position, lo: FactoryOffer, up: FactoryOffer):
        self._pos, self._lo, self._up = pos, lo, up
        self.factory_id = lo.factory_id
        self.unit_price = lo.unit_price
        self.options_price = None
        self.currency = (lo.currency or pos.currency or "CNY").upper()
        self.basis = lo.basis or pos.basis
        self.production_term = lo.production_term or pos.production_term
        self.factory_payments = list(lo.factory_payments or [])
        self.factory_payments_offer = list(lo.factory_payments or [])
        self.model = lo.model or pos.model

    def __getattr__(self, name: str):
        return getattr(self._pos, name)


def mirror_positions(db: Session, deal: Deal, supplier_id: Optional[str] = None) -> list[MirrorPosition]:
    """Позиции сделки, взятые из отобранного предложения компании группы, с ответом конечного поставщика (по поставщику X)."""
    out = []
    for pos in deal.positions:
        up = db.get(FactoryOffer, pos.offer_id) if pos.offer_id else None
        if up is None or not up.end_supplier_id or pos.factory_id != up.factory_id:
            continue
        if supplier_id and up.end_supplier_id != supplier_id:
            continue
        lo = lower_of(db, up)
        if lo is None or not lo.unit_price:
            continue
        out.append(MirrorPosition(pos, lo, up))
    return out


def mirror_suppliers(db: Session, deal: Deal) -> dict[str, dict]:
    """Конечные поставщики сделки через компанию группы: X → {group_factory_id, entity_id, entity_name, group_name}."""
    out: dict[str, dict] = {}
    for mp in mirror_positions(db, deal):
        gf = db.get(Factory, mp._up.factory_id)
        ent = group_entity(db, gf)
        out.setdefault(mp.factory_id, {"group_factory_id": gf.id if gf else None, "group_name": (gf.short_name or gf.name) if gf else "",
                                       "entity_id": ent.id if ent else None, "entity_name": ent.name if ent else ""})
    return out


def is_group_factory(db: Session, factory_id: Optional[str]) -> bool:
    f = db.get(Factory, factory_id) if factory_id else None
    return bool(f and f.supplier_status == "group_company")


# ------------------------------------------------------------------ маржа группы на экране «Расчёт и утверждение» (D-161)

def group_chain_summary(db: Session, deal: Deal, res: Any) -> Optional[dict]:
    """Позиции, купленные через компанию группы: цена конечного поставщика, цена компании группы («Цена завода» сделки),
    маржа компаний группы на единицу и всего; по предложениям — прибыль сделки + маржа группы = прибыль группы."""
    rows = []
    for pos in deal.positions:
        up = db.get(FactoryOffer, pos.offer_id) if pos.offer_id else None
        if up is None or not up.end_supplier_id:
            continue
        ch = up.chain or {}
        sup = db.get(Factory, up.end_supplier_id)
        ent = group_entity(db, db.get(Factory, up.factory_id))
        per = ch.get("group_margin_rub")
        qty = pos.qty or 1
        rows.append({"position_id": pos.id, "designation": pos.designation, "qty": qty, "end_supplier": (sup.short_name or sup.name) if sup else "",
                     "group_company": ent.name if ent else "", "supplier_price": ch.get("supplier_price"), "supplier_currency": ch.get("supplier_currency"),
                     "group_price": up.unit_price, "currency": up.currency, "transfer_price": up.transfer_price, "companies": ch.get("companies") or [],
                     "group_margin_rub_unit": per, "group_margin_rub": round(per * qty, 2) if per is not None else None,
                     "state": up.mirror_state, "policy_version": ch.get("policy_version"), "warnings": ch.get("warnings") or []})
    if not rows:
        return None
    variants = {}
    for v in deal.variants:
        mine = [r for r in rows if r["position_id"] in (v.position_ids or [])]
        if not mine:
            continue
        g = round(sum(r["group_margin_rub"] or 0 for r in mine), 2)
        base = ((getattr(res, "variants", None) or {}).get(v.id) or {}).get("base")
        profit = getattr(base, "profit_rub", None) if base is not None else None
        price = getattr(base, "price_rub", None) if base is not None else None
        total = round(profit + g, 2) if profit is not None else None
        variants[v.id] = {"name": v.name, "group_margin_rub": g, "deal_profit_rub": profit, "group_profit_rub": total,
                          "group_margin_pct": round(total / price * 100, 2) if total is not None and price else None,
                          "incomplete": any(r["group_margin_rub"] is None for r in mine)}
    return {"positions": rows, "variants": variants}


def can_see_mirror_docs(user) -> bool:
    """Договор и пакет компании группы с конечным поставщиком (с его ценой): право «Цепочка компаний группы» и юрист —
    он проверяет договор (решение владельца); менеджер их не видит (D-160)."""
    from ..core.roles import READ, Role, can
    return can(user, "intragroup_chain", READ) or user.has(Role.LAWYER)
