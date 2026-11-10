"""Запчасти: нормативные сроки по шагам (ТЗ запчастей, раздел 7 «Контроль») — напоминания раз в час, по одному на событие.

- КП клиенту: нет ни одной редакции КП через parts_sla_kp_days после заявки → менеджеру.
- Ответ поставщика: срок ответа на запрос цены прошёл, ответ не полный → автору запроса.
- Подтверждение заказа: заказ отправлен, нет подтверждения через parts_sla_po_confirm_days → автору заказа.
- Поставка: дата «на складе» по строке заказа прошла, а товар не принят → автору заказа и менеджеру заявки (повторно — при новой дате).
По аварийной заявке («машина стоит») нормативы вдвое короче, уведомляется и КД.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional


def norm_days(days: float, urgent: bool) -> int:
    return max(1, math.ceil(float(days) / 2)) if urgent else max(0, int(math.ceil(float(days))))


def overdue(start: Optional[date], days: float, urgent: bool, today: date) -> bool:
    return bool(start) and today > start + timedelta(days=norm_days(days, urgent))


def check(db, today: Optional[date] = None) -> int:
    from sqlalchemy import select
    from ..models import PartKp, PartRequest, PartRequestLine, PoLine, PurchaseOrder, SupplierRfq, User, WarrantyClaim
    from . import settings_service
    from .deal_service import notify
    today = today or date.today()
    kp_days = float(settings_service.get(db, "parts_sla_kp_days") or 2)
    conf_days = float(settings_service.get(db, "parts_sla_po_confirm_days") or 5)
    cds = [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if "cd" in (u.roles or [])]
    n = 0

    def send(uids, text):
        nonlocal n
        for uid in dict.fromkeys(u for u in uids if u):
            notify(db, None, uid, "parts_sla", None, text)
        n += 1

    for req in db.scalars(select(PartRequest).where(PartRequest.closed == "")).all():
        if "kp" in (req.sla_notified or []):
            continue
        if db.scalars(select(PartKp.id).where(PartKp.request_id == req.id)).first():
            continue
        if overdue(req.created_at.date(), kp_days, req.urgent, today):
            send([req.manager_id, *(cds if req.urgent else [])], f"{req.number}: КП клиенту не отправлено в норматив ({norm_days(kp_days, req.urgent)} дн.)"
                                                              + (" — машина стоит" if req.urgent else ""))
            req.sla_notified = [*(req.sla_notified or []), "kp"]

    for rfq in db.scalars(select(SupplierRfq).where(SupplierRfq.status.in_(("sent", "partial")))).all():
        if rfq.end_supplier_id and not rfq.mirror_of_id:   # предложение компании группы — ждём ответ её поставщика (напоминание по нему)
            continue
        if "overdue" in (rfq.sla_notified or []) or not rfq.due_date or today <= rfq.due_date:
            continue
        left = sum(1 for l in rfq.lines if not l.availability)
        send([rfq.created_by], f"{rfq.number}: поставщик не ответил в срок ({rfq.due_date:%d.%m.%Y}), строк без ответа {left} — напомните ему")
        rfq.sla_notified = [*(rfq.sla_notified or []), "overdue"]

    def urgent_po(po) -> bool:
        for l in po.lines:
            rl = db.get(PartRequestLine, l.request_line_id) if l.request_line_id else None
            if rl and (db.get(PartRequest, rl.request_id) or PartRequest(urgent=False)).urgent:
                return True
        return False

    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.kind == "external", PurchaseOrder.status.not_in(("closed", "cancelled", "draft")))).all():
        urg = urgent_po(po)
        if po.status == "sent" and not po.confirmed_at and "confirm" not in (po.sla_notified or []) and po.sent_at \
                and overdue(po.sent_at.date(), conf_days, urg, today):
            send([po.created_by, *(cds if urg else [])], f"{po.number}: поставщик не подтвердил заказ за {norm_days(conf_days, urg)} дн. — запросите подтверждение и дату отгрузки")
            po.sla_notified = [*(po.sla_notified or []), "confirm"]
        for l in po.lines:
            if l.status in ("received", "cancelled") or not l.eta or l.eta >= today or l.overdue_eta == l.eta:
                continue
            rl = db.get(PartRequestLine, l.request_line_id) if l.request_line_id else None
            req = db.get(PartRequest, rl.request_id) if rl else None
            send([po.created_by, req.manager_id if req else None, *(cds if req and req.urgent else [])],
                 f"{po.number}, строка {l.no} ({l.number}): срок на складе {l.eta:%d.%m.%Y} прошёл, товар не принят"
                 + (f" — заявка {req.number}{', машина стоит' if req.urgent else ''}" if req else "") + ". Уточните новую дату у поставщика или логиста.")
            l.overdue_eta = l.eta
    for c in db.scalars(select(WarrantyClaim).where(WarrantyClaim.status == "sent")).all():
        if c.answer_due and today > c.answer_due and "answer" not in (c.sla_notified or []):
            send([c.created_by], f"{c.number}: завод не ответил на гарантийную претензию в срок ({c.answer_due:%d.%m.%Y}) — напомните")
            c.sla_notified = [*(c.sla_notified or []), "answer"]
    db.flush()
    return n
