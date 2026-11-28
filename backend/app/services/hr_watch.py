"""D-207: «Персонал» — напоминания (фоновое задание, раз в час, в рабочие дни после 9:00 МСК).

1. Уволен — вход в CRM блокируется сразу после последнего рабочего дня (решение владельца 28.11.2026): каждый час, в любой день,
   общими шагами отключения (сессии, замещения, временные права, задачи). Если заблокировать нельзя (последний администратор
   безопасности) — уведомление администраторам безопасности и кадровикам; после отключения уведомления становятся прочитанными.
2. Допуски, обучение, инструктажи и медосмотры (D-206): за 30 дней до конца срока и в день окончания — кадровикам
   из области юрлица и самому сотруднику (если у него есть вход в CRM). Продлили — новый срок, новые напоминания.

Каждое напоминание — один раз: повтор узнаётся по журналу уведомлений (событие + ссылка с id и сроком).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..calc.calendar import DEFAULT_CALENDAR as CAL
from ..models import EngineerPermit, HrAccessScope, HrEmployee, HrEmployment, NotificationLog, ServicePermit, User

MSK = timezone(timedelta(hours=3))
SOON = 30
EV_OFF, EV_SOON, EV_EXP = "hr_offboard", "hr_permit_soon", "hr_permit_expired"


def _editors(db: Session) -> list[tuple[User, Optional[set[str]]]]:
    """Кадровики: право EDIT на «Персонал» и их область юрлиц (None — все)."""
    from ..core.roles import HR_ALL_EMPLOYERS, Role
    from .access_service import perms_for
    scopes: dict[str, set[str]] = {}
    for s in db.scalars(select(HrAccessScope)):
        scopes.setdefault(s.user_id, set()).add(s.employer_id)
    out = []
    for u in db.scalars(select(User).where(User.is_active.is_(True))):
        if perms_for(db, u).get("hr_core", 0) < 2:
            continue
        roles = set(u.roles or [])
        if Role.GD.value in roles or (u.id not in scopes and roles & {r.value for r in HR_ALL_EMPLOYERS}):
            out.append((u, None))
        else:
            out.append((u, scopes.get(u.id, set())))
    return out


def _sent(db: Session, user_id: str, event: str, link: str) -> bool:
    return db.scalar(select(NotificationLog.id).where(NotificationLog.user_id == user_id, NotificationLog.event == event,
                                                      NotificationLog.link == link).limit(1)) is not None


def _send(db: Session, user_ids: set[str], event: str, text: str, link: str) -> int:
    from .deal_service import notify
    n = 0
    for uid in sorted(user_ids):
        if not _sent(db, uid, event, link):
            notify(db, None, uid, event, None, text, link)
            n += 1
    return n


def block_gone(db: Session, today) -> int:
    """D-207 (решение владельца 28.11): уволен отовсюду, последний рабочий день прошёл — вход в CRM блокируется."""
    from .hr_core import block_if_gone, terminated_by
    n = 0
    for e in db.scalars(select(HrEmployee).where(HrEmployee.user_id.is_not(None))):
        u = db.get(User, e.user_id)
        if u is not None and u.is_active and block_if_gone(db, e, today, terminated_by(db, e.id)):
            n += 1
    return n


def check(db: Session, now: Optional[datetime] = None) -> int:
    from .engineer_permits import best_by_code, owned
    from .hr_core import full_name
    from .offboarding import security_admin_ids
    now = (now or datetime.now(timezone.utc)).astimezone(MSK)
    today = now.date()
    sent = block_gone(db, today)   # блокировка — в любой день и час: сразу после последнего рабочего дня
    if not CAL.is_workday(today) or now.hour < 9:
        return sent
    editors = _editors(db)
    emps: dict[str, list[HrEmployment]] = {}
    for m in db.scalars(select(HrEmployment)):
        emps.setdefault(m.employee_id, []).append(m)

    def hr_for(employer_ids: set[str]) -> set[str]:
        return {u.id for u, sc in editors if sc is None or sc & employer_ids}

    # 1. уволен — вход не отключён
    for e in db.scalars(select(HrEmployee).where(HrEmployee.user_id.is_not(None))):
        ms = emps.get(e.id, [])
        u = db.get(User, e.user_id)
        link = f"/hr?e={e.id}"
        if u is None or not ms:
            continue
        if not u.is_active:   # доступ уже отключён — напоминания больше не нужны
            db.execute(update(NotificationLog).where(NotificationLog.event == EV_OFF, NotificationLog.link == link,
                                                     NotificationLog.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
            continue
        if any(m.end_date is None or m.end_date >= today for m in ms):
            continue
        last = max(m.end_date for m in ms if m.end_date)
        sent += _send(db, set(security_admin_ids(db)) | hr_for({m.employer_id for m in ms}), EV_OFF,
                      f"Уволен(а) {last:%d.%m.%Y}: {full_name(e)} — вход в CRM ещё не отключён. Отключите доступ в карточке сотрудника.", link)

    # 2. допуски: за 30 дней и в день окончания
    names = {k.code: k.name for k in db.scalars(select(ServicePermit))}
    for e in db.scalars(select(HrEmployee)):
        ms = [m for m in emps.get(e.id, []) if m.start_date <= today and (m.end_date is None or m.end_date >= today)]
        if not ms:
            continue
        who = hr_for({m.employer_id for m in ms})
        if e.user_id:
            u = db.get(User, e.user_id)
            if u is not None and u.is_active:
                who.add(u.id)
        for p in best_by_code(owned(db, employee_id=e.id)).values():
            # срок, закончившийся в выходные, напоминается в первый рабочий день (окно 4 дня); давно истёкшие — в сводке «Допуски»
            if not p.valid_until or p.valid_until < today - timedelta(days=4):
                continue
            link = f"/hr?e={e.id}&s=adm#{p.id}:{p.valid_until.isoformat()}"
            what = f"{names.get(p.permit_code, p.permit_code)} — {full_name(e)}"
            if p.valid_until <= today:
                when = "Сегодня последний день" if p.valid_until == today else f"Срок закончился {p.valid_until:%d.%m.%Y}"
                sent += _send(db, who, EV_EXP, f"{when}: {what}. Без продления сотрудника нельзя назначать на работы, где это нужно.", link)
            elif today < p.valid_until <= today + timedelta(days=SOON):
                sent += _send(db, who, EV_SOON, f"До {p.valid_until:%d.%m.%Y} заканчивается: {what}. Запишите на продление.", link)
    return sent
