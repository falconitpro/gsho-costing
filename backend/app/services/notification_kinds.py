"""Уведомления: «требуют действия» и «для сведения» (D-101, аудит 19.11.2026: 26 непрочитанных на сотрудника, до 69).

- Счётчик на колокольчике считает только уведомления, которые требуют действия. Сведения видны во вкладке «Для сведения»
  и в ленте сделки; ничего не удаляется и не перестаёт приходить.
- Уведомление о задаче становится прочитанным, когда задача выполнена (у всех получателей).
- Уведомления этапа (нужна виза, расчёт готов, доработка, черновик без движения) становятся прочитанными, когда сделка
  перешла на другой этап, ушла в архив или удалена — действие уже не нужно.
Неизвестное событие считается требующим действия: лучше лишний раз показать, чем спрятать.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event, inspect, update
from sqlalchemy.orm import Session

from ..models import NotificationLog, Task

INFO_EVENTS = frozenset({"visa_escalated", 
    "approved", "won", "execution", "offers_selected", "deal_archived", "deal_deleted", "draft_auto_archived", "legal_doc",
    "mirror_offer", "mirror_changed", "parts_eta", "parts_receipt", "parts_margin", "markup_policy", "file_download_decision",
    "support_resolved", "security", "factory_decision", "user_change_decision", "test", "task_overdue_team", "feed_answer", "office_approval_result",
})
TASK_EVENTS = ("task", "task_new", "task_overdue", "task_overdue_team", "rework")
STAGE_EVENTS = ("visa_required", "calc_ready", "rework", "draft_stale", "draft_archive_warn", "self_approval", "negotiation_below_threshold")


def is_action(ev: str) -> bool:
    return ev not in INFO_EVENTS


def close_stage_notices(db: Session, deal_id: str) -> None:
    """Сделка перешла на другой этап (ушла в архив, удалена): уведомления прежнего этапа больше не ждут действия."""
    db.execute(update(NotificationLog).where(NotificationLog.deal_id == deal_id, NotificationLog.channel == "app", NotificationLog.read_at.is_(None),
                                             NotificationLog.event.in_(STAGE_EVENTS)).values(read_at=datetime.now(timezone.utc)))


def _same_moment(a, b) -> bool:
    if a is None or b is None:
        return True
    return abs((a - b).total_seconds()) <= 2


def close_done_task_notices(db: Session) -> int:
    """Уведомления о задачах, которые уже выполнены (в том числе до D-101), — прочитаны. Ежечасно, повторный запуск ничего не меняет."""
    from sqlalchemy import select
    rows = db.scalars(select(NotificationLog).where(NotificationLog.channel == "app", NotificationLog.read_at.is_(None),
                                                    NotificationLog.event.in_(TASK_EVENTS), NotificationLog.deal_id.is_not(None))).all()
    if not rows:
        return 0
    done = db.scalars(select(Task).where(Task.deal_id.in_({r.deal_id for r in rows}), Task.done_at.is_not(None))).all()
    open_titles = {(t.deal_id, t.title) for t in db.scalars(select(Task).where(Task.deal_id.in_({r.deal_id for r in rows}), Task.done_at.is_(None))).all()}
    n = 0
    now = datetime.now(timezone.utc)
    for r in rows:
        # уведомление о задаче создаётся в той же транзакции, что и задача: сверяем и название, и время
        hit = [t for t in done if t.deal_id == r.deal_id and t.title and t.title[:200] in r.text and _same_moment(t.created_at, r.at)]
        # та же формулировка у ещё открытой задачи — не трогаем (повторная задача с тем же названием)
        if hit and not any(d == r.deal_id and ti and ti[:200] in r.text for d, ti in open_titles):
            r.read_at = now
            n += 1
    return n


@event.listens_for(Session, "before_flush")
def _close_task_notices(session: Session, flush_context, instances) -> None:   # pragma: no branch
    """Задача выполнена — её уведомления прочитаны у всех получателей (текст уведомления содержит название задачи)."""
    done = []
    for obj in session.dirty:
        if isinstance(obj, Task) and obj.done_at is not None and obj.deal_id:
            hist = inspect(obj).attrs.done_at.history
            if hist.has_changes() and not any(v is not None for v in hist.deleted):
                done.append((obj.deal_id, obj.title, obj.created_at))
    for deal_id, title, created in done:
        if not title:
            continue
        q = update(NotificationLog).where(
            NotificationLog.deal_id == deal_id, NotificationLog.channel == "app", NotificationLog.read_at.is_(None),
            NotificationLog.event.in_(TASK_EVENTS), NotificationLog.text.contains(title[:200], autoescape=True))
        if created is not None:   # только уведомления этой задачи, а не другой открытой задачи с тем же названием
            q = q.where(NotificationLog.at >= created - timedelta(seconds=2), NotificationLog.at <= created + timedelta(seconds=2))
        session.execute(q.values(read_at=datetime.now(timezone.utc)))
