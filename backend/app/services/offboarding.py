"""Отключение сотрудника одной транзакцией (docs/operations/04 §5, 12 OFF-01…OFF-05).

Все пути отключения (новый POST /api/security/users/{id}/offboard, PATCH сотрудника с is_active=false, «удаление»
сотрудника со ссылками) вызывают offboard(). Любая ошибка шага — откат всего; успех показывается только после commit.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, require_step_up
from ..models import AccessUserOverride, NotificationLog, Task, User
from . import auth_session as sess
from . import security_events as sev


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _name_ok(user: User, typed: Optional[str]) -> bool:
    t = (typed or "").strip().casefold()
    full = (user.display_name or "").strip().casefold()
    short = re.sub(r"\s*\(.*\)\s*$", "", full)
    return bool(t) and t in {full, short}


def security_admin_ids(db: Session, exclude: Optional[str] = None) -> list[str]:
    from .access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all()
            if u.id != exclude and perms_for(db, u).get("security_admin", 0) >= 2]


def preview(db: Session, user: User) -> dict:
    from ..models.finance import FinanceQuestion
    today = date.today()
    t = _now()
    return {
        "active_sessions": len(sess.active(db, user.id)),
        "open_tasks": len(db.scalars(select(Task.id).where(Task.assignee_id == user.id, Task.done_at.is_(None))).all()),
        "active_overrides": len(db.scalars(select(AccessUserOverride.id).where(AccessUserOverride.user_id == user.id,
                                                                             (AccessUserOverride.valid_to.is_(None)) | (AccessUserOverride.valid_to > t))).all()),
        "deputy_links": len([u for u in db.scalars(select(User).where(User.deputy_id == user.id)).all() if u.deputy_to is None or u.deputy_to >= today])
                        + (1 if user.deputy_id and (user.deputy_to is None or user.deputy_to >= today) else 0),
        "finance_questions_open": len(db.scalars(select(FinanceQuestion.id).where(FinanceQuestion.assignee_id == user.id,
                                                                                FinanceQuestion.status.in_(["open", "answered"]))).all()),
        "support_tickets_assigned": 0,   # появится с внутренней поддержкой (этап 4): заявки снимаются с исполнителя
    }


# --- шаги (отдельными функциями, чтобы тест мог «уронить» любой и проверить откат, OFF-04)

def _revoke_sessions(db: Session, user: User, by: str) -> int:
    return sess.revoke_all(db, user.id, "offboard", by)


def _end_delegations(db: Session, user: User) -> int:
    yesterday = date.today() - timedelta(days=1)
    n = 0
    for other in db.scalars(select(User).where(User.deputy_id == user.id)).all():   # он замещал других
        if other.deputy_to is None or other.deputy_to > yesterday:
            other.deputy_to = yesterday
            n += 1
    if user.deputy_id and (user.deputy_to is None or user.deputy_to > yesterday):     # его замещали
        user.deputy_to = yesterday
        n += 1
    return n


def _end_overrides(db: Session, user: User) -> int:
    t = _now()
    return int(db.execute(update(AccessUserOverride).where(AccessUserOverride.user_id == user.id,
                                                          (AccessUserOverride.valid_to.is_(None)) | (AccessUserOverride.valid_to > t))
                          .values(valid_to=t)).rowcount or 0)


def _flag_tasks(db: Session, user: User) -> int:
    return int(db.execute(update(Task).where(Task.assignee_id == user.id, Task.done_at.is_(None), Task.needs_reassignment.is_(False))
                          .values(needs_reassignment=True)).rowcount or 0)


def _notify_admins(db: Session, actor_id: str, text: str, exclude: Optional[str] = None) -> int:
    ids = [i for i in security_admin_ids(db, exclude=exclude) if i != actor_id]
    for uid in ids:
        db.add(NotificationLog(user_id=uid, deal_id=None, event="security", channel="app", status="sent", text=text))   # только «в системе»
    return len(ids)


def offboard(db: Session, actor: CurrentUser, user_id: str, request: Optional[Request] = None, confirm_name: Optional[str] = None,
             require_name: bool = True, reason: str = "") -> dict:
    from .deal_service import audit
    if not actor.can("security_admin", 2):
        raise HTTPException(403, "Отключать доступ может только администратор безопасности")
    require_step_up(actor)
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Сотрудник не найден")
    if user.id == actor.id:
        raise HTTPException(409, "Нельзя отключить свою учётную запись")
    if not user.is_active:
        raise HTTPException(409, "Доступ сотрудника уже отключён")
    if require_name and not _name_ok(user, confirm_name):
        raise HTTPException(422, "Для подтверждения введите фамилию и имя сотрудника так, как они указаны в карточке")
    if not security_admin_ids(db, exclude=user.id):
        raise HTTPException(409, "Это последний активный администратор безопасности — сначала назначьте другого")
    before = preview(db, user)
    counts = {
        "sessions_revoked": _revoke_sessions(db, user, actor.id),
        "delegations_ended": _end_delegations(db, user),
        "overrides_ended": _end_overrides(db, user),
        "tasks_flagged": _flag_tasks(db, user),
        "finance_questions_open": before["finance_questions_open"],   # не закрываются автоматически — видны ответственному (04 §5.2 п. 9)
        "support_tickets_unassigned": 0,
    }
    user.is_active, user.offboarded_at, user.offboarded_by = False, _now(), actor.id
    audit(db, actor.id, None, "user", user.id, "offboard", {"is_active": True}, {"is_active": False, **counts}, reason[:300])
    sev.record(db, "offboard", "offboard", actor_id=actor.id, subject_id=user.id, request=request, session_id=actor.sid,
               entity_type="user", entity_id=user.id, meta=counts)
    _notify_admins(db, actor.id, f"Доступ сотрудника «{user.display_name}» отключён: сессий завершено {counts['sessions_revoked']}, "
                                 f"задач к передаче {counts['tasks_flagged']}", exclude=user.id)
    db.commit()
    return {"ok": True, "user_id": user.id, "display_name": user.display_name, "counts": counts}


def reactivate(db: Session, actor: CurrentUser, user_id: str, request: Optional[Request] = None, confirm_name: Optional[str] = None,
               require_name: bool = True) -> dict:
    """Включение обратно (04 §5.3): старые сессии и исключения прав не восстанавливаются; нужен новый вход."""
    from .deal_service import audit
    if not actor.can("security_admin", 2):
        raise HTTPException(403, "Включать доступ может только администратор безопасности")
    require_step_up(actor)
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Сотрудник не найден")
    if user.is_active:
        raise HTTPException(409, "Доступ сотрудника уже включён")
    if require_name and not _name_ok(user, confirm_name):
        raise HTTPException(422, "Для подтверждения введите фамилию и имя сотрудника так, как они указаны в карточке")
    user.is_active = True
    user.failed_logins, user.locked_until = 0, None
    audit(db, actor.id, None, "user", user.id, "reactivate", {"is_active": False}, {"is_active": True})
    sev.record(db, "offboard", "reactivate", actor_id=actor.id, subject_id=user.id, request=request, session_id=actor.sid, entity_type="user", entity_id=user.id)
    _notify_admins(db, actor.id, f"Доступ сотрудника «{user.display_name}» снова включён — проверьте его роли и права")
    db.commit()
    return {"ok": True, "user_id": user.id, "display_name": user.display_name}
