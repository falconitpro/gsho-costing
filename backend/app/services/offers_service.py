"""Несколько заводов по одному запросу клиента (задача 9): ответы-кандидаты, сравнение «параметр × завод»,
отбор КД/ГД, дублирование заявки, бренд подачи КП."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.roles import Role
from ..core.workflow import DealStatus, add_business_days
from ..models import (
    Attachment, Batch, Deal, Factory, FactoryOffer, FactoryPriceHistory, FeedEntry, LocalOption, Position, PositionImage, PositionTechParam, Task, Variant,
)
from .deal_service import audit, next_number, notify
from .tech_params_service import _norm, has_cjk


def _copy_columns(src, dst, skip: set[str]) -> None:
    for c in src.__table__.columns:
        if c.name in skip:
            continue
        setattr(dst, c.name, getattr(src, c.name))


def duplicate_deal(db: Session, deal: Deal, user_id: str, offer_id: Optional[str] = None, copy_calcs: bool = True, comment: str = "") -> Deal:
    """Новая заявка того же клиента: требования, теххарактеристики, изображения, опции РФ, выбранный ответ завода;
    расчёты логиста и сервиса копируются с пометкой «проверить» и задачами на проверку. Цена КД, условия оплаты и визы — заново."""
    if not deal.request_group_id:
        deal.request_group_id = str(uuid.uuid4())
    siblings = len(db.scalars(select(Deal.id).where(Deal.request_group_id == deal.request_group_id)).all()) or 1
    base_no = deal.number.split("-")[0:3]
    root = "-".join(base_no) if len(base_no) >= 3 else deal.number
    number = f"{root}-{siblings + 1}"
    while db.scalar(select(Deal.id).where(Deal.number == number)):
        number = number.rsplit("-", 1)[0] + f"-{int(number.rsplit('-', 1)[1]) + 1}"
    new = Deal(number=number, client_id=deal.client_id, contact=deal.contact, site=deal.site, region=deal.region, requested_at=deal.requested_at,
               kp_due=deal.kp_due, priority=deal.priority, status=DealStatus.DRAFT, manager_id=deal.manager_id, logistician_id=deal.logistician_id,
               service_head_id=deal.service_head_id, base_currency=deal.base_currency, operating_conditions=dict(deal.operating_conditions or {}),
               service_flag=deal.service_flag, target_hours=deal.target_hours, client_payment_wishes=deal.client_payment_wishes,
               parent_deal_id=deal.id, request_group_id=deal.request_group_id, kp_brand=deal.kp_brand, has_alternatives=False)
    db.add(new)
    db.flush()
    offer = db.get(FactoryOffer, offer_id) if offer_id else None
    batch_map: dict[str, str] = {}
    if copy_calcs:
        for b in deal.batches:
            nb = Batch(deal_id=new.id)
            _copy_columns(b, nb, {"id", "deal_id", "created_at", "updated_at", "actual_events", "border_actual"})
            nb.items = {**(b.items or {}), "copied_from": deal.number, "verify": True}
            db.add(nb)
            db.flush()
            batch_map[b.id] = nb.id
    source_positions = [p for p in deal.positions if not offer or offer.source_position_id in (None, p.id)]
    for p in source_positions:
        np_ = Position(deal_id=new.id)
        _copy_columns(p, np_, {"id", "deal_id", "created_at", "updated_at", "batch_id", "rfq_import_id", "actual_events", "logistics", "service_calc"})
        np_.batch_id = batch_map.get(p.batch_id) if copy_calcs else None
        np_.logistics = {**(p.logistics or {}), "copied_from": deal.number, "verify": True} if copy_calcs and p.logistics else {}
        np_.service_calc = {**(p.service_calc or {}), "copied_from": deal.number, "verify": True} if copy_calcs and p.service_calc else {}
        np_.factory_payments = []   # график заводу задаёт КД заново
        if offer:
            np_.factory_id, np_.model, np_.unit_price, np_.currency, np_.basis = offer.factory_id, offer.model or p.model, offer.unit_price or p.unit_price, offer.currency, offer.basis
            np_.production_term = offer.production_term or p.production_term
            np_.factory_payments_offer = offer.factory_payments or []
            np_.offer_id = offer.id
            np_.price_reference = {}
        db.add(np_)
        db.flush()
        rows = offer.params if offer and offer.params else [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in (p.tech_param_rows or [])]
        for r in rows:
            db.add(PositionTechParam(position_id=np_.id, **{k: v for k, v in r.items() if k in PositionTechParam.__table__.columns.keys() and k not in ("id", "position_id", "created_at", "updated_at")}))
        for o in p.local_option_rows or []:
            no = LocalOption(deal_id=new.id, position_id=np_.id)
            _copy_columns(o, no, {"id", "deal_id", "position_id", "created_at", "updated_at", "ordered_at", "installed_at"})
            no.status = "estimate" if o.status in ("ordered", "received", "installed") else o.status
            db.add(no)
        for img in p.image_rows or []:
            db.add(PositionImage(deal_id=new.id, position_id=np_.id, attachment_id=img.attachment_id, kind=img.kind, source=img.source, caption=img.caption,
                                 has_factory_logo=img.has_factory_logo, in_kp=False, send_to_factory=False, uploaded_by=img.uploaded_by))
        if offer and offer.images:
            for aid in offer.images:
                db.add(PositionImage(deal_id=new.id, position_id=np_.id, attachment_id=aid, kind="photo", source="factory", has_factory_logo=True, uploaded_by=user_id))
        if offer and offer.unit_price:
            db.add(FactoryPriceHistory(factory_id=offer.factory_id, model=np_.model, unit_price=offer.unit_price, currency=offer.currency, basis=offer.basis,
                                       production_term=offer.production_term or {}, factory_payments=offer.factory_payments or [], on=date.today(), deal_id=new.id, source="offer"))
    db.flush()
    db.refresh(new)
    from ..api.deals import ensure_default_variant
    ensure_default_variant(db, new)
    if offer:
        offer.status, offer.selected_deal_id, offer.selected_by = "selected", new.id, user_id
    if copy_calcs and (deal.batches or any(p.logistics for p in deal.positions)):
        new.logistics_done = new.service_done = False
        for role, flag, title in ((Role.LOGISTICIAN, True, "Проверить скопированный расчёт логистики (другой завод — маршрут, вес, габариты)"),
                                  (Role.SERVICE_HEAD, deal.service_flag, "Проверить скопированный расчёт сервиса (регламенты ТО другого завода)")):
            if flag:
                assignee = new.logistician_id if role == Role.LOGISTICIAN else new.service_head_id
                db.add(Task(deal_id=new.id, role=role.value, assignee_id=assignee, title=title, block="copied_calc", due=add_business_days(date.today(), 2)))
    db.add(FeedEntry(deal_id=new.id, kind="system", subject="Заявка создана дублированием", body=f"Из {deal.number}" + (f" (ответ завода {offer.model})" if offer else "") + (f": {comment}" if comment else ""), user_id=user_id))
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject="Создана связанная заявка", body=f"{new.number}" + (f" — ответ завода {offer.model}" if offer else ""), user_id=user_id))
    audit(db, user_id, new.id, "deal", new.id, "duplicate", after={"from": deal.number, "offer_id": offer_id, "copy_calcs": copy_calcs})
    return new


# ------------------------------------------------------------------ ответы заводов и сравнение

def compare(db: Session, deal: Deal, source_position: Optional[Position]) -> dict:
    """Таблица «параметр × завод»: соответствие требованиям заказчика (match / mismatch / empty), цена, срок, условия оплаты."""
    offers = db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id).order_by(FactoryOffer.created_at)).all()
    factories = {f.id: f for f in db.scalars(select(Factory)).all()}
    base_rows = [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in sorted(source_position.tech_param_rows or [], key=lambda r: r.no)] if source_position else []
    cols = []
    for o in offers:
        f = factories.get(o.factory_id)
        cols.append({"offer_id": o.id, "factory_id": o.factory_id, "factory": f.name if f else o.factory_id, "model": o.model, "unit_price": o.unit_price,
                     "currency": o.currency, "basis": o.basis, "production_term": o.production_term, "factory_payments": o.factory_payments, "status": o.status,
                     "answered": o.answer_received_at is not None, "images": len(o.images or []), "selected_deal_id": o.selected_deal_id})
    by_offer = {o.id: {(_norm(r.get("name_ru")) or str(r.get("no"))): r for r in (o.params or [])} for o in offers}
    rows = []
    for r in base_rows:
        if r.get("local_ru"):
            continue
        key = _norm(r.get("name_ru")) or str(r.get("no"))
        req = (r.get("customer_requirement_ru") or "").strip()
        cells = []
        for o in offers:
            v = by_offer[o.id].get(key) or {}
            val = (v.get("value_ru") or v.get("value_zh") or "").strip()
            status = "empty" if not val else ("unknown" if not req else ("match" if _norm(val) == _norm(req) or _norm(req) in _norm(val) else "mismatch"))
            cells.append({"value": val, "value_zh": v.get("value_zh") or "", "comment": v.get("comment_ru") or "", "status": status})
        rows.append({"no": r.get("no"), "name_ru": r.get("name_ru"), "unit": r.get("unit"), "requirement": req, "required": r.get("required"), "cells": cells})
    return {"offers": cols, "rows": rows}


def submit_compare(db: Session, deal: Deal, user_id: str) -> None:
    """Менеджер отправил сравнение на отбор → задача КД (видна и ГД)."""
    if not db.scalar(select(Task.id).where(Task.deal_id == deal.id, Task.block == "offers", Task.done_at.is_(None))):
        db.add(Task(deal_id=deal.id, role=Role.CD.value, title="Отобрать заводы для КП (сравнение ответов)", block="offers", due=add_business_days(date.today(), 1)))
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject="Сравнение ответов заводов отправлено на отбор", body="", user_id=user_id))


def select_offers(db: Session, deal: Deal, offer_ids: list[str], user_id: str, copy_calcs: bool = True) -> list[Deal]:
    """КД/ГД отобрал ответы: первый — в исходную заявку (позиция обновляется), остальные — отдельные заявки дублированием.
    Неотобранные ответы остаются в истории цен завода."""
    offers = [o for o in db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id)).all() if o.id in offer_ids]
    if not offers:
        raise ValueError("Ответы не найдены")
    created: list[Deal] = []
    first, rest = offers[0], offers[1:]
    pos = next((p for p in deal.positions if p.id == first.source_position_id), deal.positions[0] if deal.positions else None)
    if pos is not None:
        pos.factory_id, pos.model = first.factory_id, first.model or pos.model
        if first.unit_price:
            pos.unit_price, pos.currency, pos.basis = first.unit_price, first.currency, first.basis
        if first.production_term:
            pos.production_term = first.production_term
        pos.factory_payments_offer, pos.offer_id = first.factory_payments or [], first.id
        for r in pos.tech_param_rows or []:
            v = next((x for x in (first.params or []) if _norm(x.get("name_ru")) == _norm(r.name_ru) or (x.get("no") == r.no and not r.name_ru)), None)
            if v:
                r.value_ru, r.value_zh, r.comment_ru = v.get("value_ru") or "", v.get("value_zh") or "", v.get("comment_ru") or ""
                r.translation_status, r.source = v.get("translation_status") or "none", "factory_answer"
        for aid in first.images or []:
            if not db.scalar(select(PositionImage.id).where(PositionImage.position_id == pos.id, PositionImage.attachment_id == aid)):
                db.add(PositionImage(deal_id=deal.id, position_id=pos.id, attachment_id=aid, kind="photo", source="factory", has_factory_logo=True, uploaded_by=user_id))
        if first.unit_price:
            db.add(FactoryPriceHistory(factory_id=first.factory_id, model=pos.model, unit_price=first.unit_price, currency=first.currency, basis=first.basis,
                                       production_term=first.production_term or {}, factory_payments=first.factory_payments or [], on=date.today(), deal_id=deal.id, source="offer"))
    first.status, first.selected_deal_id, first.selected_by = "selected", deal.id, user_id
    for o in rest:
        created.append(duplicate_deal(db, deal, user_id, offer_id=o.id, copy_calcs=copy_calcs))
    for o in db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id)).all():
        if o.id not in offer_ids and o.status in ("requested", "answered"):
            o.status = "rejected"
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.block == "offers", Task.done_at.is_(None))).all():
        t.done_at = datetime.now(timezone.utc)
    notify(db, deal, deal.manager_id, "offers_selected", ["app", "outlook"], f"{deal.number}: заводы для КП отобраны" + (f", созданы заявки {', '.join(d.number for d in created)}" if created else ""))
    return created


def kp_brand_check(deal: Deal, factories: dict[str, str]) -> list[str]:
    """Предупреждения перед КП под брендом компании: нет собственного наименования, файлы с логотипом завода."""
    warnings: list[str] = []
    if deal.kp_brand != "company":
        return warnings
    for p in deal.positions:
        if not kp_model_name(p, None):
            warnings.append(f"{p.model}: не задано наименование под брендом компании в каталоге")
        for img in p.image_rows or []:
            if img.in_kp and (img.has_factory_logo or img.source == "factory"):
                warnings.append(f"{p.model}: файл «{img.caption or img.attachment_id[:8]}» помечен как файл завода / с логотипом — подтвердите вложение")
    return warnings


def kp_model_name(p: Position, db: Optional[Session]) -> str:
    """Наименование модели под брендом компании: из исполнения, иначе из модели каталога."""
    if db is None:
        return ""
    from ..models import EquipmentExecution, EquipmentModel
    ex = db.get(EquipmentExecution, p.execution_id) if p.execution_id else None
    if ex and ex.brand_name:
        return ex.brand_name
    m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == p.factory_id, EquipmentModel.model == p.model))
    return (m.brand_name if m else "") or ""
