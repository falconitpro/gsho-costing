from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import SESSION_COOKIE, CurrentUser, OidcClient, get_current_user, issue_session, set_session_cookie
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


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "roles": [{"code": r.value, "title": ROLE_TITLES[r]} for r in sorted(user.roles, key=lambda r: r.value)]}


@router.get("/dev-users")
def dev_users(db: Session = Depends(get_db), s: Settings = Depends(get_settings)):
    """Только dev: список пользователей для переключателя ролей на фронте."""
    if not (s.is_dev and s.auth_provider == "dev"):
        raise HTTPException(404)
    return [{"email": u.email, "display_name": u.display_name, "roles": u.roles} for u in db.scalars(select(User)).all()]


@router.get("/roles")
def roles():
    return [{"code": r.value, "title": t} for r, t in ROLE_TITLES.items()]
