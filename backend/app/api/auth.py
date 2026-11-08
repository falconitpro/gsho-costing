from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import SESSION_COOKIE, CurrentUser, OidcClient, get_current_user, hash_password, issue_session, set_session_cookie, verify_password
from ..core.config import Settings, get_settings
from ..core.roles import ROLE_TITLES, Role
from ..db import get_db
from ..models import User
from ..services.deal_service import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
def login(s: Settings = Depends(get_settings)):
    if s.auth_provider == "dev":
        raise HTTPException(400, "В dev-режиме используйте заголовок X-Dev-User (см. /api/auth/dev-users)")
    state = secrets.token_urlsafe(16)
    resp = RedirectResponse(OidcClient(s).authorize_url(state))
    resp.set_cookie("oidc_state", state, httponly=True, max_age=600)
    return resp


@router.get("/callback")
async def callback(request: Request, code: str, state: str, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    if request.cookies.get("oidc_state") != state:
        raise HTTPException(400, "Неверный state")
    client = OidcClient(s)
    claims = await client.exchange_code(code)
    sub, email = str(claims.get("sub") or claims.get("oid")), (claims.get("email") or claims.get("preferred_username") or "").lower()
    user = db.scalar(select(User).where((User.external_id == sub) | (User.email == email)))
    group_roles = client.roles_from_claims(claims)
    if user is None:
        user = User(external_id=sub, email=email, display_name=claims.get("name") or email, roles=group_roles)
        db.add(user)
        audit(db, None, None, "user", "new", "create", after={"email": email, "roles": group_roles})
    elif group_roles:
        user.roles = sorted(set(user.roles or []) | set(group_roles))
    db.commit()
    resp = RedirectResponse("/")
    set_session_cookie(resp, issue_session(user, s), s)
    resp.delete_cookie("oidc_state")
    return resp


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# ------------------------------------------------------------------ вход по логину и паролю (страница входа)
MAX_FAILS, LOCK_MINUTES = 5, 15
_ip_fails: dict[str, list[float]] = {}          # защита от перебора по адресу: 30 ошибок за 15 минут
SUPPORT_TEXT = "Для восстановления доступа обратитесь к администратору системы"


class PasswordLoginIn(BaseModel):
    login: str
    password: str
    remember: bool = False


def _ip_blocked(ip: str) -> bool:
    now = time.time()
    _ip_fails[ip] = [t for t in _ip_fails.get(ip, []) if now - t < LOCK_MINUTES * 60]
    return len(_ip_fails[ip]) >= 30


@router.get("/providers")
def providers(s: Settings = Depends(get_settings)):
    """Какие способы входа доступны странице входа: пароль — всегда; Microsoft 365 — когда настроен OIDC; dev — тестовый стенд."""
    return {"password": True, "m365": s.auth_provider != "dev", "dev": bool(s.is_dev and s.auth_provider == "dev"), "support": SUPPORT_TEXT}


@router.post("/password-login")
def password_login(body: PasswordLoginIn, request: Request, response: Response, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Вход по e-mail (логину) и паролю. Ошибка одна и та же для неизвестного логина и неверного пароля; 5 ошибок — блок на 15 минут."""
    ip = request.client.host if request.client else ""
    if _ip_blocked(ip):
        raise HTTPException(429, f"Слишком много попыток. Повторите через {LOCK_MINUTES} минут")
    login = body.login.strip().lower()
    user = db.scalar(select(User).where(User.email == login)) if login else None
    now = datetime.now(timezone.utc)
    bad = HTTPException(401, "Неверный логин или пароль")
    if user is not None and user.locked_until and user.locked_until > now:
        left = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(429, f"Учётная запись временно заблокирована после нескольких неверных попыток. Повторите через {left} мин.")
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        _ip_fails.setdefault(ip, []).append(time.time())
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= MAX_FAILS:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
                audit(db, user.id, None, "user", user.id, "login_locked", after={"minutes": LOCK_MINUTES})
            db.commit()
        if user is not None and not user.is_active:
            raise HTTPException(401, "Учётная запись отключена")
        if user is not None and not user.password_hash:
            raise HTTPException(401, "Для этой учётной записи пароль не задан — войдите через Microsoft 365 или обратитесь к администратору")
        raise bad
    user.failed_logins, user.locked_until = 0, None
    user.last_seen_at = now
    audit(db, user.id, None, "user", user.id, "login", after={"method": "password", "remember": body.remember})
    db.commit()
    set_session_cookie(response, issue_session(user, s, body.remember), s, body.remember)
    return {"ok": True, "display_name": user.display_name}


class PasswordChangeIn(BaseModel):
    current_password: str = ""
    new_password: str


def _check_strength(pw: str) -> None:
    if len(pw) < 8:
        raise HTTPException(422, "Пароль не короче 8 символов")
    if pw.isdigit() or pw.isalpha():
        raise HTTPException(422, "Пароль должен содержать и буквы, и цифры")


@router.post("/password")
def change_password(body: PasswordChangeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Сотрудник меняет свой пароль (нужен текущий, если он был задан)."""
    u = db.get(User, user.id)
    if u.password_hash and not verify_password(body.current_password, u.password_hash):
        raise HTTPException(403, "Текущий пароль неверный")
    _check_strength(body.new_password)
    u.password_hash, u.password_set_at = hash_password(body.new_password), datetime.now(timezone.utc)
    audit(db, user.id, None, "user", u.id, "password_change")
    db.commit()
    return {"ok": True}


class PasswordSetIn(BaseModel):
    new_password: str


@router.put("/users/{user_id}/password")
def set_password(user_id: str, body: PasswordSetIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Администратор или ЗГД задаёт (сбрасывает) пароль сотруднику — например, при первом входе или если пароль забыт."""
    if not user.has(Role.ADMIN, Role.GD):
        raise HTTPException(403, "Пароль сотруднику задаёт администратор или ЗГД")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Сотрудник не найден")
    _check_strength(body.new_password)
    u.password_hash, u.password_set_at = hash_password(body.new_password), datetime.now(timezone.utc)
    u.failed_logins, u.locked_until = 0, None
    audit(db, user.id, None, "user", u.id, "password_set", after={"by": user.display_name})
    db.commit()
    return {"ok": True, "email": u.email}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "roles": [{"code": r.value, "title": ROLE_TITLES[r]} for r in sorted(user.roles, key=lambda r: r.value)],
            "perms": user.perms}


@router.get("/dev-users")
def dev_users(db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Только dev: список пользователей для переключателя ролей на фронте."""
    if not (s.is_dev and s.auth_provider == "dev"):
        raise HTTPException(404)
    return [{"email": u.email, "display_name": u.display_name, "roles": u.roles} for u in db.scalars(select(User)).all()]


@router.get("/roles")
def roles():
    return [{"code": r.value, "title": t} for r, t in ROLE_TITLES.items()]
