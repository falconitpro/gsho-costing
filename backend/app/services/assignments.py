"""Управленческие поручения ЗГД (D-154, ТЗ «Рабочее место ЗГД» v2 §4.4, §5).

Почему отдельная сущность: Task привязана к сделке (deal_id обязателен) и не знает отчёта и приёмки; Канцелярия ведёт движение
документов. Поручение — самостоятельный объект с полным циклом и историей; задачи и документы не дублирует (может ссылаться на них
через source_url).

Цикл (полный, решение владельца 23.11.2026):
    new → accepted → in_progress → review → done
                       ↕ blocked      ↓ rework → review
    cancelled — из любого открытого статуса (автор / ЗГД, причина обязательна).
Просрочка не хранится: due_on < сегодня и статус открыт.

Кто что делает (проверяется здесь, а не в интерфейсе):
- создать — ЗГД;
- принять, начать, заблокировать, сдать на проверку — ответственный (assignee), его действующий заместитель, а в поручении
  руководителю — и назначенный им исполнитель (принять — только руководитель);
- передать исполнителю — руководитель в поручении «руководителю»;
- принять результат / вернуть на доработку / перенести срок / отменить — автор или ЗГД;
- руководитель подразделения при прямом поручении видит его и пишет комментарии, но результат не принимает;
- снять блокировку — ответственный или автор.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.roles import Role
from ..models import Assignment, AssignmentEvent, AssignmentFile, OrgDirection, User

OPEN = ("new", "accepted", "in_progress", "blocked", "review", "rework")
STATUS_TITLES = {"new": "новое", "accepted": "принято", "in_progress": "в работе", "blocked": "заблокировано", "review": "на проверке",
                 "rework": "на доработке", "done": "выполнено", "cancelled": "отменено"}
PRIORITY_TITLES = {"critical": "критично", "high": "высокий", "normal": "обычный"}
KIND_TITLES = {"task": "поручение", "explain": "запрос пояснения"}

DIRECTIONS = [("sales", "Коммерция"), ("factory", "Завод и закупки"), ("logistics", "Логистика"), ("finance", "Финансы"),
              ("legal", "Юридический блок"), ("service", "Сервис")]
DIRECTION_TITLES = dict(DIRECTIONS)
# роль сотрудника → направление (для прямого поручения: чей руководитель получает видимость). Порядок — приоритет.
ROLE_DIRECTION = [("cd", "sales"), ("manager", "sales"), ("purchasing", "factory"), ("logistician", "logistics"), ("fd", "finance"),
                  ("lawyer", "legal"), ("service_head", "service")]

# действие → (из каких статусов, в какой, кто может, обязательное поле)
ACTIONS: dict[str, tuple[tuple[str, ...], Optional[str], str, str]] = {
    "accept":     (("new",), "accepted", "assignee", ""),
    "start":      (("accepted", "rework"), "in_progress", "worker", ""),
    "block":      (("accepted", "in_progress", "rework"), "blocked", "worker", "comment"),
    "unblock":    (("blocked",), "in_progress", "worker_or_author", ""),
    "submit":     (("in_progress", "rework"), "review", "worker", "report"),
    "approve":    (("review",), "done", "controller", ""),
    "return":     (("review",), "rework", "controller", "comment"),
    "reschedule": (OPEN, None, "controller", "comment"),
    "delegate":   (("new", "accepted", "in_progress", "rework"), None, "head_assignee", ""),
    "cancel":     (OPEN, "cancelled", "controller", "comment"),
    "comment":    (OPEN + ("done", "cancelled"), None, "participant", "comment"),
}
ACTION_VERBS = {"accept": "принять", "start": "начать", "block": "заблокировать", "unblock": "снять блокировку", "submit": "сдать на проверку",
                "approve": "принять результат", "return": "вернуть на доработку", "reschedule": "перенести срок", "delegate": "передать исполнителю",
                "cancel": "отменить", "comment": "комментировать"}
ACTION_TITLES = {"create": "создано", "accept": "принято", "start": "начато", "block": "заблокировано", "unblock": "блокировка снята",
                 "submit": "сдано на проверку", "approve": "результат принят", "return": "возвращено на доработку", "reschedule": "срок перенесён",
                 "delegate": "передано исполнителю", "cancel": "отменено", "comment": "комментарий"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def heads(db: Session) -> dict[str, Optional[str]]:
    rows = {r.key: r.head_id for r in db.scalars(select(OrgDirection)).all()}
    return {k: rows.get(k) for k, _ in DIRECTIONS}


def direction_of(u: User) -> str:
    roles = set(u.roles or [])
    return next((d for r, d in ROLE_DIRECTION if r in roles), "")


def deputy_info(u: Optional[User], users: dict[str, User], today: date) -> Optional[dict]:
    """Замещение руководителя: временное (с датами, действует сегодня) или постоянный заместитель (без дат)."""
    if not u or not u.deputy_id or u.deputy_id not in users:
        return None
    if u.deputy_from and u.deputy_from > today or u.deputy_to and u.deputy_to < today:
        return None
    temporary = bool(u.deputy_from or u.deputy_to)
    return {"id": u.deputy_id, "name": users[u.deputy_id].display_name, "temporary": temporary,
            "to": u.deputy_to.isoformat() if u.deputy_to else None}


def _uids(user) -> set[str]:
    return {user.id} | set(getattr(user, "acting_for", set()) or set())


def role_of(user, a: Assignment) -> set[str]:
    """Роли пользователя в поручении (с учётом действующего замещения)."""
    me = _uids(user)
    r: set[str] = set()
    if a.author_id in me:
        r.add("author")
    if a.assignee_id in me:
        r.add("assignee")
    if a.executor_id and a.executor_id in me:
        r.add("executor")
    if a.head_id and a.head_id in me:
        r.add("head")
    if user.has(Role.GD):
        r.add("gd")
    return r


def can_see(user, a: Assignment) -> bool:
    return bool(role_of(user, a))


def allowed(user, a: Assignment) -> list[str]:
    r = role_of(user, a)
    out = []
    for act, (src, _dst, who, _need) in ACTIONS.items():
        if a.status not in src:
            continue
        ok = {
            "assignee": "assignee" in r,
            "worker": bool(r & {"assignee", "executor"}),
            "worker_or_author": bool(r & {"assignee", "executor", "author", "gd"}),
            "controller": bool(r & {"author", "gd"}),
            "head_assignee": a.mode == "head" and "assignee" in r,
            "participant": bool(r),
        }[who]
        if ok:
            out.append(act)
    return out


def _notify(db: Session, a: Assignment, user_ids, text: str, exclude: Optional[str]) -> None:
    """Во все каналы сотрудника (в системе; e-mail и мессенджеры — когда подключены), замещающему — тоже (D-154/D-155)."""
    from .deal_service import notify
    for uid in dict.fromkeys(u for u in user_ids if u and u != exclude):
        notify(db, None, uid, "assignment", None, f"Поручение {a.number} «{a.title[:80]}»: {text}"[:500], f"/assignments?open={a.id}")


def _event(db: Session, a: Assignment, by: Optional[str], action: str, frm: str, to: str, comment: str = "", data: Optional[dict] = None) -> None:
    db.add(AssignmentEvent(assignment_id=a.id, by_id=by, action=action, from_status=frm, to_status=to, comment=comment or "", data=data or {}))


def create(db: Session, user, *, title: str, body: str, mode: str, assignee_id: str, due_on: date, priority: str = "normal",
           kind: str = "task", direction_key: str = "", source_url: str = "", source_key: str = "", source_title: str = "") -> Assignment:
    if not user.has(Role.GD):
        raise HTTPException(403, "Поручения создаёт заместитель генерального директора")
    title = (title or "").strip()
    if not title:
        raise HTTPException(422, "Укажите, что нужно сделать")
    if mode not in ("head", "direct") or priority not in PRIORITY_TITLES or kind not in KIND_TITLES:
        raise HTTPException(422, "Неверный режим, приоритет или вид поручения")
    if source_url and not source_url.startswith("/"):
        raise HTTPException(422, "Ссылка на объект — только внутри CRM")
    from .finance.cashview import moscow_today
    if due_on < moscow_today():
        raise HTTPException(422, "Срок не может быть в прошлом")
    target = db.get(User, assignee_id)
    if not target or not target.is_active:
        raise HTTPException(422, "Исполнитель не найден или отключён")
    hd = heads(db)
    head_id: Optional[str] = None
    if mode == "head":
        if direction_key not in DIRECTION_TITLES:
            raise HTTPException(422, "Выберите подразделение")
        if hd.get(direction_key) != assignee_id:
            raise HTTPException(422, "Руководитель подразделения не назначен или выбран другой сотрудник — назначьте руководителя в «Направлениях»")
    else:
        direction_key = direction_key if direction_key in DIRECTION_TITLES else direction_of(target)
        h = hd.get(direction_key) if direction_key else None
        head_id = h if h and h != assignee_id else None
    year = now().year
    seq = (db.scalar(select(func.max(Assignment.seq)).where(Assignment.year == year)) or 0) + 1
    a = Assignment(number=f"П-{year}-{seq:03d}", seq=seq, year=year, kind=kind, title=title[:255], body=(body or "").strip(),
                   priority=priority, mode=mode, direction_key=direction_key, author_id=user.id, assignee_id=assignee_id,
                   head_id=head_id, status="new", due_on=due_on, first_due_on=due_on, source_url=source_url[:300],
                   source_key=source_key[:160], source_title=source_title[:255])
    db.add(a)
    db.flush()
    _event(db, a, user.id, "create", "", "new", data={"mode": mode, "assignee_id": assignee_id, "head_id": head_id, "due_on": due_on.isoformat(),
                                                       "priority": priority, "direction": direction_key})
    what = KIND_TITLES[kind]
    _notify(db, a, [assignee_id], f"новое {what}, срок {due_on:%d.%m.%Y}", user.id)
    if head_id:
        _notify(db, a, [head_id], f"{what} вашему сотруднику {target.display_name} (для сведения)", user.id)
    return a


def act(db: Session, user, a: Assignment, action: str, *, comment: str = "", report: str = "", due_on: Optional[date] = None,
        executor_id: Optional[str] = None) -> Assignment:
    if action not in ACTIONS:
        raise HTTPException(422, "Неизвестное действие")
    if not can_see(user, a):
        raise HTTPException(404, "Поручение не найдено")
    if action not in allowed(user, a):
        raise HTTPException(403, f"Нельзя {ACTION_VERBS[action]}: статус «{STATUS_TITLES[a.status]}» или нет прав")
    _src, dst, _who, need = ACTIONS[action]
    comment, report = (comment or "").strip(), (report or "").strip()
    if need == "comment" and not comment:
        raise HTTPException(422, "Укажите причину" if action != "comment" else "Пустой комментарий")
    if need == "report" and not report:
        raise HTTPException(422, "Опишите результат")
    frm = a.status
    data: dict = {}
    t = now()
    if action == "reschedule":
        if not due_on:
            raise HTTPException(422, "Укажите новый срок")
        from .finance.cashview import moscow_today
        if due_on < moscow_today():
            raise HTTPException(422, "Новый срок не может быть в прошлом")
        if due_on == a.due_on:
            raise HTTPException(422, "Срок не изменился")
        data = {"from": a.due_on.isoformat(), "to": due_on.isoformat()}
        a.due_on = due_on
    elif action == "delegate":
        ex = db.get(User, executor_id) if executor_id else None
        if not ex or not ex.is_active:
            raise HTTPException(422, "Исполнитель не найден или отключён")
        data = {"from": a.executor_id, "to": ex.id}
        a.executor_id = ex.id
    elif action == "block":
        a.block_reason = comment
    elif action == "unblock":
        a.block_reason = ""
    elif action == "submit":
        a.report = report
        a.submitted_at = t
    elif action == "accept":
        a.accepted_at = t
    elif action == "approve":
        a.done_at, a.done_by = t, user.id
    elif action == "cancel":
        a.cancelled_at = t
    if dst:
        a.status = dst
    _event(db, a, user.id, action, frm, a.status, comment or report, data)
    who = user.display_name
    workers = [a.assignee_id, a.executor_id]
    controllers = [a.author_id]
    msg = {
        "accept": (controllers, f"{who} принял(а) поручение"),
        "start": (controllers, f"{who} начал(а) выполнение"),
        "block": (controllers + [a.head_id], f"заблокировано — {comment[:160]}"),
        "unblock": (controllers + workers, "блокировка снята"),
        "submit": (controllers, f"{who} сдал(а) результат на проверку"),
        "approve": (workers + [a.head_id], "результат принят"),
        "return": (workers, f"возвращено на доработку — {comment[:160]}"),
        "reschedule": (workers + [a.head_id], f"срок перенесён на {a.due_on:%d.%m.%Y} — {comment[:120]}"),
        "delegate": ([a.executor_id, a.author_id], f"{who} передал(а) выполнение"),
        "cancel": (workers + [a.head_id], f"отменено — {comment[:160]}"),
        "comment": (controllers + workers + [a.head_id], f"комментарий {who}: {comment[:160]}"),
    }[action]
    _notify(db, a, msg[0], msg[1], user.id)
    return a


def as_dict(a: Assignment, names: dict[str, str], today: date, user=None) -> dict:
    overdue = a.status in OPEN and a.due_on < today
    return {"id": a.id, "number": a.number, "kind": a.kind, "kind_title": KIND_TITLES.get(a.kind, a.kind), "title": a.title, "body": a.body,
            "priority": a.priority, "priority_title": PRIORITY_TITLES.get(a.priority, a.priority), "mode": a.mode,
            "direction_key": a.direction_key, "direction": DIRECTION_TITLES.get(a.direction_key, ""),
            "author": names.get(a.author_id, ""), "author_id": a.author_id,
            "assignee": names.get(a.assignee_id, ""), "assignee_id": a.assignee_id,
            "executor": names.get(a.executor_id or "", ""), "executor_id": a.executor_id,
            "head": names.get(a.head_id or "", ""), "head_id": a.head_id,
            "status": a.status, "status_title": STATUS_TITLES.get(a.status, a.status), "overdue": overdue,
            "overdue_days": (today - a.due_on).days if overdue else 0,
            "due_on": a.due_on.isoformat(), "first_due_on": a.first_due_on.isoformat(), "rescheduled": a.due_on != a.first_due_on,
            "source_url": a.source_url, "source_title": a.source_title, "source_key": a.source_key,
            "report": a.report, "block_reason": a.block_reason,
            "created_at": a.created_at.isoformat() if a.created_at else None, "done_at": a.done_at.isoformat() if a.done_at else None,
            "actions": allowed(user, a) if user is not None else [], "my_role": sorted(role_of(user, a)) if user is not None else []}


def visible_query(user):
    """Поручения, которые пользователь видит: ЗГД — все; остальные — где он автор, ответственный, исполнитель или руководитель."""
    q = select(Assignment)
    if user.has(Role.GD):
        return q
    me = list(_uids(user))
    return q.where((Assignment.author_id.in_(me)) | (Assignment.assignee_id.in_(me)) | (Assignment.executor_id.in_(me)) | (Assignment.head_id.in_(me)))


def summary(db: Session, user, today: date, names: dict[str, str]) -> dict:
    """Счётчики и ближайшие поручения для «Пульта»: в работе, просрочено, срок сегодня, на проверке."""
    rows = db.scalars(visible_query(user).where(Assignment.status.in_(OPEN))).all()
    pr = {"critical": 0, "high": 1, "normal": 2}
    top = sorted(rows, key=lambda a: (0 if a.status == "review" else 1, a.due_on >= today, pr.get(a.priority, 2), a.due_on))[:5]
    return {"open": sum(1 for a in rows if a.status != "review"), "overdue": sum(1 for a in rows if a.due_on < today),
            "today": sum(1 for a in rows if a.due_on == today), "review": sum(1 for a in rows if a.status == "review"),
            "items": [as_dict(a, names, today, user) for a in top], "url": "/assignments", "create_url": "/assignments?new=1",
            "source": "поручения ЗГД"}


# ------------------------------------------------------------------ D-155: подтверждения (файлы)

MAX_FILES, MAX_FILE = 10, 25 * 1024 * 1024
ACTION_TITLES.update({"file": "приложены файлы", "file_removed": "файл убран"})


def can_attach(user, a: Assignment) -> bool:
    return a.status in OPEN and bool(role_of(user, a) & {"assignee", "executor", "author", "gd"})


def save_files(db: Session, user, a: Assignment, files: list) -> list[AssignmentFile]:
    """Файлы уже прошли общий вход загрузок (core/upload_gate): антивирус и zip-бомбы — до вызова обработчика."""
    import os
    import shutil
    import tempfile

    from .. import adapters
    from ..core.files import check_size, safe_filename
    from ..core.upload_gate import current_uploads
    if not can_attach(user, a):
        raise HTTPException(403, "Прикладывать файлы могут ответственный, исполнитель и автор, пока поручение открыто")
    if not files:
        raise HTTPException(422, "Нет файлов")
    have = db.scalar(select(func.count(AssignmentFile.id)).where(AssignmentFile.assignment_id == a.id, AssignmentFile.removed_at.is_(None))) or 0
    if have + len(files) > MAX_FILES:
        raise HTTPException(422, f"Не больше {MAX_FILES} файлов в поручении")
    for f in files:
        check_size(f, MAX_FILE)
    scans = current_uploads() or {}
    out = []
    for f in files:
        d = tempfile.mkdtemp()
        name = safe_filename(f.filename or "") or "файл"
        path = os.path.join(d, name)
        try:
            with open(path, "wb") as fh:
                f.file.seek(0)
                shutil.copyfileobj(f.file, fh)
            stored = adapters.storage().upload(path, f"Поручения/{a.year}/{a.number}", "06_mail", f"{now():%Y%m%d-%H%M%S}-{os.urandom(3).hex()}-{name}")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Хранилище недоступно: {type(e).__name__}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        scan_id = scans.get(stored.sha256)
        row = AssignmentFile(assignment_id=a.id, file_name=name, storage_path=stored.path, sha256=stored.sha256, size=stored.size,
                             scan_status="clean" if scan_id else "legacy_unscanned", scan_id=scan_id, uploaded_by=user.id)
        db.add(row)
        out.append(row)
    db.flush()
    _event(db, a, user.id, "file", a.status, a.status, ", ".join(x.file_name for x in out), {"files": [x.id for x in out]})
    return out


def files_of(db: Session, a: Assignment, names: dict[str, str], user) -> list[dict]:
    rows = db.scalars(select(AssignmentFile).where(AssignmentFile.assignment_id == a.id, AssignmentFile.removed_at.is_(None)).order_by(AssignmentFile.at)).all()
    return [{"id": f.id, "name": f.file_name, "size": f.size, "by": names.get(f.uploaded_by or "", ""), "at": f.at.isoformat() if f.at else None,
             "url": f"/api/management/assignments/{a.id}/files/{f.id}", "can_remove": a.status in OPEN and f.uploaded_by == user.id} for f in rows]


def remove_file(db: Session, user, a: Assignment, f: AssignmentFile) -> None:
    if f.uploaded_by != user.id or a.status not in OPEN:
        raise HTTPException(403, "Убрать файл может только тот, кто его приложил, пока поручение открыто")
    f.removed_at, f.removed_by = now(), user.id
    _event(db, a, user.id, "file_removed", a.status, a.status, f.file_name, {"file": f.id})


# ------------------------------------------------------------------ D-155: напоминания о сроках (фоновое задание, раз в цикл)

def check(db: Session, today: Optional[date] = None) -> int:
    """Накануне срока и в день срока — исполнителю; просрочка — исполнителю, автору и руководителю (один раз на срок),
    через 7 дней просрочки — повторно автору; результат на проверке дольше 2 дней — автору. Повторов нет (alerts)."""
    from datetime import timedelta

    from .finance.cashview import moscow_today
    today = today or moscow_today()
    sent = 0
    for a in db.scalars(select(Assignment).where(Assignment.status.in_(OPEN))).all():
        al = dict(a.alerts or {})
        workers = [a.assignee_id, a.executor_id]

        def once(key: str, who, text: str) -> None:
            nonlocal sent
            if key in al:
                return
            al[key] = today.isoformat()
            _notify(db, a, who, text, None)
            sent += 1
        due = a.due_on
        if a.status != "review":
            if due == today + timedelta(days=1):
                once(f"pre:{due}", workers, f"срок завтра, {due:%d.%m.%Y}")
            elif due == today:
                once(f"today:{due}", workers, "срок сегодня")
            elif due < today:
                once(f"over:{due}", workers + [a.author_id, a.head_id], f"просрочено — срок был {due:%d.%m.%Y}")
                if (today - due).days >= 7:
                    once(f"over7:{due}", [a.author_id], f"просрочено на {(today - due).days} дн.")
        elif a.submitted_at and (today - a.submitted_at.date()).days >= 2:
            once(f"review:{a.submitted_at.date()}", [a.author_id], "результат ждёт вашей проверки больше 2 дней")
        if al != (a.alerts or {}):
            a.alerts = al
    return sent
