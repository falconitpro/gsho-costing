"""Пересмотр прав (docs/operations/04 §6, 13 §10).

Кампания делает снимок прав каждого активного сотрудника; решение по каждому: оставить, изменено (права действительно
изменены после старта — разница фиксируется), отключить (через единую операцию offboarding) или документированный пропуск
по решению владельца. Собственные повышенные права решает другой проверяющий. Завершение — только когда решены все.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import ROLE_TITLES, Role, effective_perms
from ..models import AccessReviewCampaign, AccessReviewItem, AccessUserOverride, IntegrationLog, NotificationLog, User
from . import access_service
from . import mfa as mfa_svc
from . import security_events as sev

SENSITIVE = ("security_admin", "access_admin", "security_audit", "backup_status", "finance_section")   # особое внимание (13 §10)
LONG_OVERRIDE = timedelta(days=90)
REMIND_DAYS = (14, 7, 1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot(db: Session, u: User) -> dict:
    """Что видно проверяющему — без паролей, секретов кода и токенов."""
    rules = access_service.rules(db)
    roles = {Role(r) for r in (u.roles or []) if r in Role._value2member_map_}
    by_role = effective_perms(roles, rules, {})
    eff = access_service.perms_for(db, u)
    t = _now()
    ovs = db.scalars(select(AccessUserOverride).where(AccessUserOverride.user_id == u.id)).all()
    overrides = [{"group": o.field_group, "level": o.level, "valid_to": o.valid_to.isoformat() if o.valid_to else None, "comment": (o.comment or "")[:200],
                  "active": o.valid_to is None or o.valid_to > t, "long": o.valid_to is None or o.valid_to - t > LONG_OVERRIDE} for o in ovs]
    elevated = sorted({g for g, lvl in eff.items() if lvl > by_role.get(g, 0)})
    deputies = [x.display_name for x in db.scalars(select(User).where(User.deputy_id == u.id)).all()]
    last = u.last_seen_at
    return {
        "display_name": u.display_name, "email": u.email, "is_active": u.is_active,
        "roles": [{"code": r, "title": ROLE_TITLES.get(Role(r), r) if r in Role._value2member_map_ else r} for r in (u.roles or [])],
        "perms": {g: lvl for g, lvl in sorted(eff.items()) if lvl},
        "elevated_by_override": elevated,
        "sensitive": {g: eff.get(g, 0) for g in SENSITIVE if eff.get(g, 0)},
        "overrides": overrides,
        "observer_scope": u.observer_scope or {},
        "deputy_for": deputies,
        "deputy": {"id": u.deputy_id, "from": u.deputy_from.isoformat() if u.deputy_from else None, "to": u.deputy_to.isoformat() if u.deputy_to else None} if u.deputy_id else None,
        "mfa_enabled": mfa_svc.enabled(db, u.id),
        "last_seen_at": last.isoformat() if last else None,
        "inactive_days": (t - last).days if last else None,
        "taken_at": t.isoformat(),
    }


def needs_second_reviewer(snap: dict) -> bool:
    """Повышенные права: исключения сверх роли или право администрировать доступ и безопасность."""
    s = snap.get("sensitive", {})
    return bool(snap.get("elevated_by_override")) or s.get("security_admin", 0) >= 2 or s.get("access_admin", 0) >= 2


def diff(old: dict, new: dict) -> dict:
    out: dict = {}
    op, np_ = old.get("perms", {}), new.get("perms", {})
    perms = {g: [op.get(g, 0), np_.get(g, 0)] for g in sorted(set(op) | set(np_)) if op.get(g, 0) != np_.get(g, 0)}
    if perms:
        out["perms"] = perms
    if [r["code"] for r in old.get("roles", [])] != [r["code"] for r in new.get("roles", [])]:
        out["roles"] = [[r["code"] for r in old.get("roles", [])], [r["code"] for r in new.get("roles", [])]]
    key = lambda o: (o["group"], o["level"], o["valid_to"])   # noqa: E731
    if sorted(map(key, [o for o in old.get("overrides", []) if o["active"]])) != sorted(map(key, [o for o in new.get("overrides", []) if o["active"]])):
        out["overrides"] = True
    if old.get("observer_scope") != new.get("observer_scope"):
        out["observer_scope"] = True
    if old.get("is_active") != new.get("is_active"):
        out["is_active"] = [old.get("is_active"), new.get("is_active")]
    return out


def create(db: Session, actor: CurrentUser, title: str, period_start: date, period_end: date, due: date, roles: Optional[list[str]] = None) -> AccessReviewCampaign:
    if due < date.today():
        raise HTTPException(422, "Срок проверки не может быть в прошлом")
    if period_end < period_start:
        raise HTTPException(422, "Конец периода раньше начала")
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()
    if roles:
        users = [u for u in users if set(u.roles or []) & set(roles)]
    if not users:
        raise HTTPException(422, "В выбранный круг не попал ни один активный сотрудник")
    c = AccessReviewCampaign(title=title.strip()[:200] or "Пересмотр прав", period_start=period_start, period_end=period_end, due_date=due,
                             status="open", scope={"roles": roles} if roles else {}, created_by=actor.id)
    db.add(c)
    db.flush()
    for u in users:
        db.add(AccessReviewItem(campaign_id=c.id, user_id=u.id, snapshot=snapshot(db, u)))
    sev.record(db, "access", "review_created", actor_id=actor.id, session_id=actor.sid, entity_type="access_review", entity_id=c.id, meta={"users": len(users)})
    db.commit()
    return c


def progress(db: Session, c: AccessReviewCampaign) -> dict:
    items = db.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == c.id)).all()
    done = [i for i in items if i.decision != "pending"]
    by = {k: sum(1 for i in items if i.decision == k) for k in ("pending", "keep", "change", "disable", "skipped")}
    long_ov = sum(1 for i in items if any(o["active"] and o["long"] for o in (i.snapshot or {}).get("overrides", [])))
    return {"total": len(items), "done": len(done), "percent": round(100 * len(done) / len(items)) if items else 100, "by_decision": by,
            "overdue": c.status == "open" and c.due_date < date.today(), "days_left": (c.due_date - date.today()).days,
            "long_overrides": long_ov, "second_reviewer_needed": sum(1 for i in items if i.decision == "pending" and needs_second_reviewer(i.snapshot or {}))}


def decide(db: Session, actor: CurrentUser, item: AccessReviewItem, decision: str, comment: str) -> AccessReviewItem:
    c = db.get(AccessReviewCampaign, item.campaign_id)
    if c is None or c.status != "open":
        raise HTTPException(409, "Кампания закрыта — решения не принимаются")
    if decision not in ("keep", "change", "disable", "skipped"):
        raise HTTPException(422, "Решение: оставить, изменено, отключить или пропуск")
    snap = item.snapshot or {}
    if item.user_id == actor.id and needs_second_reviewer(snap):
        raise HTTPException(409, "Свои повышенные права подтверждает другой проверяющий")
    u = db.get(User, item.user_id)
    comment = (comment or "").strip()
    changes: dict = {}
    if decision == "change":
        changes = diff(snap, snapshot(db, u))
        if not changes:
            raise HTTPException(409, "Права сотрудника не изменились с начала проверки. Сначала измените их в «Уровнях доступа», затем отметьте «Изменено»")
        if not comment:
            raise HTTPException(422, "Опишите, что изменено и почему")
    if decision == "disable" and u.is_active:
        raise HTTPException(409, "Сначала отключите доступ сотрудника (кнопка «Отключить доступ»), затем отметьте решение")
    if decision == "skipped" and len(comment) < 10:
        raise HTTPException(422, "Пропуск допускается только по решению владельца — укажите, кто и почему решил")
    item.decision, item.reviewer_id, item.reviewed_at, item.comment, item.changes = decision, actor.id, _now(), comment[:2000], changes
    item.completed_at = item.reviewed_at
    sev.record(db, "access", f"review_{decision}", actor_id=actor.id, subject_id=item.user_id, session_id=actor.sid,
               entity_type="access_review", entity_id=c.id, meta={"changes": sorted(changes)})
    db.commit()
    return item


def reopen(db: Session, actor: CurrentUser, item: AccessReviewItem) -> AccessReviewItem:
    c = db.get(AccessReviewCampaign, item.campaign_id)
    if c is None or c.status != "open":
        raise HTTPException(409, "Кампания закрыта")
    item.decision, item.reviewer_id, item.reviewed_at, item.completed_at, item.changes = "pending", None, None, None, {}
    sev.record(db, "access", "review_reopened", actor_id=actor.id, subject_id=item.user_id, session_id=actor.sid, entity_type="access_review", entity_id=c.id)
    db.commit()
    return item


def complete(db: Session, actor: CurrentUser, c: AccessReviewCampaign, note: str) -> AccessReviewCampaign:
    if c.status != "open":
        raise HTTPException(409, "Кампания уже закрыта")
    left = progress(db, c)["by_decision"]["pending"]
    if left:
        raise HTTPException(409, f"Не решено сотрудников: {left}. Завершить можно, когда по каждому есть решение или документированный пропуск")
    c.status, c.completed_at, c.completed_by, c.completion_note = "completed", _now(), actor.id, (note or "")[:2000]
    sev.record(db, "access", "review_completed", actor_id=actor.id, session_id=actor.sid, entity_type="access_review", entity_id=c.id,
               meta={"overdue": c.due_date < date.today()})
    db.commit()
    return c


def remind(db: Session, today: Optional[date] = None) -> list[str]:
    """Напоминания только в системе: за 14, 7 и 1 день до срока и о просрочке; без повторов (IntegrationLog system=access_review)."""
    from .offboarding import security_admin_ids
    today = today or date.today()
    sent = []
    for c in db.scalars(select(AccessReviewCampaign).where(AccessReviewCampaign.status == "open")).all():
        left = (c.due_date - today).days
        key = f"{c.id}:{left}" if left in REMIND_DAYS else f"{c.id}:overdue" if left < 0 else None
        if key is None:
            continue
        if db.scalar(select(IntegrationLog.id).where(IntegrationLog.system == "access_review", IntegrationLog.operation == key)):
            continue
        pending = progress(db, c)["by_decision"]["pending"]
        if not pending:
            continue
        text = (f"Пересмотр прав «{c.title}»: срок истёк, не решено сотрудников — {pending}" if left < 0
                else f"Пересмотр прав «{c.title}»: до срока {left} дн., не решено сотрудников — {pending}")
        for uid in security_admin_ids(db):
            db.add(NotificationLog(user_id=uid, deal_id=None, event="access_review", channel="app", status="sent", text=text))
        db.add(IntegrationLog(system="access_review", operation=key, ok=True, details={"pending": pending}))
        if left < 0:   # просрочка — ещё и предупреждение безопасности в журнале (D-042, 04 §7.3)
            from . import security_events as sev
            sev.record(db, "access", "review_overdue", entity_type="access_review", entity_id=c.id, meta={"pending": pending, "days_overdue": -left})
        sent.append(key)
    db.commit()
    return sent
