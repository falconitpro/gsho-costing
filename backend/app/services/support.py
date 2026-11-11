"""Поддержка внутри CRM (docs/operations/05): обращения, переписка, назначение, SLA, уведомления только «в системе».

Правила доступа: автор видит и ведёт своё обращение; support_admin READ видит очередь и чужие обращения, EDIT — отвечает,
назначает, меняет приоритет и статус. Чужое обращение — 404 (не раскрываем, что оно есть). Почта, WeCom и общий notify()
не используются: уведомления пишутся только в NotificationLog(channel="app").
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..models import NotificationLog, SupportAttachment, SupportMessage, SupportNumberSequence, SupportTicket, User
from . import security_events as sev

CATEGORIES = {"error": "Ошибка", "question": "Вопрос по работе", "access": "Доступ", "improvement": "Предложение"}
URGENCY = {"normal": "обычная", "high": "высокая", "critical": "критичная"}
PRIORITY = {"low": "низкий", "normal": "обычный", "high": "высокий", "critical": "критичный"}
STATUS = {"new": "новое", "in_progress": "в работе", "waiting_reporter": "ждём автора", "resolved": "решено", "closed": "закрыто"}
ACTIVE = ("new", "in_progress", "waiting_reporter")
# SLA первого ответа (05 §8). Рабочего календаря в системе нет — считаем календарные часы и так и пишем (D-052)
SLA_HOURS = {"critical": 4, "high": 24, "normal": 48}
SLA_NOTE = "календарные часы (рабочий календарь не настроен)"
MAX_FILES, MAX_FILE = 5, 50 * 1024 * 1024
_META_KEYS = {"browser", "os", "viewport", "timezone", "local_time", "app_version"}
_SENSITIVE_KEY = re.compile(r"(?i)(token|code|key|secret|pass|sig|state|session|auth)")


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ права

def is_admin(user: CurrentUser, level: int = 1) -> bool:
    return user.can("support_admin", level)


def visible(user: CurrentUser, t: SupportTicket) -> bool:
    return t.reporter_id == user.id or is_admin(user, 1)


def get_or_404(db: Session, user: CurrentUser, ticket_id: str, request: Optional[Request] = None, lock: bool = False) -> SupportTicket:
    q = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if lock:
        q = q.with_for_update()
    t = db.scalar(q)
    if t is None:
        raise HTTPException(404, "Обращение не найдено")
    if not visible(user, t):
        # факт отказа — в журнал безопасности (без темы и текста), ответ такой же, как для несуществующего
        sev.record(db, "support", "view", result="denied", actor_id=user.id, request=request, session_id=user.sid,
                   entity_type="support_ticket", entity_id=t.id)
        db.commit()
        raise HTTPException(404, "Обращение не найдено")
    return t


def admin_ids(db: Session, level: int = 2) -> list[str]:
    from .access_service import perms_for
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if perms_for(db, u).get("support_admin", 0) >= level]


# ------------------------------------------------------------------ номер

def next_number(db: Session, year: int) -> str:
    """SUP-YYYY-NNNN: строка счётчика года под блокировкой; первое обращение года создаёт строку без гонки (ON CONFLICT)."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(pg_insert(SupportNumberSequence).values(year=year, next_value=1).on_conflict_do_nothing(index_elements=["year"]))
    elif db.get(SupportNumberSequence, year) is None:   # pragma: no cover
        db.add(SupportNumberSequence(year=year, next_value=1))
        db.flush()
    row = db.execute(select(SupportNumberSequence).where(SupportNumberSequence.year == year).with_for_update()).scalar_one()
    n = row.next_value
    row.next_value = n + 1
    return f"SUP-{year}-{n:04d}"


# ------------------------------------------------------------------ уведомления (только «в системе»)

def link_for(t: SupportTicket, admin: bool) -> str:
    return f"/support/admin?ticket={t.id}" if admin else f"/support/{t.id}"


def notify(db: Session, user_ids, t: SupportTicket, event: str, text: str, admin_link: bool, exclude: Optional[str] = None) -> int:
    """Только NotificationLog(channel="app"); без текста сообщений и вложений — номер и тема."""
    sent = 0
    for uid in dict.fromkeys(u for u in user_ids if u and u != exclude):
        db.add(NotificationLog(user_id=uid, deal_id=None, event=event, channel="app", status="sent",
                               text=f"{t.number} «{t.subject[:80]}»: {text}"[:500], link=link_for(t, admin_link)))
        sent += 1
    return sent


def _queue(db: Session, t: SupportTicket) -> list[str]:
    """Кому из поддержки сообщать: назначенный, иначе вся очередь (EDIT)."""
    return [t.assignee_id] if t.assignee_id else admin_ids(db, 2)


