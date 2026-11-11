"""Вход через OpenID Connect с переключаемым провайдером (раздел 13 ТЗ).

Провайдер выбирается настройкой AUTH_PROVIDER без изменения прикладного кода:
  entra    — Microsoft Entra ID (первая очередь)
  yandex   — Яндекс ID / Яндекс 360 (OAuth 2.0 + userinfo)
  keycloak — локальный резерв
  dev      — без входа: пользователь берётся из заголовка X-Dev-User (только APP_ENV=dev)

Сессия — подписанный JWT в HttpOnly-cookie, истекает через SESSION_TTL_HOURS бездействия (обновляется
при каждом запросе). Роли берутся из групп провайдера (OIDC_GROUP_ROLE_MAP) или назначаются администратором.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from .config import Settings, get_settings
from .roles import Role

SESSION_COOKIE = "gsho_session"


@dataclass
class ProviderConfig:
    issuer: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    jwks_url: str
    scopes: str
    groups_claim: str


def provider_config(s: Settings) -> ProviderConfig:
    if s.auth_provider == "entra":
        base = f"https://login.microsoftonline.com/{s.graph_tenant_id or 'common'}"
        return ProviderConfig(
            issuer=s.oidc_issuer or f"{base}/v2.0",
            authorize_url=f"{base}/oauth2/v2.0/authorize",
            token_url=f"{base}/oauth2/v2.0/token",
            userinfo_url="https://graph.microsoft.com/oidc/userinfo",
            jwks_url=f"{base}/discovery/v2.0/keys",
            scopes="openid profile email offline_access",
            groups_claim="groups",
        )
    if s.auth_provider == "yandex":
        return ProviderConfig(
            issuer="https://oauth.yandex.ru",
            authorize_url="https://oauth.yandex.ru/authorize",
            token_url="https://oauth.yandex.ru/token",
            userinfo_url="https://login.yandex.ru/info?format=json",
            jwks_url="",
            scopes="login:email login:info",
            groups_claim="",
        )
    # keycloak / generic OIDC через issuer
    iss = s.oidc_issuer.rstrip("/")
    return ProviderConfig(
        issuer=iss,
        authorize_url=f"{iss}/protocol/openid-connect/auth",
        token_url=f"{iss}/protocol/openid-connect/token",
        userinfo_url=f"{iss}/protocol/openid-connect/userinfo",
        jwks_url=f"{iss}/protocol/openid-connect/certs",
        scopes="openid profile email",
        groups_claim="groups",
    )


class OidcClient:
    def __init__(self, s: Settings):
        self.s = s
        self.cfg = provider_config(s)

    def authorize_url(self, state: str) -> str:
        q = {
            "client_id": self.s.oidc_client_id, "response_type": "code", "redirect_uri": self.s.oidc_redirect_uri,
            "scope": self.cfg.scopes, "state": state,
        }
        return f"{self.cfg.authorize_url}?{urlencode(q)}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.s.oidc_redirect_uri,
            "client_id": self.s.oidc_client_id, "client_secret": self.s.oidc_client_secret,
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(self.cfg.token_url, data=data)
            r.raise_for_status()
            tokens = r.json()
            claims: dict[str, Any] = {}
            if tokens.get("id_token") and self.cfg.jwks_url:
                jwks = jwt.PyJWKClient(self.cfg.jwks_url)
                key = jwks.get_signing_key_from_jwt(tokens["id_token"])
                claims = jwt.decode(tokens["id_token"], key.key, algorithms=["RS256"], audience=self.s.oidc_client_id,
                                    options={"verify_iss": False})
            if not claims.get("email"):
                ui = await c.get(self.cfg.userinfo_url, headers={"Authorization": f"Bearer {tokens['access_token']}"}
                                 if self.s.auth_provider != "yandex" else {"Authorization": f"OAuth {tokens['access_token']}"})
                ui.raise_for_status()
                info = ui.json()
                claims = {**info, **claims}
                claims.setdefault("sub", info.get("id") or info.get("sub"))
                claims.setdefault("email", info.get("default_email") or info.get("email"))
                claims.setdefault("name", info.get("real_name") or info.get("name") or info.get("display_name"))
            return claims

    def roles_from_claims(self, claims: dict[str, Any]) -> list[str]:
        mapping = dict(p.split(":", 1) for p in self.s.oidc_group_role_map.split(",") if ":" in p)
        groups = claims.get(self.cfg.groups_claim) or [] if self.cfg.groups_claim else []
        return sorted({mapping[g] for g in groups if g in mapping})


# ------------------------------------------------------------------ сессии (на сервере: services/auth_session.py, docs/operations/04 §2)

def password_version(user: User) -> int:
    """Версия пароля — момент его последней смены (мс). Лежит в токене сессии: смена или сброс пароля отзывает все прежние сессии."""
    from ..services.auth_session import password_version as pv
    return pv(user)


# ------------------------------------------------------------------ пароли (вход по логину и паролю)
_PBKDF2_ITER = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${dk.hex()}"


def needs_rehash(stored: Optional[str]) -> bool:
    try:
        algo, iters, _, _ = (stored or "").split("$")
        return algo == "pbkdf2_sha256" and int(iters) < _PBKDF2_ITER
    except ValueError:
        return False


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


@dataclass
class CurrentUser:
    id: str
    email: str
    display_name: str
    roles: set[Role]
    observer_scope: dict
    perms: dict[str, int] = field(default_factory=dict)   # группа полей → уровень (0/1/2), см. core/roles.py
    acting_for: set[str] = field(default_factory=set)      # сотрудники, которых пользователь сейчас замещает (заместитель на период)
    sid: str = ""                                          # текущая сессия (AuthSession.sid)
    auth_method: str = ""
    mfa_level: str = "none"
    step_up_fresh: bool = False                            # подтверждение опасного действия не старше 10 минут
    password_must_change: bool = False
    mfa_enabled: bool = False
    mfa_setup_required: bool = False                       # MFA_POLICY=required, а TOTP ещё не настроен

    def has(self, *roles: Role) -> bool:
        return bool(self.roles & set(roles))

    def is_manager_of(self, manager_id) -> bool:
        """Менеджер сделки — сам или как действующий заместитель."""
        return bool(manager_id) and (manager_id == self.id or manager_id in self.acting_for)

    def can(self, group: str, level: int = 1) -> bool:
        """Право по группе доступа: 1 — видит, 2 — редактирует (настраивается администратором/ЗГД)."""
        return self.perms.get(group, 0) >= level


# Пока не сменён временный пароль или не настроен обязательный TOTP — доступны только эти адреса (04 §3.3, §4)
_LIMITED_OK = {"/api/auth/me", "/api/auth/session-status", "/api/auth/logout", "/api/auth/providers", "/api/auth/password", "/api/auth/change-password",
               "/api/auth/mfa/setup", "/api/auth/mfa/confirm", "/api/auth/step-up", "/api/auth/dev-users"}


def _legacy_allowed(s: Settings) -> bool:
    from datetime import datetime, timezone
    if not s.legacy_sessions_until:
        return False
    try:
        until = datetime.fromisoformat(s.legacy_sessions_until.replace("Z", "+00:00"))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < until


async def get_current_user(request: Request, response: Response, db: Session = Depends(get_db),
                           s: Settings = Depends(get_settings)) -> CurrentUser:
    from ..services import auth_session as sess
    from ..services import mfa as mfa_svc
    user: Optional[User] = None
    row = None
    if s.auth_provider == "dev" and s.is_dev:
        email = request.headers.get("X-Dev-User", "")
        if email:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None and user.is_active:
                # dev-стенд: «Войти как» — своя серверная сессия для выбранного пользователя (одна на пользователя, переиспользуется)
                row = next((r for r in sess.active(db, user.id) if r.auth_method == "dev"), None)
                if row is None:
                    row = sess.create(db, user, s, method="dev", request=request)
                    db.commit()
                sess.set_cookie(response, row, user, s)
            else:
                user = None
    if user is None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
        try:
            claims = jwt.decode(token, s.secret_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
        user = db.get(User, claims.get("sub"))
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётная запись отключена")
        if claims.get("pv", 0) != password_version(user):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пароль изменён — войдите заново")
        if not claims.get("sid"):
            # старый вход без серверной сессии — только в явно заданное окно перехода (LEGACY_SESSIONS_UNTIL), затем отказ
            if not _legacy_allowed(s):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия устарела после обновления системы — войдите заново")
            row = sess.create(db, user, s, method="legacy", request=request)
            db.commit()
            sess.set_cookie(response, row, user, s)
        else:
            try:
                row = sess.validate(db, claims, user, s)
            except sess.SessionInvalid as e:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, e.message)
            # статус сессии для предупреждения «скоро выход» не считается активностью — иначе опрос продлевал бы вход бесконечно
            background = request.headers.get("x-background-request") == "1" or request.url.path == "/api/auth/session-status"
            if not background and sess.touch(db, row, s):
                db.commit()
    from ..services.access_service import acting_for, perms_for
    mfa_on = mfa_svc.enabled(db, user.id)
    cu = CurrentUser(id=user.id, email=user.email, display_name=user.display_name,
                     roles={Role(r) for r in user.roles}, observer_scope=user.observer_scope or {}, perms=perms_for(db, user), acting_for=acting_for(db, user),
                     sid=row.sid if row else "", auth_method=row.auth_method if row else "", mfa_level=row.mfa_level if row else "none",
                     step_up_fresh=sess.step_up_fresh(row), password_must_change=bool(user.password_must_change), mfa_enabled=mfa_on,
                     mfa_setup_required=(s.mfa_policy == "required" and not mfa_on))
    if (cu.password_must_change or cu.mfa_setup_required) and request.url.path not in _LIMITED_OK:
        code = "password_change_required" if cu.password_must_change else "mfa_setup_required"
        msg = "Смените временный пароль, чтобы продолжить" if cu.password_must_change else "Настройте вход с кодом из приложения, чтобы продолжить"
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": code, "message": msg})
    return cu


def require_step_up(user: CurrentUser) -> None:
    """Опасное действие (отключение сотрудника, сброс TOTP, завершение чужих сессий): подтверждение не старше 10 минут."""
    if not user.step_up_fresh:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "step_up_required", "message": "Подтвердите действие кодом из приложения или паролем"})


def require_roles(*roles: Role):
    async def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if roles and not user.has(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user
    return dep
