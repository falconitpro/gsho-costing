"""Безопасность сотрудников (docs/operations/09 §5): сессии, сброс TOTP, временный пароль, отключение и включение доступа,
журнал событий. Чтение — security_admin ≥ READ; действия — security_admin = EDIT и подтверждение не старше 10 минут."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user, hash_password, require_step_up
from ..core.roles import ROLE_TITLES, Role
from ..db import get_db
from ..models import AuthSession, SecurityEvent, User
from ..services import auth_session as sess
from ..services import mfa as mfa_svc
from ..services import offboarding
from ..services import password_policy
from ..services import security_events as sev
from ..services.deal_service import audit

router = APIRouter(prefix="/api/security", tags=["security"])


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("security_admin", 1):
        raise HTTPException(403, "Нет доступа к безопасности сотрудников")
    return user


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("security_admin", 2):
        raise HTTPException(403, "Действие доступно администратору безопасности")
    return user


def _target(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Сотрудник не найден")
    return u


def _user_dto(db: Session, u: User) -> dict:
    roles = []
    for r in u.roles or []:
        try:
            roles.append({"code": r, "title": ROLE_TITLES[Role(r)]})
        except ValueError:
            roles.append({"code": r, "title": r})
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    return {"id": u.id, "display_name": u.display_name, "email": u.email, "roles": roles, "is_active": u.is_active,
            "offboarded_at": iso(u.offboarded_at), "last_seen_at": iso(u.last_seen_at), "mfa_enabled": mfa_svc.enabled(db, u.id),
            "password_set": bool(u.password_hash), "password_must_change": bool(u.password_must_change),
            "active_sessions": len(sess.active(db, u.id)) if u.is_active else 0}


@router.get("/users")
def users(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    rows = db.scalars(select(User).order_by(User.is_active.desc(), User.display_name)).all()
    return {"items": [_user_dto(db, u) for u in rows], "can_edit": user.can("security_admin", 2), "step_up_fresh": user.step_up_fresh}


@router.get("/users/{user_id}")
def user_card(user_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    u = _target(db, user_id)
    return {**_user_dto(db, u), "sessions": [sess.dto(r) for r in sess.active(db, u.id)], "offboard_preview": offboarding.preview(db, u) if u.is_active else None}


@router.get("/users/{user_id}/sessions")
def user_sessions(user_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    u = _target(db, user_id)
    return {"items": [sess.dto(r, user.sid) for r in sess.active(db, u.id)]}


@router.delete("/users/{user_id}/sessions/{sid}")
def revoke_user_session(user_id: str, sid: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    require_step_up(user)
    u = _target(db, user_id)
    row = db.scalar(select(AuthSession).where(AuthSession.sid == sid, AuthSession.user_id == u.id))
    if row is None or row.revoked_at is not None:
        raise HTTPException(404, "Сессия не найдена или уже завершена")
    sess.revoke(db, row, "admin", user.id)
    sev.record(db, "session", "admin_revoke", actor_id=user.id, subject_id=u.id, request=request, session_id=user.sid, entity_type="session", entity_id=sid)
    db.commit()
    return {"ok": True, "items": [sess.dto(r, user.sid) for r in sess.active(db, u.id)]}


@router.post("/users/{user_id}/sessions/revoke-all")
def revoke_user_sessions(user_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    require_step_up(user)
    u = _target(db, user_id)
    n = sess.revoke_all(db, u.id, "admin", user.id, except_sid=user.sid if u.id == user.id else None)
    sev.record(db, "session", "admin_revoke_all", actor_id=user.id, subject_id=u.id, request=request, session_id=user.sid, meta={"count": n})
    db.commit()
    return {"ok": True, "revoked": n}


@router.post("/users/{user_id}/mfa/reset")
def reset_mfa(user_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Сброс TOTP при потере телефона (12 SEC-08): удаляет секрет и коды, завершает все сессии сотрудника."""
    require_step_up(user)
    u = _target(db, user_id)
    if u.id == user.id:
        raise HTTPException(409, "Свой TOTP сбрасывает другой администратор безопасности")
    had = mfa_svc.enabled(db, u.id)
    mfa_svc.disable(db, u.id)
    n = sess.revoke_all(db, u.id, "mfa_reset", user.id)
    audit(db, user.id, None, "user", u.id, "mfa_reset", {"mfa_enabled": had}, {"mfa_enabled": False, "sessions_revoked": n})
    sev.record(db, "mfa", "admin_reset", actor_id=user.id, subject_id=u.id, request=request, session_id=user.sid, meta={"had_mfa": had, "sessions_revoked": n})
    offboarding._notify_admins(db, user.id, f"Сброшен вход с кодом из приложения у «{u.display_name}» — сотрудник настроит его заново")
    db.commit()
    return {"ok": True, "sessions_revoked": n}


@router.post("/users/{user_id}/password-reset")
def temporary_password(user_id: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    """Временный пароль (04 §4): показывается один раз в ответе, в журнал не пишется; сотрудник обязан сменить его при входе."""
    require_step_up(user)
    u = _target(db, user_id)
    if u.id == user.id:
        raise HTTPException(409, "Свой пароль меняйте в разделе «Безопасность»")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    while True:
        temp = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
        if not password_policy.problems(temp, u):
            break
    password_policy.remember_old(db, u)
    u.password_hash, u.password_set_at, u.password_must_change = hash_password(temp), datetime.now(timezone.utc), True
    u.failed_logins, u.locked_until = 0, None
    n = sess.revoke_all(db, u.id, "password", user.id)
    audit(db, user.id, None, "user", u.id, "password_set", after={"by": user.display_name, "temporary": True})
    sev.record(db, "password", "admin_temporary", actor_id=user.id, subject_id=u.id, request=request, session_id=user.sid, meta={"sessions_revoked": n})
    db.commit()
    return {"ok": True, "temporary_password": temp, "sessions_revoked": n}


class ConfirmIn(BaseModel):
    confirm_name: str = ""
    reason: str = ""


@router.post("/users/{user_id}/offboard")
def offboard_user(user_id: str, body: ConfirmIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return offboarding.offboard(db, user, user_id, request, confirm_name=body.confirm_name, reason=body.reason)


@router.post("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, body: ConfirmIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return offboarding.reactivate(db, user, user_id, request, confirm_name=body.confirm_name)


@router.get("/events")
def events(user_id: Optional[str] = None, event_type: Optional[str] = None, result: Optional[str] = None, limit: int = 50, offset: int = 0,
           db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Журнал событий (security_audit ≥ READ). Полный экран журнала и предупреждения — этап 3."""
    if not user.can("security_audit", 1):
        raise HTTPException(403, "Нет доступа к журналу безопасности")
    q = select(SecurityEvent)
    if user_id:
        q = q.where((SecurityEvent.actor_user_id == user_id) | (SecurityEvent.subject_user_id == user_id))
    if event_type:
        q = q.where(SecurityEvent.event_type == event_type)
    if result in ("success", "denied", "error"):
        q = q.where(SecurityEvent.result == result)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(SecurityEvent.occurred_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
    return {"total": int(total), "items": [{"id": e.id, "at": e.occurred_at.isoformat() if e.occurred_at else None, "type": e.event_type, "action": e.action,
                                            "result": e.result, "actor_user_id": e.actor_user_id, "subject_user_id": e.subject_user_id,
                                            "ip_prefix": e.ip_prefix, "entity_type": e.entity_type, "entity_id": e.entity_id, "meta": e.meta or {}} for e in rows]}