def _system(db: Session, t: SupportTicket, body: str) -> SupportMessage:
    m = SupportMessage(ticket_id=t.id, author_id=None, kind="system", body=body)
    db.add(m)
    return m


# ------------------------------------------------------------------ вложения

def _safe_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip() or "файл"
    return "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')[:200] or "файл"


def save_files(db: Session, user: CurrentUser, t: SupportTicket, files: list, message: Optional[SupportMessage]) -> list[SupportAttachment]:
    """Файлы уже прошли общий вход загрузок (core/upload_gate): антивирус и zip-бомбы — до вызова обработчика."""
    from .. import adapters
    from ..core.files import check_size
    from ..core.upload_gate import current_uploads
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"Не больше {MAX_FILES} файлов в одном сообщении")
    for f in files:
        check_size(f, MAX_FILE)
    out = []
    scans = current_uploads() or {}
    for f in files:
        d = tempfile.mkdtemp()
        name = _safe_name(f.filename or "")
        path = os.path.join(d, name)
        try:
            with open(path, "wb") as fh:
                f.file.seek(0)
                shutil.copyfileobj(f.file, fh)
            stored = adapters.storage().upload(path, f"Поддержка/{t.created_at.year if t.created_at else now().year}/{t.id}", "06_mail", f"{now():%Y%m%d-%H%M%S}-{os.urandom(3).hex()}-{name}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Хранилище недоступно: {type(e).__name__}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        scan_id = scans.get(stored.sha256)
        a = SupportAttachment(ticket_id=t.id, message_id=message.id if message else None, file_name=name, storage_path=stored.path,
                              sha256=stored.sha256, size=stored.size, scan_status="clean" if scan_id else "legacy_unscanned", scan_id=scan_id,
                              uploaded_by=user.id)
        db.add(a)
        out.append(a)
    return out


# ------------------------------------------------------------------ создание

def _clean_route(route: str) -> str:
    """Адрес страницы без чувствительных параметров (token, code, key…): остальные параметры сохраняются."""
    from urllib.parse import parse_qsl, urlencode, urlsplit
    parts = urlsplit((route or "").strip())
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _SENSITIVE_KEY.search(k)]
    return (parts.path + ("?" + urlencode(keep) if keep else ""))[:300]


def _clean_meta(meta: Optional[dict]) -> dict:
    out = {}
    for k, v in (meta or {}).items():
        if k in _META_KEYS and isinstance(v, (str, int, float)):
            out[k] = str(v)[:120]
    return out


def validate(category: str, subject: str, description: str, urgency: str) -> tuple[str, str]:
    if category not in CATEGORIES:
        raise HTTPException(400, "Выберите категорию")
    if urgency not in URGENCY:
        raise HTTPException(400, "Выберите срочность")
    subject, description = (subject or "").strip(), (description or "").strip()
    if not 5 <= len(subject) <= 200:
        raise HTTPException(400, "Тема — от 5 до 200 символов")
    if not 10 <= len(description) <= 8000:
        raise HTTPException(400, "Описание — от 10 до 8000 символов")
    return subject, description


def create(db: Session, user: CurrentUser, *, category: str, subject: str, description: str, urgency: str, route: str = "",
           app_commit: str = "", correlation_id: str = "", meta: Optional[dict] = None, files: Optional[list] = None,
           idempotency_key: str = "", request: Optional[Request] = None) -> tuple[SupportTicket, bool]:
    """(обращение, создано ли сейчас). Повтор с тем же ключом от того же автора возвращает прежнее обращение."""
    subject, description = validate(category, subject, description, urgency)
    key = re.sub(r"[^\w\-]", "", idempotency_key or "")[:64] or None
    if key:
        old = db.scalar(select(SupportTicket).where(SupportTicket.reporter_id == user.id, SupportTicket.idempotency_key == key))
        if old is not None:
            return old, False
    files = files or []
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"Не больше {MAX_FILES} файлов")
    t0 = now()
    t = SupportTicket(number=f"tmp-{os.urandom(5).hex()}", reporter_id=user.id, category=category, requested_urgency=urgency,
                      priority=urgency, subject=subject, description=description, current_route=_clean_route(route),
                      app_commit=re.sub(r"[^\w.\-]", "", app_commit or "")[:40], correlation_id=re.sub(r"[^\w\-]", "", correlation_id or "")[:40],
                      client_metadata=_clean_meta(meta), status="new", idempotency_key=key, last_activity_at=t0)
    db.add(t)
    db.flush()
    first = SupportMessage(ticket_id=t.id, author_id=user.id, kind="user", body=description)
    db.add(first)
    db.flush()
    save_files(db, user, t, files, first)
    # номер — последним шагом перед фиксацией: блокировка счётчика года держится доли секунды, а не всё сохранение файлов
    t.number = next_number(db, t0.astimezone(_msk()).year)
    notify(db, admin_ids(db, 2), t, "support_ticket_created", f"новое обращение ({CATEGORIES[category].lower()}, срочность {URGENCY[urgency]})",
           admin_link=True, exclude=user.id)
    sev.record(db, "support", "create", actor_id=user.id, subject_id=user.id, request=request, session_id=user.sid, entity_type="support_ticket",
               entity_id=t.id, meta={"category": category, "urgency": urgency, "files": len(files)})
    return t, True


