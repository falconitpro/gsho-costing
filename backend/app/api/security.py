"""Безопасность сотрудников (docs/operations/09 §5): сессии, сброс TOTP, временный пароль, отключение и включение доступа,
журнал событий. Чтение — security_admin ≥ READ; действия — security_admin = EDIT и подтверждение не старше 10 минут."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
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


def _parse_day(v: Optional[str], end: bool = False) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = date.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, "Дата в формате ГГГГ-ММ-ДД")
    from datetime import time as _time, timedelta as _td
    from zoneinfo import ZoneInfo
    t = datetime.combine(d, _time.min, tzinfo=ZoneInfo("Europe/Moscow"))
    return t + _td(days=1) if end else t


EVENT_TYPE_TITLES = {"auth": "Вход", "session": "Сессии", "mfa": "Код из приложения", "password": "Пароль", "offboard": "Отключение доступа",
                     "access": "Права доступа", "download": "Скачивание файла", "export": "Выгрузка", "alert": "Предупреждения", "backup": "Резервные копии",
                     "file": "Проверка файла", "scanner": "Антивирус"}


def _event_dto(db: Session, e: SecurityEvent, names: dict) -> dict:
    def nm(uid):
        if not uid:
            return None
        if uid not in names:
            u = db.get(User, uid)
            names[uid] = u.display_name if u else uid
        return names[uid]
    return {"id": e.id, "at": e.occurred_at.isoformat() if e.occurred_at else None, "type": e.event_type, "type_title": EVENT_TYPE_TITLES.get(e.event_type, e.event_type),
            "action": e.action, "result": e.result, "actor_user_id": e.actor_user_id, "actor_name": nm(e.actor_user_id),
            "subject_user_id": e.subject_user_id, "subject_name": nm(e.subject_user_id), "ip_prefix": e.ip_prefix, "request_id": e.request_id,
            "entity_type": e.entity_type, "entity_id": e.entity_id, "deal_id": e.deal_id, "format": e.format, "row_count": e.row_count,
            "byte_size": e.byte_size, "alert_id": e.alert_id, "meta": e.meta or {}}


@router.get("/events")
def events(user_id: Optional[str] = None, event_type: Optional[str] = None, result: Optional[str] = None, date_from: Optional[str] = None,
           date_to: Optional[str] = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Журнал событий (security_audit ≥ READ): вход, сессии, права, скачивания и выгрузки — без содержимого файлов."""
    if not user.can("security_audit", 1):
        raise HTTPException(403, "Нет доступа к журналу безопасности")
    q = select(SecurityEvent)
    if user_id:
        q = q.where((SecurityEvent.actor_user_id == user_id) | (SecurityEvent.subject_user_id == user_id))
    if event_type:
        q = q.where(SecurityEvent.event_type == event_type)
    if result in ("success", "denied", "error"):
        q = q.where(SecurityEvent.result == result)
    if (f := _parse_day(date_from)) is not None:
        q = q.where(SecurityEvent.occurred_at >= f)
    if (t := _parse_day(date_to, end=True)) is not None:
        q = q.where(SecurityEvent.occurred_at < t)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(SecurityEvent.occurred_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
    names: dict = {}
    return {"total": int(total), "items": [_event_dto(db, e, names) for e in rows], "types": EVENT_TYPE_TITLES}


# ------------------------------------------------------------------ пересмотр прав (04 §6, 13 §10)

from datetime import date as _date  # noqa: E402

from ..models import AccessReviewCampaign, AccessReviewItem  # noqa: E402
from ..services import access_review as ar  # noqa: E402


class CampaignIn(BaseModel):
    title: str = ""
    period_start: _date
    period_end: _date
    due_date: _date
    roles: list[str] = []


class DecisionIn(BaseModel):
    decision: str
    comment: str = ""


class CompleteIn(BaseModel):
    note: str = ""


def _campaign(db: Session, cid: str) -> AccessReviewCampaign:
    c = db.get(AccessReviewCampaign, cid)
    if c is None:
        raise HTTPException(404, "Кампания не найдена")
    return c


def _campaign_dto(db: Session, c: AccessReviewCampaign) -> dict:
    return {"id": c.id, "title": c.title, "period_start": c.period_start.isoformat(), "period_end": c.period_end.isoformat(),
            "due_date": c.due_date.isoformat(), "status": c.status, "scope": c.scope or {}, "created_at": c.created_at.isoformat() if c.created_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None, "completion_note": c.completion_note, "progress": ar.progress(db, c)}


@router.get("/access-reviews")
def list_reviews(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    rows = db.scalars(select(AccessReviewCampaign).order_by(AccessReviewCampaign.created_at.desc())).all()
    return {"items": [_campaign_dto(db, c) for c in rows], "can_edit": user.can("security_admin", 2)}


@router.post("/access-reviews", status_code=201)
def create_review(body: CampaignIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = ar.create(db, user, body.title, body.period_start, body.period_end, body.due_date, body.roles or None)
    return _campaign_dto(db, c)


@router.get("/access-reviews/{cid}")
def get_review(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    c = _campaign(db, cid)
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out = []
    for i in db.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == c.id)).all():
        u = db.get(User, i.user_id)
        cur = ar.snapshot(db, u)
        snap = i.snapshot or {}
        out.append({"id": i.id, "user_id": i.user_id, "snapshot": snap, "current_diff": ar.diff(snap, cur), "decision": i.decision,
                    "reviewer": names.get(i.reviewer_id or "", ""), "reviewed_at": i.reviewed_at.isoformat() if i.reviewed_at else None,
                    "comment": i.comment, "changes": i.changes or {}, "is_self": i.user_id == user.id,
                    "second_reviewer_needed": i.user_id == user.id and ar.needs_second_reviewer(snap)})
    out.sort(key=lambda x: (x["decision"] != "pending", x["snapshot"].get("display_name", "")))
    from ..core.roles import FIELD_GROUPS
    return {**_campaign_dto(db, c), "items": out, "can_edit": user.can("security_admin", 2),
            "group_titles": {g: t for g, (t, _) in FIELD_GROUPS.items()}, "sensitive_groups": list(ar.SENSITIVE)}


@router.post("/access-reviews/{cid}/items/{iid}/decision")
def decide_item(cid: str, iid: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    item = db.get(AccessReviewItem, iid)
    if item is None or item.campaign_id != cid:
        raise HTTPException(404, "Сотрудник в кампании не найден")
    item = ar.decide(db, user, item, body.decision, body.comment)
    return {"ok": True, "decision": item.decision, "changes": item.changes}


@router.post("/access-reviews/{cid}/items/{iid}/reopen")
def reopen_item(cid: str, iid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    item = db.get(AccessReviewItem, iid)
    if item is None or item.campaign_id != cid:
        raise HTTPException(404, "Сотрудник в кампании не найден")
    ar.reopen(db, user, item)
    return {"ok": True}


@router.post("/access-reviews/{cid}/complete")
def complete_review(cid: str, body: CompleteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    return _campaign_dto(db, ar.complete(db, user, _campaign(db, cid), body.note))


@router.post("/access-reviews/{cid}/cancel")
def cancel_review(cid: str, body: CompleteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    c = _campaign(db, cid)
    if c.status != "open":
        raise HTTPException(409, "Кампания уже закрыта")
    if len((body.note or "").strip()) < 5:
        raise HTTPException(422, "Укажите причину отмены")
    c.status, c.completed_at, c.completed_by, c.completion_note = "cancelled", ar._now(), user.id, body.note.strip()[:2000]
    sev.record(db, "access", "review_cancelled", actor_id=user.id, session_id=user.sid, entity_type="access_review", entity_id=c.id)
    db.commit()
    return _campaign_dto(db, c)


# ------------------------------------------------------------------ предупреждения безопасности (04 §7.3, 09 §5)

from ..models import SecurityAlert  # noqa: E402
from ..services import security_alerts as alerts_svc  # noqa: E402


class AlertActionIn(BaseModel):
    comment: str


def _audit_reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("security_audit", 1):
        raise HTTPException(403, "Нет доступа к журналу безопасности")
    return user


@router.get("/alerts")
def alerts(status: Optional[str] = None, severity: Optional[str] = None, user_id: Optional[str] = None, rule: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 50, offset: int = 0,
           db: Session = Depends(get_db), user: CurrentUser = Depends(_audit_reader)):
    q = select(SecurityAlert)
    if status == "open":
        q = q.where(SecurityAlert.status != "resolved")
    elif status in ("new", "acknowledged", "resolved"):
        q = q.where(SecurityAlert.status == status)
    if severity in alerts_svc.SEVERITY_RANK:
        q = q.where(SecurityAlert.severity == severity)
    if user_id:
        q = q.where(SecurityAlert.subject_user_id == user_id)
    if rule in alerts_svc.RULES:
        q = q.where(SecurityAlert.rule_key == rule)
    if (f := _parse_day(date_from)) is not None:
        q = q.where(SecurityAlert.last_seen_at >= f)
    if (t := _parse_day(date_to, end=True)) is not None:
        q = q.where(SecurityAlert.first_seen_at < t)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    open_counts = dict(db.execute(select(SecurityAlert.severity, func.count()).where(SecurityAlert.status != "resolved").group_by(SecurityAlert.severity)).all())
    rows = db.scalars(q.order_by(SecurityAlert.last_seen_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
    names: dict = {}
    return {"total": int(total), "items": [alerts_svc.dto(db, a, names) for a in rows],
            "open_by_severity": {k: int(open_counts.get(k, 0)) for k in alerts_svc.SEVERITY_RANK},
            "rules": [{"key": r.key, "title": r.title, "severity": r.severity} for r in alerts_svc.RULES.values()],
            "rules_version": alerts_svc.RULES_VERSION, "can_act": user.can("security_admin", 2), "step_up_fresh": user.step_up_fresh}


@router.get("/alerts/{aid}")
def alert_card(aid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_audit_reader)):
    a = db.get(SecurityAlert, aid)
    if a is None:
        raise HTTPException(404, "Предупреждение не найдено")
    evs = db.scalars(select(SecurityEvent).where(SecurityEvent.alert_id == a.id).order_by(SecurityEvent.occurred_at.desc()).limit(50)).all()
    names: dict = {}
    return {**alerts_svc.dto(db, a, names), "events": [_event_dto(db, e, names) for e in evs],
            "can_act": user.can("security_admin", 2), "step_up_fresh": user.step_up_fresh}


def _alert_action(db: Session, aid: str, body: AlertActionIn, user: CurrentUser, request: Request, action: str) -> dict:
    if not user.can("security_admin", 2):
        raise HTTPException(403, "Отметить предупреждение может администратор безопасности")
    comment = (body.comment or "").strip()
    if len(comment) < 3:
        raise HTTPException(422, "Напишите комментарий: что проверено и что решили")
    a = db.execute(select(SecurityAlert).where(SecurityAlert.id == aid).with_for_update()).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "Предупреждение не найдено")
    if a.status == "resolved":
        raise HTTPException(409, "Предупреждение уже закрыто")
    now = datetime.now(timezone.utc)
    if action == "ack":
        if a.status == "acknowledged":
            raise HTTPException(409, "Предупреждение уже взято в работу")
        a.status, a.acknowledged_by, a.acknowledged_at, a.ack_comment = "acknowledged", user.id, now, comment[:2000]
    else:
        if a.severity == "critical":
            require_step_up(user)   # критическое закрывается только с подтверждением (09 §1)
        if a.acknowledged_at is None:
            a.acknowledged_by, a.acknowledged_at = user.id, now
        a.status, a.resolved_by, a.resolved_at, a.resolution_comment = "resolved", user.id, now, comment[:2000]
    sev.record(db, "alert", "acknowledged" if action == "ack" else "resolved", actor_id=user.id, subject_id=a.subject_user_id, request=request,
               session_id=user.sid, entity_type="security_alert", entity_id=a.id, meta={"rule": a.rule_key, "severity": a.severity, "comment_length": len(comment)})
    audit(db, user.id, None, "security_alert", a.id, action, None, {"status": a.status})
    db.commit()
    return alerts_svc.dto(db, a)


@router.post("/alerts/{aid}/ack")
def alert_ack(aid: str, body: AlertActionIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return _alert_action(db, aid, body, user, request, "ack")


@router.post("/alerts/{aid}/resolve")
def alert_resolve(aid: str, body: AlertActionIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return _alert_action(db, aid, body, user, request, "resolve")


# ------------------------------------------------------------------ проверка файлов (04 §8)

@router.get("/scanner")
def scanner(db: Session = Depends(get_db), user: CurrentUser = Depends(_audit_reader)):
    """Антивирус: режим, доступность, свежесть баз; статусы файлов и последние проверки (без путей хранения)."""
    from ..core.config import get_settings
    from ..services import scanner_status
    return scanner_status.summary(db, get_settings())
