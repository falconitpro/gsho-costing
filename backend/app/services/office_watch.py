"""D-133: сроки по документам Канцелярии — напоминания исполнителю, эскалация после срока, панель «на контроле».

Раз в час (фоновое задание): по открытым документам со сроком
- за 1 рабочий день до срока и в день срока — исполнителю и соисполнителям (письмо на рассмотрении — руководителю);
- на следующий день после срока — исполнителю, руководителю (кто наложил резолюцию / рассматривает) и делопроизводителям;
  дальше — повтор раз в 3 рабочих дня, пока документ не закрыт.
Каждое напоминание — один раз (отметка в office_docs.alerts). Напоминания уходят только в рабочие дни после 9:00 МСК.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.calendar import DEFAULT_CALENDAR as CAL
from ..models import Deal, OfficeDoc, User
from .office import KINDS, OPEN

MSK = timezone(timedelta(hours=3))
REPEAT_WD = 3


def _clerks(db: Session) -> list[str]:
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if "clerk" in (u.roles or [])]


def _boss(doc: OfficeDoc) -> Optional[str]:
    """Руководитель документа: кто рассматривает или кто наложил последнюю резолюцию «к исполнению»."""
    for r in reversed(doc.resolutions or []):
        if r.get("kind") == "execute" and r.get("by"):
            return r["by"]
    return doc.reviewer_id


def _title(doc: OfficeDoc) -> str:
    return f"{doc.number or KINDS.get(doc.kind, '')} «{doc.subject[:80]}»"


def check(db: Session, now: Optional[datetime] = None) -> int:
    """Разослать положенные напоминания. Возвращает число отправленных уведомлений."""
    from .deal_service import notify
    now = (now or datetime.now(timezone.utc)).astimezone(MSK)
    today = now.date()
    if not CAL.is_workday(today) or now.hour < 9:
        return 0
    sent = 0
    clerks = None
    for doc in db.scalars(select(OfficeDoc).where(OfficeDoc.status.in_(OPEN), OfficeDoc.due_on.is_not(None))).all():
        due = doc.due_on
        marks = dict(doc.alerts or {})
        targets: list[str] = []
        key, text = "", ""
        main = doc.reviewer_id if doc.status == "review" else doc.executor_id
        team = [x for x in [main, *([] if doc.status == "review" else (doc.co_executor_ids or []))] if x]
        if due > today:
            if CAL.add_working_days(today, 1) == due:
                key, targets = f"pre:{due}", team
                text = f"Канцелярия: завтра срок — {_title(doc)} ({due:%d.%m.%Y})"
        elif due == today:
            key, targets = f"today:{due}", team
            text = f"Канцелярия: сегодня срок — {_title(doc)}"
        else:
            late = CAL.working_days_between(due, today)
            if late >= 1 and (late - 1) % REPEAT_WD == 0:
                if clerks is None:
                    clerks = _clerks(db)
                key = f"late:{due}:{late}"
                targets = [*team, *([b] if (b := _boss(doc)) else []), *clerks]
                who = db.get(User, main) if main else None
                text = (f"Канцелярия: просрочено на {late} раб. дн. — {_title(doc)}, срок был {due:%d.%m.%Y}"
                        + (f", {'рассматривает' if doc.status == 'review' else 'исполнитель'}: {who.display_name}" if who else ", исполнитель не назначен"))
        if not key or key in marks:
            continue
        targets = list(dict.fromkeys(t for t in targets if t))
        deal = db.get(Deal, doc.deal_ids[0]) if doc.deal_ids else None
        for uid in targets:
            notify(db, deal, uid, "office_due", None, text)
            sent += 1
        marks[key] = today.isoformat()
        doc.alerts = marks
    sent += _ack_reminders(db, today)
    return sent


ACK_REMIND_WD = 3


def _ack_reminders(db: Session, today: date) -> int:
    """D-140: не ознакомился за 3 рабочих дня — напоминание сотруднику, дальше раз в 3 рабочих дня."""
    from sqlalchemy import String, cast
    from .deal_service import notify
    sent = 0
    for doc in db.scalars(select(OfficeDoc).where(OfficeDoc.status.in_(("signed", "in_force")), cast(OfficeDoc.acks, String) != "[]")).all():
        changed = False
        acks = []
        for a in doc.acks or []:
            if a.get("acked_at") or not a.get("at"):
                acks.append(a)
                continue
            given = datetime.fromisoformat(a["at"]).astimezone(MSK).date()
            wd = CAL.working_days_between(given, today)
            key = today.isoformat()
            if wd >= ACK_REMIND_WD and wd % ACK_REMIND_WD == 0 and key not in (a.get("reminded") or []):
                notify(db, None, a["user_id"], "office_ack", None, f"Канцелярия: напоминание — ознакомьтесь с {_title(doc)}")
                a = {**a, "reminded": [*(a.get("reminded") or []), key]}
                sent += 1
                changed = True
            acks.append(a)
        if changed:
            doc.acks = acks
    return sent


def control(db: Session, today: Optional[date] = None) -> dict:
    """Панель «на контроле»: по исполнителям — в работе, срок сегодня, просрочено; за 30 дней — закрыто и в срок."""
    today = today or date.today()
    since = today - timedelta(days=30)
    rows: dict[str, dict] = {}
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}

    def row(uid: Optional[str]) -> dict:
        k = uid or ""
        if k not in rows:
            rows[k] = {"user_id": uid, "name": names.get(uid or "", "") or "не назначен", "open": 0, "today": 0, "overdue": 0,
                       "closed_30": 0, "on_time_30": 0, "late_docs": []}
        return rows[k]

    for d in db.scalars(select(OfficeDoc).where(OfficeDoc.status.in_(OPEN))).all():
        r = row(d.reviewer_id if d.status == "review" else d.executor_id)
        r["open"] += 1
        if d.due_on == today:
            r["today"] += 1
        if d.due_on and d.due_on < today:
            r["overdue"] += 1
            r["late_docs"].append({"id": d.id, "number": d.number, "subject": d.subject, "due_on": d.due_on.isoformat(),
                                   "days": CAL.working_days_between(d.due_on, today)})
    for d in db.scalars(select(OfficeDoc).where(OfficeDoc.status == "closed", OfficeDoc.closed_on >= since, OfficeDoc.kind == "in")).all():
        r = row(d.executor_id)
        r["closed_30"] += 1
        if not d.due_on or (d.closed_on and d.closed_on <= d.due_on):
            r["on_time_30"] += 1
    items = sorted(rows.values(), key=lambda r: (-r["overdue"], -r["today"], -r["open"], r["name"]))
    for r in items:
        r["late_docs"].sort(key=lambda x: -x["days"])
    tot = {k: sum(r[k] for r in items) for k in ("open", "today", "overdue", "closed_30", "on_time_30")}
    return {"as_of": today.isoformat(), "items": items, "total": tot}
