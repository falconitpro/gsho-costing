"""Виза ФД без ответа (D-112): если ФД не завизировал и не отказал за N рабочих дней (настройка
«Виза ФД: передать ЗГД, если нет ответа»), виза переходит ЗГД. Проверка — в ежечасном цикле; ФД получает уведомление
«для сведения», задача ФД закрывается, ЗГД — задача и уведомление «требует действия», запись в журнале действий.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.workflow import DealState, DealStatus, add_business_days, fd_visa_timeout
from ..models import Deal, Task, User
from . import settings_service
from .deal_service import apply_transition, complete_tasks, notify
from ..core.clock import msk_date, today as msk_today


def escalate_fd(db: Session, today: Optional[date] = None) -> int:
    days = int(settings_service.get(db, "fd_visa_escalation_days") or 0)
    if days <= 0:
        return 0
    today = today or msk_today()
    from ..api.pricing import _ladder
    n = 0
    for deal in db.scalars(select(Deal).where(Deal.status == DealStatus.FD_VISA, Deal.deleted_at.is_(None))).all():
        tasks = db.scalars(select(Task).where(Task.deal_id == deal.id, Task.role == "fd", Task.block == "visa", Task.done_at.is_(None))
                           .order_by(Task.created_at)).all()
        if not tasks:
            continue
        since = msk_date(tasks[0].created_at)
        if today <= add_business_days(since, days):
            continue
        tr = fd_visa_timeout(DealState(status=deal.status, service_flag=deal.service_flag), days, today, _ladder(db))
        fd_ids = {t.assignee_id for t in tasks if t.assignee_id}
        complete_tasks(db, deal, block="visa", role="fd")
        apply_transition(db, deal, tr, None)
        for uid in fd_ids:
            if db.get(User, uid) is not None:
                notify(db, deal, uid, "visa_escalated", ["app"], f"{deal.number}: виза передана ЗГД — нет ответа ФД за {days} раб. дн.")
        n += 1
    if n:
        db.commit()
    return n
