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

from ..core.auth import SESSION_COOKIE, CurrentUser, OidcClient, get_current_user, hash_password, needs_rehash, verify_password
from ..services import auth_session as sess
from ..services import mfa as mfa_svc
from ..services import password_policy
from ..services import security_events as sev
from ..core.config import Settings, get_settings
from ..core.roles import ROLE_TITLES, Role
from ..db import get_db
from ..models import User
from ..services.deal_service import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
OIDC_PROVIDERS = ("entra", "yandex", "keycloak")   # AUTH_PROVIDER=password — только логин и пароль (+ код из приложения)


@router.get("/login")
def login(s: Settings = Depends(get_settings)):
    if s.auth_provider == "dev":
        raise HTTPException(400, "В dev-режиме используйте заголовок X-Dev-User (см. /api/auth/dev-users)")
    if s.auth_provider not in OIDC_PROVIDERS:
        raise HTTPException(404, "Вход через внешнюю учётную запись не подключён — используйте логин и пароль")
    state = secrets.token_urlsafe(16)
    resp = RedirectResponse(OidcClient(s).authorize_url(state))
    resp.set_cookie("oidc_state", state, httponly=True, max_age=600)
    return resp


@router.get("/callback")
async def callback(request: Request, code: str, state: str, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    if s.auth_provider not in OIDC_PROVIDERS:
        raise HTTPException(404, "Вход через внешнюю учётную запись не подключён")
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
    if not user.is_active:
        sev.record(db, "auth", "login", result="denied", subject_id=user.id, request=request, meta={"method": "oidc", "reason": "inactive"})
        db.commit()
        raise HTTPException(401, "Учётная запись отключена")
    if mfa_svc.enabled(db, user.id):
        # наличие корпоративного входа не равно второму фактору (04 §3.3): включённый TOTP проверяется и здесь
        tok = mfa_svc.create_challenge(db, user, False, "oidc", sev.ip_prefix(request))
        db.commit()
        resp = RedirectResponse("/login?mfa=1")
        _set_challenge_cookie(resp, tok, s)
        resp.delete_cookie("oidc_state")
        return resp
    resp = RedirectResponse("/")
    row = sess.issue(db, resp, user, s, method="oidc", request=request)
    sev.record(db, "auth", "login", subject_id=user.id, actor_id=user.id, request=request, session_id=row.sid, meta={"method": "oidc"})
    db.commit()
    resp.delete_cookie("oidc_state")
    return resp


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Выход отзывает текущую сессию на сервере: повторно отправленный тот же cookie даёт 401 (04 §2.3)."""
    import jwt as _jwt
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        try:
            claims = _jwt.decode(tok, s.secret_key, algorithms=["HS256"])
            row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == str(claims.get("sid") or ""))) if claims.get("sid") else None
            if row is not None and row.user_id == claims.get("sub") and sess.revoke(db, row, "logout", row.user_id):
                sev.record(db, "session", "logout", actor_id=row.user_id, subject_id=row.user_id, request=request, session_id=row.sid)
                db.commit()
        except _jwt.PyJWTError:
            pass
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(mfa_svc.CHALLENGE_COOKIE, path="/api/auth")
    return {"ok": True}


def _set_challenge_cookie(resp: Response, token: str, s: Settings) -> None:
    resp.set_cookie(mfa_svc.CHALLENGE_COOKIE, token, httponly=True, secure=not s.is_dev, samesite="strict", path="/api/auth",
                    max_age=int(mfa_svc.CHALLENGE_TTL.total_seconds()))


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
    return {"password": True, "m365": s.auth_provider in OIDC_PROVIDERS, "dev": bool(s.is_dev and s.auth_provider == "dev"), "support": SUPPORT_TEXT}


@router.post("/password-login")
def password_login(body: PasswordLoginIn, request: Request, response: Response, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Вход по e-mail (логину) и паролю. Ошибка одна и та же для неизвестного логина и неверного пароля; 5 ошибок — блок на 15 минут."""
    ip = request.client.host if request.client else ""     # в рабочем контуре uvicorn берёт адрес клиента из X-Forwarded-For от Caddy (FORWARDED_ALLOW_IPS)
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
        sev.record(db, "auth", "login", result="denied", subject_id=user.id if user else None, request=request,
                   meta={"method": "password", "reason": "inactive" if user is not None and not user.is_active else "bad_credentials"})
        db.commit()
        if user is not None and not user.is_active:
            raise HTTPException(401, "Учётная запись отключена")
        if user is not None and not user.password_hash:
            raise HTTPException(401, "Для этой учётной записи пароль не задан — войдите через Microsoft 365 или обратитесь к администратору")
        raise bad
    user.failed_logins, user.locked_until = 0, None
    user.last_seen_at = now
    if needs_rehash(user.password_hash):                       # прозрачное усиление старого хэша (04 §4)
        user.password_hash = hash_password(body.password)
    if mfa_svc.enabled(db, user.id):
        tok = mfa_svc.create_challenge(db, user, body.remember, "password", sev.ip_prefix(request))
        sev.record(db, "auth", "password_ok_mfa_pending", subject_id=user.id, request=request)
        db.commit()
        _set_challenge_cookie(response, tok, s)
        return {"ok": True, "mfa_required": True}
    row = sess.issue(db, response, user, s, remember=body.remember, method="password", request=request)
    audit(db, user.id, None, "user", user.id, "login", after={"method": "password", "remember": body.remember})
    sev.record(db, "auth", "login", actor_id=user.id, subject_id=user.id, request=request, session_id=row.sid, meta={"method": "password", "remember": body.remember})
    db.commit()
    return {"ok": True, "display_name": user.display_name, "mfa_required": False}


class MfaCodeIn(BaseModel):
    code: str


def _finish_challenge(db: Session, request: Request, response: Response, s: Settings, code: str, recovery: bool) -> dict:
    ch = mfa_svc.load_challenge(db, request.cookies.get(mfa_svc.CHALLENGE_COOKIE, ""))
    user = db.get(User, ch.user_id)
    if user is None or not user.is_active:
        ch.consumed_at = sess.now()
        db.commit()
        raise HTTPException(401, "Учётная запись отключена")
    left = None
    ok = False
    if recovery:
        left = mfa_svc.use_recovery(db, user.id, code)
        ok = left is not None
    else:
        ok = mfa_svc.verify_totp(db, user.id, code)
    if not ok:
        ch.attempts += 1
        sev.record(db, "mfa", "recovery_failed" if recovery else "totp_failed", result="denied", subject_id=user.id, request=request)
        db.commit()
        raise HTTPException(401, {"code": "mfa_invalid", "message": "Код не подошёл" + (" — проверьте код восстановления" if recovery else ". Введите текущий код из приложения")})
    ch.consumed_at = sess.now()
    level = "recovery" if recovery else "totp"
    row = sess.issue(db, response, user, s, remember=ch.remember, method=ch.auth_method, mfa_level=level, request=request)
    audit(db, user.id, None, "user", user.id, "login", after={"method": ch.auth_method, "mfa": level})
    sev.record(db, "auth", "login", actor_id=user.id, subject_id=user.id, request=request, session_id=row.sid,
               meta={"method": ch.auth_method, "mfa": level, "recovery_left": left})
    db.commit()
    response.delete_cookie(mfa_svc.CHALLENGE_COOKIE, path="/api/auth")
    out = {"ok": True, "display_name": user.display_name}
    if recovery:
        out["recovery_left"] = left
    return out


@router.post("/mfa/verify")
def mfa_verify(body: MfaCodeIn, request: Request, response: Response, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Второй шаг входа: 6-значный код из приложения. Полноценная сессия выдаётся только здесь."""
    return _finish_challenge(db, request, response, s, body.code, recovery=False)


@router.post("/mfa/recovery")
def mfa_recovery_login(body: MfaCodeIn, request: Request, response: Response, db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Второй шаг входа одноразовым кодом восстановления вместо кода из приложения."""
    return _finish_challenge(db, request, response, s, body.code, recovery=True)


class PasswordChangeIn(BaseModel):
    current_password: str = ""
    new_password: str


def _set_password(db: Session, u: User, new_password: str) -> None:
    password_policy.validate(db, u, new_password)
    password_policy.remember_old(db, u)
    u.password_hash, u.password_set_at = hash_password(new_password), datetime.now(timezone.utc)


@router.post("/password")
@router.post("/change-password")
def change_password(body: PasswordChangeIn, request: Request, response: Response, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user),
                    s: Settings = Depends(get_settings)):
    """Сотрудник меняет свой пароль (нужен текущий, если он был задан). Остальные его сессии отзываются, текущая продолжается."""
    u = db.get(User, user.id)
    if u.password_hash and not verify_password(body.current_password, u.password_hash):
        sev.record(db, "password", "change", result="denied", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid)
        db.commit()
        raise HTTPException(403, "Текущий пароль неверный")
    _set_password(db, u, body.new_password)
    u.password_must_change = False
    n = sess.revoke_all(db, u.id, "password", u.id, except_sid=user.sid)
    audit(db, user.id, None, "user", u.id, "password_change")
    sev.record(db, "password", "change", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid, meta={"sessions_revoked": n})
    db.flush()
    cur = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid)) if user.sid else None
    if cur is not None and cur.revoked_at is None:
        sess.set_cookie(response, cur, u, s)       # новый pv в токене текущей сессии — вход не прерывается
    db.commit()
    return {"ok": True, "other_sessions_revoked": n}


