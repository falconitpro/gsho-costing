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


# ------------------------------------------------------------------ сессии

REMEMBER_DAYS = 30


def password_version(user: User) -> int:
    """Версия пароля — момент его последней смены (мс). Лежит в токене сессии: смена или сброс пароля отзывает все прежние сессии."""
    at = user.password_set_at
    return int(at.timestamp() * 1000) if at else 0


def issue_session(user: User, s: Settings, remember: bool = False) -> str:
    """Сессия: 8 ч скользящего срока; «Запомнить меня» — 30 дней (пароль нигде не хранится, только подписанный JWT)."""
    now = int(time.time())
    ttl = REMEMBER_DAYS * 86400 if remember else s.session_ttl_hours * 3600
    claims = {"sub": user.id, "iat": now, "exp": now + ttl, "nonce": secrets.token_hex(4), "pv": password_version(user)}
    if remember:
        claims["rem"] = 1
    return jwt.encode(claims, s.secret_key, algorithm="HS256")


def reissue_session(request: Request, resp: Response, user: User, s: Settings) -> None:
    """Выдать сотруднику новую сессию после смены им своего пароля (сохраняя «Запомнить меня»), чтобы текущий вход не прервался."""
    remember = False
    try:
        remember = bool(jwt.decode(request.cookies.get(SESSION_COOKIE, ""), s.secret_key, algorithms=["HS256"]).get("rem"))
    except jwt.PyJWTError:
        pass
    set_session_cookie(resp, issue_session(user, s, remember), s, remember)


def set_session_cookie(resp: Response, token: str, s: Settings, remember: bool = False) -> None:
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, secure=not s.is_dev, samesite="lax",
                    max_age=REMEMBER_DAYS * 86400 if remember else s.session_ttl_hours * 3600)


# ------------------------------------------------------------------ пароли (вход по логину и паролю)
_PBKDF2_ITER = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt}${dk.hex()}"


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

    def has(self, *roles: Role) -> bool:
        return bool(self.roles & set(roles))

    def is_manager_of(self, manager_id) -> bool:
        """Менеджер сделки — сам или как действующий заместитель."""
        return bool(manager_id) and (manager_id == self.id or manager_id in self.acting_for)

    def can(self, group: str, level: int = 1) -> bool:
        """Право по группе доступа: 1 — видит, 2 — редактирует (настраивается администратором/ЗГД)."""
        return self.perms.get(group, 0) >= level


async def get_current_user(request: Request, response: Response, db: Session = Depends(get_db),
                           s: Settings = Depends(get_settings)) -> CurrentUser:
    user: Optional[User] = None
    if s.auth_provider == "dev" and s.is_dev:
        email = request.headers.get("X-Dev-User", "")
        if email:
            user = db.scalar(select(User).where(User.email == email))
            if user is not None and user.is_active:
                # dev-стенд: сессия и для обычных ссылок (скачивание КП, Excel, фото) — от выбранного пользователя
                set_session_cookie(response, issue_session(user, s), s)
    if user is None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
        try:
            claims = jwt.decode(token, s.secret_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
        user = db.get(User, claims["sub"])
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётная запись отключена")
        if claims.get("pv", 0) != password_version(user):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пароль изменён — войдите заново")
        remember = bool(claims.get("rem"))
        set_session_cookie(response, issue_session(user, s, remember), s, remember)   # скользящий срок: 8 ч или 30 дней («Запомнить меня»)
    from ..services.access_service import acting_for, perms_for
    return CurrentUser(id=user.id, email=user.email, display_name=user.display_name,
                       roles={Role(r) for r in user.roles}, observer_scope=user.observer_scope or {}, perms=perms_for(db, user), acting_for=acting_for(db, user))


def require_roles(*roles: Role):
    async def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if roles and not user.has(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user
    return dep
