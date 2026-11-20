"""Черновики сделок без движения (аудит процессов 19.11.2026, решение владельца 20.11.2026).

- 3 дня без движения — менеджеру уведомление с перечнем того, чего не хватает для отправки на расчёт.
- 30 дней — менеджеру, КД и ЗГД: «завтра черновик будет перенесён в архив».
- Через сутки после предупреждения, если движения так и не было, — черновик переносится в архив (не удаляется):
  его видно в блоке «Архив» реестра, КД и ЗГД возвращают его кнопкой «Вернуть из архива».

«Движение» — любое изменение сделки, её позиций и предложений, новый файл или запись в ленте (не системная).
Любое движение сбрасывает отсчёт: напоминания приходят заново только после нового простоя.
Каждое напоминание — один раз на период простоя (проверка по журналу уведомлений).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.workflow import DealStatus, manager_check_items
from ..models import Attachment, Deal, FeedEntry, NotificationLog, Position, Task, User, Variant
from .deal_service import audit, notify

REMIND_DAYS = 3
WARN_DAYS = 30
ARCHIVE_AFTER_WARN = timedelta(hours=20)   # «через день»: ежечасная проверка переносит не раньше, чем через 20 ч после предупреждения
EV_REMIND, EV_WARN, EV_ARCHIVED = "draft_stale", "draft_archive_warn", "draft_auto_archived"
MSK = timedelta(hours=3)


def _utc(x: datetime | None) -> datetime | None:
    if x is None:
        return None
    return x if x.tzinfo else x.replace(tzinfo=timezone.utc)


def last_activity(db: Session, deal_ids: list[str]) -> dict[str, datetime]:
    """Последнее движение по каждому черновику: сделка, позиции, предложения, файлы, лента (кроме системных записей)."""
    if not deal_ids:
        return {}
    out: dict[str, datetime] = {}

    def take(rows):
        for did, t in rows:
            t = _utc(t)
            if t is not None and (did not in out or t > out[did]):
                out[did] = t
    take(db.execute(select(Deal.id, Deal.updated_at).where(Deal.id.in_(deal_ids))).all())
    take(db.execute(select(Position.deal_id, func.max(Position.updated_at)).where(Position.deal_id.in_(deal_ids)).group_by(Position.deal_id)).all())
    take(db.execute(select(Variant.deal_id, func.max(Variant.updated_at)).where(Variant.deal_id.in_(deal_ids)).group_by(Variant.deal_id)).all())
    take(db.execute(select(Attachment.deal_id, func.max(Attachment.uploaded_at)).where(Attachment.deal_id.in_(deal_ids)).group_by(Attachment.deal_id)).all())
    take(db.execute(select(FeedEntry.deal_id, func.max(FeedEntry.at)).where(FeedEntry.deal_id.in_(deal_ids), FeedEntry.kind != "system")
                    .group_by(FeedEntry.deal_id)).all())
    return out


def idle_days(last: datetime, now: datetime) -> int:
    """Дни без движения по московской дате (как на рабочем месте)."""
    return ((now + MSK).date() - (last + MSK).date()).days


def _sent_since(db: Session, deal_id: str, event: str, since: datetime) -> datetime | None:
    t = db.scalar(select(func.max(NotificationLog.at)).where(NotificationLog.deal_id == deal_id, NotificationLog.event == event, NotificationLog.at > since))
    return _utc(t)


def _missing_text(deal: Deal) -> str:
    from ..api.deals import _deal_dict
    items = manager_check_items(_deal_dict(deal))
    parts = []
    for it in items:
        what = list(it["fields"]) + [f"характеристика «{t}»" for t in it["tech"]]
        if what:
            label = "" if it["group"] == "Сделка" else f"{it['group']}: "
            parts.append(label + ", ".join(what[:6]) + (f" и ещё {len(what) - 6}" if len(what) > 6 else ""))
    return "; ".join(parts[:5]) + (f"; и ещё {len(parts) - 5} разд." if len(parts) > 5 else "")


def _leaders(db: Session) -> list[User]:
    return [u for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if {"cd", "gd"} & set(u.roles or [])]


def check(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Ежечасная проверка. Возвращает, сколько напоминаний, предупреждений и переносов в архив сделано."""
    now = now or datetime.now(timezone.utc)
    drafts = db.scalars(select(Deal).where(Deal.status == DealStatus.DRAFT, Deal.archived_at.is_(None), Deal.deleted_at.is_(None))).all()
    acts = last_activity(db, [d.id for d in drafts])
    stats = {"reminded": 0, "warned": 0, "archived": 0}
    leaders = None
    for deal in drafts:
        last = acts.get(deal.id) or _utc(deal.created_at) or now
        age = idle_days(last, now)
        if age < REMIND_DAYS:
            continue
        if age >= WARN_DAYS:
            warned = _sent_since(db, deal.id, EV_WARN, last)
            if warned is None:
                leaders = leaders if leaders is not None else _leaders(db)
                text = (f"{deal.number}: черновик {age} дн. без движения — завтра будет перенесён в архив. "
                        f"Чтобы оставить в работе — внесите любое изменение или отправьте на расчёт; ненужный — удалите.")
                to = {deal.manager_id} if deal.manager_id else set()
                to |= {u.id for u in leaders}
                for uid in to:
                    notify(db, deal, uid, EV_WARN, None, text)
                stats["warned"] += 1
            elif now - warned >= ARCHIVE_AFTER_WARN:
                _archive(db, deal, age, now)
                stats["archived"] += 1
            continue
        if deal.manager_id and _sent_since(db, deal.id, EV_REMIND, last) is None:
            missing = _missing_text(deal)
            text = (f"{deal.number}: черновик {age} дн. без движения. "
                    + (f"Для отправки на расчёт не хватает — {missing}." if missing else "Всё заполнено — можно отправлять на расчёт.")
                    + f" Если черновик пролежит без движения {WARN_DAYS + 1} день, он уйдёт в архив.")
            notify(db, deal, deal.manager_id, EV_REMIND, None, text)
            stats["reminded"] += 1
    return stats


def _archive(db: Session, deal: Deal, age: int, now: datetime) -> None:
    reason = f"автоматически: черновик {age} дн. без движения"
    deal.archived_at, deal.archived_by, deal.archive_reason = now, None, reason
    closed = 0
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None))).all():
        t.done_at = now
        closed += 1
    db.add(FeedEntry(deal_id=deal.id, kind="system", direction="out", subject="Черновик перенесён в архив",
                     body=f"{reason}. Вернуть — КД или ЗГД, кнопка «Вернуть из архива» в карточке сделки."))
    text = f"{deal.number}: черновик перенесён в архив ({reason}). Вернуть могут КД и ЗГД — реестр сделок, блок «Архив»."
    for uid in ({deal.manager_id} if deal.manager_id else set()) | {u.id for u in _leaders(db)}:
        notify(db, deal, uid, EV_ARCHIVED, None, text)
    audit(db, None, deal.id, "deal", deal.id, "archive", {"status": deal.status.value}, {"reason": reason, "auto": True, "tasks_closed": closed}, reason)


__all__ = ["check", "last_activity", "idle_days", "REMIND_DAYS", "WARN_DAYS"]