class PasswordSetIn(BaseModel):
    new_password: str


@router.put("/users/{user_id}/password")
def set_password(user_id: str, body: PasswordSetIn, request: Request, response: Response, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user), s: Settings = Depends(get_settings)):
    """Администратор или ЗГД задаёт сотруднику временный пароль (04 §4): сотрудник обязан сменить его при входе,
    все прежние сессии отзываются. Пароль не попадает в журнал."""
    if not user.has(Role.ADMIN, Role.GD):
        raise HTTPException(403, "Пароль сотруднику задаёт администратор или ЗГД")
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "Сотрудник не найден")
    _set_password(db, u, body.new_password)
    u.failed_logins, u.locked_until = 0, None
    own = u.id == user.id
    u.password_must_change = not own
    n = sess.revoke_all(db, u.id, "password", user.id, except_sid=user.sid if own else None)
    audit(db, user.id, None, "user", u.id, "password_set", after={"by": user.display_name, "must_change": not own})
    sev.record(db, "password", "admin_reset", actor_id=user.id, subject_id=u.id, request=request, session_id=user.sid, meta={"sessions_revoked": n})
    db.flush()
    if own:
        cur = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid))
        if cur is not None:
            sess.set_cookie(response, cur, u, s)
    db.commit()
    return {"ok": True, "email": u.email, "must_change": not own, "sessions_revoked": n}