def _msk():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Europe/Moscow")


# ------------------------------------------------------------------ сообщения и переходы

def add_message(db: Session, user: CurrentUser, t: SupportTicket, body: str, files: Optional[list] = None,
                request: Optional[Request] = None) -> SupportMessage:
    body = (body or "").strip()
    files = files or []
    if t.status == "closed":
        raise HTTPException(409, "Обращение закрыто — создайте новое и укажите номер прежнего")
    if not body and not files:
        raise HTTPException(400, "Напишите сообщение или приложите файл")
    if len(body) > 8000:
        raise HTTPException(400, "Сообщение — не длиннее 8000 символов")
    as_reporter = t.reporter_id == user.id
    if not as_reporter and not is_admin(user, 2):
        raise HTTPException(403, "Отвечать в обращении может автор или специалист поддержки")
    kind = "user" if as_reporter else "specialist"
    m = SupportMessage(ticket_id=t.id, author_id=user.id, kind=kind, body=body or "(файл)")
    db.add(m)
    db.flush()
    save_files(db, user, t, files, m)
    t0 = now()
    t.last_activity_at = t0
    if kind == "specialist":
        if t.first_response_at is None:
            t.first_response_at = t0
        if t.status == "new":
            t.status = "in_progress"
        notify(db, [t.reporter_id], t, "support_message_specialist", "ответ поддержки", admin_link=False, exclude=user.id)
    else:
        if t.status in ("waiting_reporter", "resolved"):
            was = t.status
            t.status = "in_progress"
            if was == "resolved":
                t.resolved_at = None
                _system(db, t, "Автор написал после решения — обращение снова в работе")
                notify(db, _queue(db, t), t, "support_reopened", "автор вернул обращение в работу", admin_link=True, exclude=user.id)
                return m
        notify(db, _queue(db, t), t, "support_message_reporter", "новое сообщение автора", admin_link=True, exclude=user.id)
    return m


# действие → (кто: reporter|admin, из каких статусов, в какой, нужен ли текст)
TRANSITIONS = {
    "start": ("admin", {"new", "waiting_reporter"}, "in_progress", False),
    "wait": ("admin", {"new", "in_progress"}, "waiting_reporter", True),
    "resolve": ("admin", {"new", "in_progress", "waiting_reporter"}, "resolved", True),
    "reopen": ("any", {"resolved"}, "in_progress", False),
    "close": ("any", {"new", "in_progress", "waiting_reporter", "resolved"}, "closed", False),
}


