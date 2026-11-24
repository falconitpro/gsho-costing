"""D-155: история сигналов «Требует вмешательства» (ТЗ v2 §4.3): сигнал устранён — уходит из очереди, история остаётся.

Сигналы не хранятся как объекты — очередь каждый раз собирается из первичных данных (D-153). Здесь только журнал:
ключ появился → запись; ключа больше нет → resolved_at; вернулся → снова открыт, times + 1. Обновляется при открытии
«Пульта» и фоновым заданием раз в цикл (так время устранения известно с точностью до часа, даже если пульт не открывали).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Assignment, SignalLog


def sync(db: Session, escalations: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    cur = {e["key"][:160]: e for e in escalations}
    rows = {r.key: r for r in db.scalars(select(SignalLog).where((SignalLog.resolved_at.is_(None)) | (SignalLog.key.in_(list(cur) or [""])))).all()}
    new = [k for k in cur if k not in rows]
    if new and db.get_bind().dialect.name == "postgresql":
        # «Пульт» могут открыть одновременно (две вкладки, счётчики): новый ключ вставляется без конфликта,
        # второй запрос ждёт первый и берёт уже записанную строку (раньше — 500 из-за signal_log_pkey).
        db.execute(pg_insert(SignalLog).values([{"key": k, "first_seen": now, "last_seen": now, "times": 1} for k in new])
                   .on_conflict_do_nothing(index_elements=["key"]))
        rows.update({r.key: r for r in db.scalars(select(SignalLog).where(SignalLog.key.in_(new))).all()})
    for k, e in cur.items():
        r = rows.get(k)
        if r is None:
            r = SignalLog(key=k, first_seen=now, times=1)
            db.add(r)
        elif r.resolved_at is not None:
            r.resolved_at, r.times = None, (r.times or 1) + 1
        r.priority, r.title, r.url = e.get("priority", "normal"), (e.get("title") or "")[:512], (e.get("url") or "")[:300]
        r.object_type, r.object_number, r.direction = (e.get("object_type") or "")[:64], (e.get("object_number") or "")[:64], (e.get("direction") or "")[:64]
        r.last_seen = now
    for k, r in rows.items():
        if k not in cur and r.resolved_at is None:
            r.resolved_at = now


def history(db: Session, days: int = 30) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(select(SignalLog).where(SignalLog.resolved_at.is_not(None), SignalLog.resolved_at >= since).order_by(SignalLog.resolved_at.desc()).limit(200)).all()
    asg = {a.source_key: a for a in db.scalars(select(Assignment).where(Assignment.source_key.in_([r.key for r in rows] or [""]))).all()}
    out = []
    for r in rows:
        a = asg.get(r.key)
        out.append({"key": r.key, "priority": r.priority, "title": r.title, "object_type": r.object_type, "object_number": r.object_number,
                    "direction": r.direction, "url": r.url, "first_seen": r.first_seen.isoformat(), "resolved_at": r.resolved_at.isoformat(),
                    "hours": round((r.resolved_at - r.first_seen).total_seconds() / 3600, 1), "times": r.times,
                    "assignment": {"id": a.id, "number": a.number, "status": a.status} if a else None})
    return out


def refresh(db: Session) -> int:
    """Фоновое задание: собрать очередь от имени ЗГД (первого активного) и обновить журнал."""
    from ..api.workplace import build
    from ..core.auth import CurrentUser
    from ..core.roles import Role
    from ..models import User
    from .access_service import acting_for, perms_for
    u = next((x for x in db.scalars(select(User).where(User.is_active.is_(True))).all() if "gd" in (x.roles or [])), None)
    if u is None:
        return 0
    cu = CurrentUser(id=u.id, email=u.email, display_name=u.display_name, roles={Role(r) for r in u.roles if r in Role._value2member_map_},
                     observer_scope=u.observer_scope or {}, perms=perms_for(db, u), acting_for=acting_for(db, u))
    out, _ctx = build(db, cu, executive=True)
    return len(out["executive"]["escalations"])
