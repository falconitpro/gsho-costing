"""Уровни доступа как данные (задача 11): правила по ролям и исключения для сотрудников, с кэшем.

Итоговые права пользователя считает `core.roles.effective_perms`; здесь — чтение/запись правил в БД.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.roles import DEFAULT_MATRIX, EDIT, FIELD_GROUPS, LEVEL_TITLES, NONE, PROTECTED, ROLE_TITLES, Role, effective_perms
from ..models import AccessRule, AccessUserOverride, User

_CACHE: dict[str, Any] = {"at": 0.0, "rules": None}
CACHE_TTL = 30.0


def invalidate() -> None:
    _CACHE["at"] = 0.0
    _CACHE["rules"] = None


def rules(db: Session) -> dict[str, dict[str, int]]:
    """Правила из БД: роль → {группа: уровень}. Пустая БД = стартовая матрица."""
    if _CACHE["rules"] is not None and time.time() - _CACHE["at"] < CACHE_TTL:
        return _CACHE["rules"]
    out: dict[str, dict[str, int]] = {}
    for r in db.scalars(select(AccessRule)).all():
        out.setdefault(r.role, {})[r.field_group] = int(r.level)
    _CACHE["rules"], _CACHE["at"] = out, time.time()
    return out


def user_overrides(db: Session, user_id: str) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for o in db.scalars(select(AccessUserOverride).where(AccessUserOverride.user_id == user_id)).all():
        if o.valid_to is not None and o.valid_to.replace(tzinfo=o.valid_to.tzinfo or timezone.utc) < now:
            continue
        out[o.field_group] = int(o.level)
    return out


def perms_for(db: Session, user: User) -> dict[str, int]:
    return effective_perms({Role(r) for r in (user.roles or [])}, rules(db), user_overrides(db, user.id))


def matrix(db: Session) -> dict[str, Any]:
    """Полная матрица для экрана «Уровни доступа»: группы, роли, действующие уровни (стартовые + из БД)."""
    db_rules = rules(db)
    roles_out = []
    for role in Role:
        base = {g: NONE for g in FIELD_GROUPS}
        base.update(DEFAULT_MATRIX.get(role, {}))
        current = dict(base)
        current.update(db_rules.get(role.value, {}))
        roles_out.append({"role": role.value, "title": ROLE_TITLES[role], "levels": current, "defaults": base,
                          "protected": sorted(PROTECTED.get(role, set()))})
    return {
        "groups": [{"code": g, "title": t, "fields": sorted(keys)} for g, (t, keys) in FIELD_GROUPS.items()],
        "levels": [{"level": l, "title": t} for l, t in LEVEL_TITLES.items()],
        "roles": roles_out,
        "note": "ГД наследует права КД; группу «Уровни доступа» нельзя закрыть для ГД и администратора.",
    }


def set_rule(db: Session, role: str, group: str, level: int, user_id: Optional[str]) -> AccessRule:
    role_enum = Role(role)
    if group not in FIELD_GROUPS:
        raise ValueError(f"Неизвестная группа {group}")
    if level not in LEVEL_TITLES:
        raise ValueError("Уровень: 0, 1 или 2")
    if group in PROTECTED.get(role_enum, set()) and level < EDIT:
        raise ValueError("Это право нельзя снять с ГД/администратора")
    row = db.scalar(select(AccessRule).where(AccessRule.role == role, AccessRule.field_group == group))
    if row is None:
        row = AccessRule(role=role, field_group=group, level=level, changed_by=user_id)
        db.add(row)
    else:
        row.level, row.changed_by, row.changed_at = level, user_id, datetime.now(timezone.utc)
    invalidate()
    return row


def set_override(db: Session, user: User, group: str, level: int, valid_to: Optional[datetime], comment: str,
                 created_by: Optional[str]) -> AccessUserOverride:
    if group not in FIELD_GROUPS:
        raise ValueError(f"Неизвестная группа {group}")
    if level not in LEVEL_TITLES:
        raise ValueError("Уровень: 0, 1 или 2")
    user_roles = {Role(r) for r in (user.roles or [])}
    if any(group in PROTECTED.get(r, set()) for r in user_roles) and level < EDIT:
        raise ValueError("Это право нельзя снять с ГД/администратора")
    row = db.scalar(select(AccessUserOverride).where(AccessUserOverride.user_id == user.id, AccessUserOverride.field_group == group))
    if row is None:
        row = AccessUserOverride(user_id=user.id, field_group=group, level=level, valid_to=valid_to, comment=comment, created_by=created_by)
        db.add(row)
    else:
        row.level, row.valid_to, row.comment, row.created_by = level, valid_to, comment, created_by
    return row