def transition(db: Session, user: CurrentUser, t: SupportTicket, action: str, body: str = "", request: Optional[Request] = None) -> bool:
    """True — статус изменён; False — уже в нужном состоянии (повтор без побочных эффектов)."""
    if action not in TRANSITIONS:
        raise HTTPException(400, "Неизвестное действие")
    who, src, dst, need_text = TRANSITIONS[action]
    as_reporter = t.reporter_id == user.id
    if who == "admin" and not is_admin(user, 2):
        raise HTTPException(403, "Действие доступно специалисту поддержки")
    if who == "any" and not (as_reporter or is_admin(user, 2)):
        raise HTTPException(403, "Действие доступно автору или специалисту поддержки")
    if t.status == dst:
        return False
    if t.status not in src:
        raise HTTPException(409, f"Из статуса «{STATUS[t.status]}» так нельзя")
    body = (body or "").strip()
    if need_text and len(body) < 5:
        raise HTTPException(400, "Опишите: что нужно от автора или какое решение" if action == "wait" else "Опишите решение")
    t0 = now()
    t.status, t.last_activity_at = dst, t0
    if body:
        m = SupportMessage(ticket_id=t.id, author_id=user.id, kind="user" if as_reporter and not is_admin(user, 2) else "specialist", body=body[:8000])
        db.add(m)
        if m.kind == "specialist" and t.first_response_at is None:
            t.first_response_at = t0
    who_name = "Автор" if as_reporter else "Поддержка"
    if action == "start":
        _system(db, t, "Обращение взято в работу")
    elif action == "wait":
        _system(db, t, "Ждём ответа автора")
        notify(db, [t.reporter_id], t, "support_waiting_reporter", "поддержке нужны данные от вас", admin_link=False, exclude=user.id)
    elif action == "resolve":
        t.resolved_at = t0
        _system(db, t, "Отмечено как решённое. Если проблема осталась — нажмите «Проблема осталась»")
        notify(db, [t.reporter_id], t, "support_resolved", "отмечено как решённое", admin_link=False, exclude=user.id)
    elif action == "reopen":
        t.resolved_at = None
        _system(db, t, f"{who_name}: проблема осталась — обращение снова в работе")
        notify(db, _queue(db, t), t, "support_reopened", "автор вернул обращение в работу", admin_link=True, exclude=user.id)
    elif action == "close":
        t.closed_at = t0
        _system(db, t, f"{who_name} закрыл обращение")
        if not as_reporter:
            notify(db, [t.reporter_id], t, "support_resolved", "обращение закрыто", admin_link=False, exclude=user.id)
    sev.record(db, "support", f"status_{dst}", actor_id=user.id, subject_id=t.reporter_id, request=request, session_id=user.sid,
               entity_type="support_ticket", entity_id=t.id, meta={"action": action})
    return True


def assign(db: Session, user: CurrentUser, t: SupportTicket, assignee_id: Optional[str], request: Optional[Request] = None) -> bool:
    if not is_admin(user, 2):
        raise HTTPException(403, "Назначать может специалист поддержки")
    if t.status == "closed":
        raise HTTPException(409, "Обращение закрыто")
    if assignee_id and assignee_id not in admin_ids(db, 2):
        raise HTTPException(400, "Назначить можно только активного специалиста поддержки")
    if (t.assignee_id or None) == (assignee_id or None):
        return False
    t.assignee_id = assignee_id or None
    t.last_activity_at = now()
    if assignee_id:
        u = db.get(User, assignee_id)
        _system(db, t, f"Назначено: {u.display_name}")
        notify(db, [assignee_id], t, "support_ticket_assigned", "назначено вам", admin_link=True, exclude=user.id)
    else:
        _system(db, t, "Назначение снято")
    sev.record(db, "support", "assign", actor_id=user.id, subject_id=assignee_id, request=request, session_id=user.sid,
               entity_type="support_ticket", entity_id=t.id)
    return True


def set_priority(db: Session, user: CurrentUser, t: SupportTicket, priority: str, request: Optional[Request] = None) -> bool:
    if not is_admin(user, 2):
        raise HTTPException(403, "Приоритет меняет специалист поддержки")
    if priority not in PRIORITY:
        raise HTTPException(400, "Неизвестный приоритет")
    if t.status == "closed":
        raise HTTPException(409, "Обращение закрыто")
    if t.priority == priority:
        return False
    old = t.priority
    t.priority = priority
    _system(db, t, f"Приоритет: {PRIORITY[old]} → {PRIORITY[priority]}")
    sev.record(db, "support", "priority", actor_id=user.id, request=request, session_id=user.sid, entity_type="support_ticket", entity_id=t.id,
               meta={"before": old, "after": priority})
    return True


def unassign_offboarded(db: Session, user: User) -> int:
    """Отключение специалиста (05 §9): обращения остаются открытыми, назначение снимается, очередь уведомляется."""
    rows = db.scalars(select(SupportTicket).where(SupportTicket.assignee_id == user.id, SupportTicket.status != "closed")).all()
    queue = [i for i in admin_ids(db, 2) if i != user.id]
    for t in rows:
        t.assignee_id = None
        t.last_activity_at = now()
        _system(db, t, f"Назначенный специалист ({user.display_name}) отключён — обращение вернулось в общую очередь")
        notify(db, queue, t, "support_unassigned_offboard", "специалист отключён, нужен новый исполнитель", admin_link=True)
    return len(rows)


# ------------------------------------------------------------------ SLA и представление

def sla(t: SupportTicket) -> dict:
    due = t.created_at + timedelta(hours=SLA_HOURS[t.requested_urgency]) if t.created_at else None
    answered = t.first_response_at is not None
    overdue = bool(due and not answered and t.status in ACTIVE and now() > due)
    return {"hours": SLA_HOURS[t.requested_urgency], "due_at": due.isoformat() if due else None, "answered": answered, "overdue": overdue, "note": SLA_NOTE}


