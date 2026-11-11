"""Предупреждения безопасности (docs/operations/04 §7.3, 02 «security_alerts»).

Правила версионируются здесь, в коде сервера: пороги не приходят от клиента и не меняются из интерфейса. Первый срез только
предупреждает — работу никто не блокирует. Одна открытая запись на (правило, сотрудник): повторы увеличивают счётчик и
last_seen_at; после «Закрыто» следующий случай создаёт новую запись. Уведомление «в системе» получают держатели права
security_audit — только при новой записи, не на каждый повтор. В предупреждении нет содержимого файлов и выгрузок.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import NotificationLog, SecurityAlert, SecurityEvent, User

log = logging.getLogger("gsho.security")

RULES_VERSION = "2026-11-v1"
WINDOW = timedelta(minutes=10)
EXPORT_BURST = 5          # больше 5 выгрузок за 10 минут
DOWNLOAD_BURST = 20       # больше 20 закрытых файлов за 10 минут (просмотр картинок inline не считается)
DENIED_BURST = 10         # 10 и больше отказов в файлах/выгрузках за 10 минут
LARGE_EXPORT = 1000       # выгрузка от 1000 строк
LARGE_EXPORT_HIGH = 10000

ACCESS_ACTIONS = {"rule_changed", "override_set", "override_deleted", "roles_changed"}
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_TITLES = {"low": "низкая", "medium": "средняя", "high": "высокая", "critical": "критическая"}


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    severity: str
    subject: str = "actor"   # чьё поведение: actor — кто скачивал/менял; subject — над кем действие


RULES = {r.key: r for r in (
    Rule("export_burst", "Частые выгрузки", "high"),
    Rule("large_export", "Крупная выгрузка", "medium"),
    Rule("download_burst", "Частые скачивания закрытых файлов", "high"),
    Rule("denied_burst", "Повторные отказы в доступе к файлам", "medium"),
    Rule("access_change", "Изменение прав доступа", "high", subject="subject"),
    Rule("mfa_reset", "Сброс кода из приложения администратором", "critical", subject="subject"),
    Rule("offboard", "Отключение или включение доступа сотрудника", "high", subject="subject"),
    Rule("password_admin_reset", "Временный пароль выдан администратором", "high", subject="subject"),
    Rule("access_review_overdue", "Пересмотр прав просрочен", "medium", subject="subject"),
)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_count(db: Session, ev: SecurityEvent, *, result: str, extra: Optional[Callable] = None) -> int:
    """Сколько таких событий у сотрудника за окно, включая текущее (оно ещё не записано)."""
    q = select(func.count()).select_from(SecurityEvent).where(
        SecurityEvent.actor_user_id == ev.actor_user_id, SecurityEvent.event_type == ev.event_type,
        SecurityEvent.result == result, SecurityEvent.occurred_at >= _now() - WINDOW)
    if extra is not None:
        q = extra(q)
    with db.no_autoflush:
        return int(db.scalar(q) or 0) + 1


def match(db: Session, ev: SecurityEvent) -> list[tuple[str, str, dict, str]]:
    """Сработавшие правила для события: (rule_key, severity, details, summary)."""
    hits: list[tuple[str, str, dict, str]] = []
    t, a, res = ev.event_type, ev.action, ev.result
    if t in ("export", "download") and res == "denied" and ev.actor_user_id:
        n = _window_count(db, ev, result="denied")
        if n >= DENIED_BURST:
            hits.append(("denied_burst", "medium", {"count_10min": n, "threshold": DENIED_BURST},
                         f"{n} отказов в доступе к файлам и выгрузкам за 10 минут"))
    if t == "export" and res == "success" and ev.actor_user_id:
        n = _window_count(db, ev, result="success")
        if n > EXPORT_BURST:
            hits.append(("export_burst", "high", {"count_10min": n, "threshold": EXPORT_BURST}, f"{n} выгрузок за 10 минут"))
        if (ev.row_count or 0) >= LARGE_EXPORT:
            sev = "high" if ev.row_count >= LARGE_EXPORT_HIGH else "medium"
            hits.append(("large_export", sev, {"rows": ev.row_count, "threshold": LARGE_EXPORT}, f"Выгрузка на {ev.row_count} строк"))
    if t == "download" and res == "success" and ev.actor_user_id and not (ev.meta or {}).get("inline"):
        n = _window_count(db, ev, result="success",
                          extra=lambda q: q.where(SecurityEvent.meta["inline"].as_boolean().is_not(True)))
        if n > DOWNLOAD_BURST:
            hits.append(("download_burst", "high", {"count_10min": n, "threshold": DOWNLOAD_BURST}, f"{n} скачиваний закрытых файлов за 10 минут"))
    if res == "success":
        if t == "access" and a in ACCESS_ACTIONS:
            hits.append(("access_change", "high", {"action": a, **{k: v for k, v in (ev.meta or {}).items() if k in ("group", "role", "level", "before", "after", "roles_added", "roles_removed")}},
                         {"rule_changed": "Изменён стартовый уровень доступа роли", "override_set": "Сотруднику задано исключение прав",
                          "override_deleted": "Удалено исключение прав сотрудника", "roles_changed": "Изменены роли сотрудника"}[a]))
        elif t == "mfa" and a == "admin_reset":
            hits.append(("mfa_reset", "critical", {}, "Администратор сбросил вход с кодом из приложения"))
        elif t == "offboard" and a in ("offboard", "reactivate"):
            hits.append(("offboard", "high", {"action": a}, "Доступ сотрудника отключён" if a == "offboard" else "Доступ сотрудника снова включён"))
        elif t == "access" and a == "review_overdue":
            hits.append(("access_review_overdue", "medium", {"pending": (ev.meta or {}).get("pending")}, f"Пересмотр прав просрочен, не решено сотрудников — {(ev.meta or {}).get('pending')}"))
        elif t == "password" and a == "admin_temporary":
            hits.append(("password_admin_reset", "high", {}, "Администратор выдал временный пароль"))
    return hits


def _audit_holders(db: Session) -> list[str]:
    from .access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if perms_for(db, u).get("security_audit", 0) >= 1]


def _notify(db: Session, alert: SecurityAlert) -> int:
    ids = _audit_holders(db)
    who = db.get(User, alert.subject_user_id) if alert.subject_user_id else None
    rule = RULES[alert.rule_key]
    text = f"Предупреждение безопасности ({SEVERITY_TITLES[alert.severity]}): {rule.title.lower()}" + (f" — {who.display_name}" if who else "") + ". Проверьте в «Журнал и предупреждения»."
    for uid in ids:
        db.add(NotificationLog(user_id=uid, deal_id=None, event="security_alert", channel="app", status="sent", text=text[:500]))
    return len(ids)


def _open(db: Session, rule_key: str, subject: Optional[str], lock: bool = True) -> Optional[SecurityAlert]:
    q = select(SecurityAlert).where(SecurityAlert.rule_key == rule_key, SecurityAlert.status != "resolved",
                                    SecurityAlert.subject_user_id.is_(None) if subject is None else SecurityAlert.subject_user_id == subject)
    if lock:
        q = q.with_for_update()
    with db.no_autoflush:
        return db.scalar(q)


def raise_alert(db: Session, rule_key: str, severity: str, subject: Optional[str], details: dict, summary: str, event_id: str) -> SecurityAlert:
    """Создаёт или продолжает открытое предупреждение (dedup по правилу и сотруднику)."""
    from .security_events import sanitize_meta
    now = _now()
    a = _open(db, rule_key, subject)
    if a is None:
        try:
            with db.begin_nested():
                a = SecurityAlert(rule_key=rule_key, rule_version=RULES_VERSION, severity=severity, status="new", subject_user_id=subject,
                                  first_seen_at=now, last_seen_at=now, occurrence_count=1, summary=summary[:300],
                                  details_sanitized=sanitize_meta(details), last_event_id=event_id)
                db.add(a)
                db.flush([a])
            _notify(db, a)
            return a
        except IntegrityError:   # параллельный запрос успел создать открытую запись — продолжаем её
            a = _open(db, rule_key, subject)
            if a is None:   # pragma: no cover
                raise
    a.occurrence_count = (a.occurrence_count or 0) + 1
    a.last_seen_at = now
    a.last_event_id = event_id
    a.summary = summary[:300]
    a.details_sanitized = sanitize_meta(details)
    if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(a.severity, 0):
        a.severity = severity
    return a


def evaluate(db: Session, ev: SecurityEvent) -> Optional[str]:
    """Вызывается из security_events.record до вставки события: возвращает id предупреждения для ev.alert_id.
    События по самим предупреждениям (ack/resolve) правил не запускают."""
    if ev.event_type == "alert":
        return None
    first: Optional[SecurityAlert] = None
    for key, severity, details, summary in match(db, ev):
        rule = RULES[key]
        subject = ev.subject_user_id if rule.subject == "subject" else ev.actor_user_id
        # изменение стартового уровня роли касается всех её сотрудников — общая запись без subject
        details = {**details, "event_type": ev.event_type, "action": ev.action, "actor_user_id": ev.actor_user_id}
        a = raise_alert(db, key, severity, subject, details, summary, ev.id)
        first = first or a
    return first.id if first else None


def dto(db: Session, a: SecurityAlert, names: Optional[dict] = None) -> dict:
    names = names if names is not None else {}

    def nm(uid):
        if not uid:
            return None
        if uid not in names:
            u = db.get(User, uid)
            names[uid] = u.display_name if u else uid
        return names[uid]
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    rule = RULES.get(a.rule_key)
    return {"id": a.id, "rule_key": a.rule_key, "rule_title": rule.title if rule else a.rule_key, "rule_version": a.rule_version,
            "severity": a.severity, "severity_title": SEVERITY_TITLES.get(a.severity, a.severity), "status": a.status,
            "subject_user_id": a.subject_user_id, "subject_name": nm(a.subject_user_id),
            "first_seen_at": iso(a.first_seen_at), "last_seen_at": iso(a.last_seen_at), "occurrence_count": a.occurrence_count,
            "summary": a.summary, "details": a.details_sanitized or {}, "last_event_id": a.last_event_id,
            "acknowledged_by": a.acknowledged_by, "acknowledged_by_name": nm(a.acknowledged_by), "acknowledged_at": iso(a.acknowledged_at),
            "ack_comment": a.ack_comment, "resolved_by": a.resolved_by, "resolved_by_name": nm(a.resolved_by), "resolved_at": iso(a.resolved_at),
            "resolution_comment": a.resolution_comment}