# ------------------------------------------------------------------ личные сессии (09 §4)

@router.get("/sessions")
def my_sessions(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return {"items": [sess.dto(r, user.sid) for r in sess.active(db, user.id)]}


@router.delete("/sessions/{sid}")
def revoke_my_session(sid: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == sid, sess.AuthSession.user_id == user.id))
    if row is None or row.revoked_at is not None:
        raise HTTPException(404, "Сессия не найдена или уже завершена")
    if row.sid == user.sid:
        raise HTTPException(409, "Это текущая сессия — для неё используйте «Выйти»")
    sess.revoke(db, row, "user", user.id)
    sev.record(db, "session", "revoke", actor_id=user.id, subject_id=user.id, request=request, session_id=user.sid, entity_type="session", entity_id=row.sid)
    db.commit()
    return {"ok": True, "items": [sess.dto(r, user.sid) for r in sess.active(db, user.id)]}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    n = sess.revoke_all(db, user.id, "user", user.id, except_sid=user.sid)
    sev.record(db, "session", "revoke_others", actor_id=user.id, subject_id=user.id, request=request, session_id=user.sid, meta={"count": n})
    db.commit()
    return {"ok": True, "revoked": n, "items": [sess.dto(r, user.sid) for r in sess.active(db, user.id)]}


