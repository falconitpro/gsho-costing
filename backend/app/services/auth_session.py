"""Сессии на сервере (docs/operations/04 §2, 02 §5).

JWT в cookie `gsho_session` несёт sub, sid, iat, exp, nonce, pv, mfa. Каждая проверка смотрит запись AuthSession:
не отозвана, не истекла по бездействию (8 ч; «Запомнить меня» — 30 дн.) и по абсолютному сроку (24 ч; «Запомнить меня» — 30 дн.).
last_seen обновляется не чаще раза в 60 секунд. Сам токен на сервере не хранится.
"""
from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..models import AuthSession, MfaChallenge, User
from . import security_events as sev

REMEMBER_DAYS = 30
TOUCH_EVERY = timedelta(seconds=60)
STEP_UP_TTL = timedelta(minutes=10)
COOKIE = "gsho_session"


def now() -> datetime:
    return datetime.now(timezone.utc)


def password_version(user: User) -> int:
    at = user.password_set_at
    return int(at.timestamp() * 1000) if at else 0


def _limits(s: Settings, remember: bool) -> tuple[timedelta, timedelta]:
    if remember:
        return timedelta(days=REMEMBER_DAYS), timedelta(days=REMEMBER_DAYS)
    return timedelta(hours=s.session_ttl_hours), timedelta(hours=max(s.session_absolute_hours, s.session_ttl_hours))


def create(db: Session, user: User, s: Settings, *, remember: bool = False, method: str = "password", mfa_level: str = "none",
           request: Optional[Request] = None) -> AuthSession:
    idle, absolute = _limits(s, remember)
    t = now()
    row = AuthSession(sid=str(uuid.uuid4()), user_id=user.id, created_at=t, last_seen_at=t, idle_expires_at=t + idle,
                      absolute_expires_at=t + absolute, remember=remember, auth_method=method, mfa_level=mfa_level,
                      last_mfa_at=t if mfa_level != "none" else None, last_step_up_at=t if mfa_level != "none" else None,
                      ip_prefix=sev.ip_prefix(request), user_agent_short=sev.user_agent_short(request))
    db.add(row)
    db.flush()
    return row


def token(row: AuthSession, user: User, s: Settings) -> str:
    claims = {"sub": user.id, "sid": row.sid, "iat": int(time.time()), "exp": int(row.absolute_expires_at.timestamp()),
              "nonce": secrets.token_hex(4), "pv": password_version(user), "mfa": row.mfa_level}
    if row.remember:
        claims["rem"] = 1
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def set_cookie(resp: Response, row: AuthSession, user: User, s: Settings) -> None:
    max_age = max(60, int((row.absolute_expires_at - now()).total_seconds()))
    resp.set_cookie(COOKIE, token(row, user, s), httponly=True, secure=not s.is_dev, samesite="lax", max_age=max_age)


def issue(db: Session, resp: Response, user: User, s: Settings, **kw) -> AuthSession:
    row = create(db, user, s, **kw)
    set_cookie(resp, row, user, s)
    return row


class SessionInvalid(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def validate(db: Session, claims: dict, user: User) -> AuthSession:
    row = db.scalar(select(AuthSession).where(AuthSession.sid == str(claims.get("sid") or "")))
    t = now()
    if row is None or row.user_id != user.id:
        raise SessionInvalid("Сессия не найдена — войдите заново")
    if row.revoked_at is not None:
        raise SessionInvalid("Сессия завершена — войдите заново")
    if row.absolute_expires_at <= t:
        raise SessionInvalid("Срок сессии истёк — войдите заново")
    if row.idle_expires_at <= t:
        raise SessionInvalid("Сессия истекла из-за бездействия — войдите заново")
    return row


def touch(db: Session, row: AuthSession, s: Settings) -> bool:
    t = now()
    if row.last_seen_at and t - row.last_seen_at < TOUCH_EVERY:
        return False
    idle, _ = _limits(s, row.remember)
    row.last_seen_at = t
    row.idle_expires_at = min(t + idle, row.absolute_expires_at)
    return True


def revoke(db: Session, row: AuthSession, reason: str, by: Optional[str]) -> bool:
    if row.revoked_at is not None:
        return False
    row.revoked_at, row.revoke_reason, row.revoked_by = now(), reason, by
    return True


def revoke_all(db: Session, user_id: str, reason: str, by: Optional[str], except_sid: Optional[str] = None) -> int:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_sid:
        q = q.where(AuthSession.sid != except_sid)
    n = db.execute(q.values(revoked_at=now(), revoke_reason=reason, revoked_by=by)).rowcount or 0
    db.execute(update(MfaChallenge).where(MfaChallenge.user_id == user_id, MfaChallenge.consumed_at.is_(None)).values(consumed_at=now()))
    return int(n)


def active(db: Session, user_id: str) -> list[AuthSession]:
    t = now()
    return db.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None), AuthSession.absolute_expires_at > t,
                                                AuthSession.idle_expires_at > t).order_by(AuthSession.last_seen_at.desc().nullslast())).all()


def dto(row: AuthSession, current_sid: Optional[str] = None) -> dict:
    """Без JWT, cookie и полного user-agent: только то, что помогает узнать своё устройство."""
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    return {"sid": row.sid, "current": row.sid == current_sid, "device": row.user_agent_short or "неизвестное устройство",
            "ip_prefix": row.ip_prefix, "created_at": iso(row.created_at), "last_seen_at": iso(row.last_seen_at),
            "expires_at": iso(min(row.idle_expires_at, row.absolute_expires_at)), "remember": row.remember,
            "auth_method": row.auth_method, "mfa_level": row.mfa_level}


def step_up_fresh(row: Optional[AuthSession]) -> bool:
    return bool(row and row.last_step_up_at and now() - row.last_step_up_at <= STEP_UP_TTL)