def unread_for(db: Session, user_id: str) -> dict[str, int]:
    """Непрочитанные уведомления поддержки по ссылке обращения (для меток в списке и в шапке)."""
    rows = db.execute(select(NotificationLog.link, func.count()).where(NotificationLog.user_id == user_id, NotificationLog.event.like("support_%"),
                                                                     NotificationLog.read_at.is_(None)).group_by(NotificationLog.link)).all()
    out: dict[str, int] = {}
    for link, n in rows:
        tid = (link or "").split("ticket=")[-1].split("/")[-1]
        out[tid] = out.get(tid, 0) + int(n)
    return out


def mark_read(db: Session, user_id: str, t: SupportTicket) -> int:
    rows = db.scalars(select(NotificationLog).where(NotificationLog.user_id == user_id, NotificationLog.event.like("support_%"),
                                                    NotificationLog.read_at.is_(None),
                                                    NotificationLog.link.in_([link_for(t, True), link_for(t, False)]))).all()
    for r in rows:
        r.read_at, r.status = now(), "read"
    return len(rows)


def _name(db: Session, uid: Optional[str], cache: dict) -> Optional[str]:
    if not uid:
        return None
    if uid not in cache:
        u = db.get(User, uid)
        cache[uid] = (u.display_name + ("" if u.is_active else " (сотрудник отключён)")) if u else "—"
    return cache[uid]


def dto(db: Session, t: SupportTicket, user: CurrentUser, names: Optional[dict] = None, unread: int = 0, full: bool = False) -> dict:
    names = names if names is not None else {}
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    out = {"id": t.id, "number": t.number, "subject": t.subject, "category": t.category, "category_title": CATEGORIES[t.category],
           "requested_urgency": t.requested_urgency, "urgency_title": URGENCY[t.requested_urgency], "priority": t.priority,
           "priority_title": PRIORITY[t.priority], "status": t.status, "status_title": STATUS[t.status],
           "reporter_id": t.reporter_id, "reporter_name": _name(db, t.reporter_id, names), "assignee_id": t.assignee_id,
           "assignee_name": _name(db, t.assignee_id, names), "created_at": iso(t.created_at), "last_activity_at": iso(t.last_activity_at),
           "first_response_at": iso(t.first_response_at), "resolved_at": iso(t.resolved_at), "closed_at": iso(t.closed_at),
           "sla": sla(t), "unread": unread, "mine": t.reporter_id == user.id}
    if full:
        msgs = db.scalars(select(SupportMessage).where(SupportMessage.ticket_id == t.id).order_by(SupportMessage.created_at, SupportMessage.id)).all()
        atts = db.scalars(select(SupportAttachment).where(SupportAttachment.ticket_id == t.id).order_by(SupportAttachment.created_at)).all()
        by_msg: dict = {}
        for a in atts:
            by_msg.setdefault(a.message_id, []).append({"id": a.id, "file_name": a.file_name, "size": a.size, "scan_status": a.scan_status,
                                                       "url": f"/api/support/tickets/{t.id}/attachments/{a.id}" if a.scan_status in ("clean", "legacy_unscanned") else None})
        out.update({
            "description": t.description, "current_route": t.current_route, "app_commit": t.app_commit, "correlation_id": t.correlation_id,
            "client_metadata": t.client_metadata or {},
            "messages": [{"id": m.id, "kind": m.kind, "author_name": _name(db, m.author_id, names) if m.kind != "system" else None, "body": m.body,
                          "at": iso(m.created_at), "attachments": by_msg.get(m.id, [])} for m in msgs],
            "actions": actions(user, t)})
    return out


def actions(user: CurrentUser, t: SupportTicket) -> list[str]:
    out = []
    mine, edit = t.reporter_id == user.id, is_admin(user, 2)
    for a, (who, src, _dst, _need) in TRANSITIONS.items():
        if t.status in src and (edit or (who == "any" and mine)):
            out.append(a)
    if t.status != "closed" and (mine or edit):
        out.append("message")
    if edit and t.status != "closed":
        out += ["assign", "priority"]
    return out


def _immutable_messages() -> None:
    """Сообщения не правятся и не удаляются (05 §5); в production это же держит триггер SQL 017."""
    from sqlalchemy import event

    @event.listens_for(SupportMessage, "before_update")
    def _no_update(mapper, connection, target):   # noqa: ANN001
        raise RuntimeError("support_messages: сообщения обращений не изменяются")

    @event.listens_for(SupportMessage, "before_delete")
    def _no_delete(mapper, connection, target):   # noqa: ANN001
        raise RuntimeError("support_messages: сообщения обращений не удаляются")


_immutable_messages()