# ------------------------------------------------------------------ подтверждение опасных действий (step-up)

class StepUpIn(BaseModel):
    code: str = ""
    password: str = ""


@router.post("/step-up")
def step_up(body: StepUpIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user), s: Settings = Depends(get_settings)):
    """Подтверждение на 10 минут: код из приложения, если TOTP включён, иначе пароль. На dev-стенде под «Войти как»
    у демо-учётных записей без пароля и TOTP — подтверждение без ввода (только APP_ENV=dev, D-034)."""
    row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid)) if user.sid else None
    if row is None:
        raise HTTPException(401, "Требуется вход")
    u = db.get(User, user.id)
    if user.mfa_enabled:
        ok = mfa_svc.verify_totp(db, u.id, body.code)
        how = "totp"
    elif u.password_hash:
        ok = verify_password(body.password, u.password_hash)
        how = "password"
    else:
        ok = s.is_dev and row.auth_method == "dev"
        how = "dev"
    if not ok:
        sev.record(db, "auth", "step_up", result="denied", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid, meta={"method": how})
        db.commit()
        raise HTTPException(401, {"code": "step_up_invalid", "message": "Код не подошёл" if how == "totp" else "Пароль неверный" if how == "password" else "Подтверждение недоступно"})
    row.last_step_up_at = sess.now()
    if how == "totp":
        row.last_mfa_at = row.last_step_up_at
    sev.record(db, "auth", "step_up", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid, meta={"method": how})
    db.commit()
    return {"ok": True, "method": how, "valid_minutes": int(sess.STEP_UP_TTL.total_seconds() // 60)}


@router.get("/step-up")
def step_up_method(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    u = db.get(User, user.id)
    method = "totp" if user.mfa_enabled else "password" if u.password_hash else ("dev" if s.is_dev and user.auth_method == "dev" else "none")
    return {"method": method, "fresh": user.step_up_fresh}


# ------------------------------------------------------------------ TOTP: настройка (09 §4)

class MfaSetupIn(BaseModel):
    password: str = ""


@router.post("/mfa/setup")
def mfa_setup(body: MfaSetupIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Начать настройку: нужен текущий пароль (или свежее подтверждение). Секрет показывается только в этом ответе."""
    u = db.get(User, user.id)
    if not user.step_up_fresh:
        if u.password_hash and not verify_password(body.password, u.password_hash):
            sev.record(db, "mfa", "setup", result="denied", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid)
            db.commit()
            raise HTTPException(403, {"code": "password_required", "message": "Введите текущий пароль"})
        if not u.password_hash and not (user.auth_method in ("dev", "oidc")):
            raise HTTPException(403, {"code": "step_up_required", "message": "Подтвердите действие"})
    out = mfa_svc.start_setup(db, u)
    sev.record(db, "mfa", "setup_started", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid)
    db.commit()
    return {**out, "replaces_existing": user.mfa_enabled}


@router.post("/mfa/confirm")
def mfa_confirm(body: MfaCodeIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    u = db.get(User, user.id)
    codes = mfa_svc.confirm_setup(db, u, body.code)
    row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid)) if user.sid else None
    if row is not None:
        row.mfa_level, row.last_mfa_at, row.last_step_up_at = "totp", sess.now(), sess.now()
    audit(db, user.id, None, "user", u.id, "mfa_enabled")
    sev.record(db, "mfa", "enabled", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid)
    db.commit()
    return {"ok": True, "recovery_codes": codes, "recovery_left": len(codes)}


@router.post("/mfa/recovery/regenerate")
def mfa_regenerate(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..core.auth import require_step_up
    if not user.mfa_enabled:
        raise HTTPException(409, "Вход с кодом из приложения не включён")
    require_step_up(user)
    codes = mfa_svc.regenerate_recovery(db, user.id)
    sev.record(db, "mfa", "recovery_regenerated", actor_id=user.id, subject_id=user.id, request=request, session_id=user.sid)
    db.commit()
    return {"ok": True, "recovery_codes": codes, "recovery_left": len(codes)}


class MfaDisableIn(BaseModel):
    password: str = ""
    code: str


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisableIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user),
                s: Settings = Depends(get_settings)):
    """Отключить свой TOTP можно только при MFA_POLICY=optional: пароль (если задан) + текущий код."""
    if s.mfa_policy == "required":
        raise HTTPException(409, "Вход с кодом обязателен в организации — отключить нельзя; при потере телефона обратитесь к администратору")
    u = db.get(User, user.id)
    if u.password_hash and not verify_password(body.password, u.password_hash):
        raise HTTPException(403, "Пароль неверный")
    if not mfa_svc.verify_totp(db, u.id, body.code):
        db.commit()
        raise HTTPException(401, {"code": "mfa_invalid", "message": "Код не подошёл"})
    mfa_svc.disable(db, u.id)
    row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid)) if user.sid else None
    if row is not None:
        row.mfa_level = "none"
    audit(db, user.id, None, "user", u.id, "mfa_disabled")
    sev.record(db, "mfa", "disabled", actor_id=u.id, subject_id=u.id, request=request, session_id=user.sid)
    db.commit()
    return {"ok": True}


@router.get("/session-status")
def session_status(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user), s: Settings = Depends(get_settings)):
    """Когда закончится текущий вход: для предупреждения за 5 минут. Сам запрос активностью не считается (D-041)."""
    row = db.scalar(select(sess.AuthSession).where(sess.AuthSession.sid == user.sid)) if user.sid else None
    if row is None:
        return {"session": False}
    now = sess.now()
    idle_end, abs_end = sess.effective(row, s)
    ends = min(idle_end, abs_end)
    return {"session": True, "now": now.isoformat(), "idle_expires_at": idle_end.isoformat(),
            "absolute_expires_at": abs_end.isoformat(), "ends_at": ends.isoformat(),
            "reason": "absolute" if abs_end <= idle_end else "idle",
            "seconds_left": max(0, int((ends - now).total_seconds()))}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "roles": [{"code": r.value, "title": ROLE_TITLES[r]} for r in sorted(user.roles, key=lambda r: r.value)],
            "perms": user.perms,
            "security": {"mfa_enabled": user.mfa_enabled, "mfa_policy": s.mfa_policy, "mfa_required": s.mfa_policy == "required",
                         "mfa_setup_required": user.mfa_setup_required, "mfa_available": mfa_svc.available(s), "mfa_key_status": mfa_svc.key_status(s),
                         "password_must_change": user.password_must_change,
                         "recovery_left": mfa_svc.recovery_left(db, user.id) if user.mfa_enabled else 0,
                         "session_mfa": user.mfa_level}}


@router.get("/dev-users")
def dev_users(db: Session = Depends(get_db), s: Settings = Depends(get_settings), _: CurrentUser = Depends(get_current_user)):
    """Только dev и только после входа: список учётных записей для переключателя «Войти как» в шапке."""
    if not (s.is_dev and s.auth_provider == "dev"):
        raise HTTPException(404)
    return [{"email": u.email, "display_name": u.display_name, "roles": u.roles} for u in db.scalars(select(User)).all()]


@router.get("/roles")
def roles():
    return [{"code": r.value, "title": t} for r, t in ROLE_TITLES.items()]
