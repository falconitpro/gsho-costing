"""Поддержка внутри CRM (docs/operations/05 §10, 09 «Support»). Файлы проходят общий вход загрузок (антивирус) до обработчика,
скачивание вложений — журнал скачиваний; чужое обращение и его вложения — 404."""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.download_audit import note
from ..core.files import file_response
from ..db import get_db
from ..models import SupportAttachment, SupportTicket, User
from ..services import support as sup

router = APIRouter(prefix="/api/support", tags=["support"])

TABS = {"active": ("new", "in_progress", "waiting_reporter"), "waiting": ("waiting_reporter",), "resolved": ("resolved", "closed")}


def _admin(level: int):
    def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not sup.is_admin(user, level):
            raise HTTPException(403, "Раздел доступен специалистам поддержки")
        return user
    return dep


def _files(files: Optional[list[UploadFile]]) -> list[UploadFile]:
    return [f for f in (files or []) if f is not None and (f.filename or "")]


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Справочники формы и счётчик непрочитанного для кнопки «Поддержка» (только свои обращения либо очередь по праву)."""
    unread = sum(sup.unread_for(db, user.id).values())
    return {"categories": sup.CATEGORIES, "urgency": sup.URGENCY, "priority": sup.PRIORITY, "status": sup.STATUS,
            "sla_hours": sup.SLA_HOURS, "sla_note": sup.SLA_NOTE, "max_files": sup.MAX_FILES, "max_file_mb": sup.MAX_FILE // (1024 * 1024),
            "unread": unread, "is_admin": sup.is_admin(user, 1), "can_edit": sup.is_admin(user, 2)}


@router.post("/tickets", status_code=201)
def create_ticket(request: Request, category: str = Form(...), subject: str = Form(...), description: str = Form(...),
                  urgency: str = Form("normal"), route: str = Form(""), app_commit: str = Form(""), correlation_id: str = Form(""),
                  client_metadata: str = Form("{}"), files: Optional[list[UploadFile]] = File(None),
                  idempotency_key: str = Header("", alias="Idempotency-Key"), db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_user)):
    try:
        meta_in = json.loads(client_metadata or "{}")
        if not isinstance(meta_in, dict):
            meta_in = {}
    except ValueError:
        meta_in = {}
    try:
        t, created = sup.create(db, user, category=category, subject=subject, description=description, urgency=urgency, route=route,
                                app_commit=app_commit, correlation_id=correlation_id, meta=meta_in, files=_files(files),
                                idempotency_key=idempotency_key, request=request)
        db.commit()
    except IntegrityError:
        # параллельный повтор с тем же ключом: обращение уже создано другим запросом
        db.rollback()
        t = db.scalar(select(SupportTicket).where(SupportTicket.reporter_id == user.id, SupportTicket.idempotency_key == idempotency_key[:64]))
        if t is None:
            raise HTTPException(409, "Не удалось создать обращение — повторите")
        created = False
    return {**sup.dto(db, t, user, full=True), "created": created}


@router.get("/tickets")
def my_tickets(tab: str = "active", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Только свои обращения — даже у специалиста поддержки (очередь — отдельно)."""
    q = select(SupportTicket).where(SupportTicket.reporter_id == user.id)
    if tab in TABS:
        q = q.where(SupportTicket.status.in_(TABS[tab]))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(SupportTicket.last_activity_at.desc()).offset(offset).limit(limit)).all()
    unread, names = sup.unread_for(db, user.id), {}
    counts = dict(db.execute(select(SupportTicket.status, func.count()).where(SupportTicket.reporter_id == user.id).group_by(SupportTicket.status)).all())
    return {"total": int(total), "items": [sup.dto(db, t, user, names, unread.get(t.id, 0)) for t in rows],
            "tabs": {k: int(sum(counts.get(s, 0) for s in v)) for k, v in TABS.items()} | {"all": int(sum(counts.values()))}}


@router.get("/tickets/{tid}")
def ticket(tid: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = sup.get_or_404(db, user, tid, request)
    if sup.mark_read(db, user.id, t):
        db.commit()
    out = sup.dto(db, t, user, full=True)
    if sup.is_admin(user, 2):
        out["assignees"] = [{"id": u.id, "name": u.display_name} for u in db.scalars(select(User).where(User.id.in_(sup.admin_ids(db, 2)))).all()]
    return out


@router.post("/tickets/{tid}/messages", status_code=201)
def message(tid: str, request: Request, body: str = Form(""), files: Optional[list[UploadFile]] = File(None),
            db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = sup.get_or_404(db, user, tid, request, lock=True)
    sup.add_message(db, user, t, body, _files(files), request)
    db.commit()
    return sup.dto(db, t, user, full=True)


class TransitionIn(BaseModel):
    action: str
    body: str = ""


@router.post("/tickets/{tid}/transition")
def do_transition(tid: str, payload: TransitionIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = sup.get_or_404(db, user, tid, request, lock=True)
    changed = sup.transition(db, user, t, payload.action, payload.body, request)
    db.commit()
    return {**sup.dto(db, t, user, full=True), "changed": changed}


class AssignIn(BaseModel):
    assignee_id: Optional[str] = None


@router.post("/tickets/{tid}/assign")
def do_assign(tid: str, payload: AssignIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = sup.get_or_404(db, user, tid, request, lock=True)
    changed = sup.assign(db, user, t, payload.assignee_id, request)
    db.commit()
    return {**sup.dto(db, t, user, full=True), "changed": changed}


class PriorityIn(BaseModel):
    priority: str


@router.post("/tickets/{tid}/priority")
def do_priority(tid: str, payload: PriorityIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    t = sup.get_or_404(db, user, tid, request, lock=True)
    changed = sup.set_priority(db, user, t, payload.priority, request)
    db.commit()
    return {**sup.dto(db, t, user, full=True), "changed": changed}


@router.get("/tickets/{tid}/attachments/{aid}")
def attachment(tid: str, aid: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Доступ — через обращение (автор или поддержка), затем статус проверки, затем выдача (журнал скачиваний)."""
    from ..core.config import get_settings
    t = sup.get_or_404(db, user, tid, request)
    a = db.get(SupportAttachment, aid)
    if a is None or a.ticket_id != t.id:
        raise HTTPException(404, "Файл не найден")
    if a.scan_status not in ("clean", "legacy_unscanned"):
        raise HTTPException(410, "Файл заблокирован проверкой безопасности")
    note(entity_type="support_attachment", entity_id=a.id)
    s = get_settings()
    if s.storage_provider == "s3":
        from fastapi.responses import RedirectResponse
        from .. import adapters
        note(presigned=True)
        return RedirectResponse(adapters.storage(s).presigned(a.storage_path, a.file_name), status_code=307,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    full = os.path.join(s.storage_local_root, a.storage_path)
    if not os.path.isfile(full):
        raise HTTPException(404, "Файл не найден")
    return file_response(full, a.file_name)


# ------------------------------------------------------------------ очередь поддержки

@router.get("/admin/tickets")
def queue(status: Optional[str] = None, category: Optional[str] = None, priority: Optional[str] = None, assignee: Optional[str] = None,
          q: str = "", overdue: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
          db: Session = Depends(get_db), user: CurrentUser = Depends(_admin(1))):
    qs = select(SupportTicket)
    if status == "open":
        qs = qs.where(SupportTicket.status != "closed")
    elif status in sup.STATUS:
        qs = qs.where(SupportTicket.status == status)
    if category in sup.CATEGORIES:
        qs = qs.where(SupportTicket.category == category)
    if priority in sup.PRIORITY:
        qs = qs.where(SupportTicket.priority == priority)
    if assignee == "none":
        qs = qs.where(SupportTicket.assignee_id.is_(None))
    elif assignee == "me":
        qs = qs.where(SupportTicket.assignee_id == user.id)
    elif assignee:
        qs = qs.where(SupportTicket.assignee_id == assignee)
    term = (q or "").strip()[:100]
    if term:
        like = f"%{term.replace('%', '').replace('_', '')}%"
        qs = qs.where(or_(SupportTicket.number.ilike(like), SupportTicket.subject.ilike(like)))
    rows = db.scalars(qs.order_by(SupportTicket.last_activity_at.desc()).limit(1000)).all()
    rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    items = [t for t in rows if not overdue or sup.sla(t)["overdue"]]
    # сначала критичные/высокие и просроченные, затем самые давние
    items.sort(key=lambda t: (t.status == "closed", not sup.sla(t)["overdue"], rank.get(t.priority, 9), t.last_activity_at))
    unread, names = sup.unread_for(db, user.id), {}
    page = items[offset:offset + limit]
    return {"total": len(items), "items": [sup.dto(db, t, user, names, unread.get(t.id, 0)) for t in page], "can_edit": sup.is_admin(user, 2)}


@router.get("/admin/metrics")
def metrics(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db), user: CurrentUser = Depends(_admin(1))):
    from datetime import timedelta
    rows = db.scalars(select(SupportTicket).where(SupportTicket.status != "closed")).all()
    since = sup.now() - timedelta(days=days)
    resolved = db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.resolved_at >= since)) or 0
    return {"new": sum(1 for t in rows if t.status == "new"), "no_first_response": sum(1 for t in rows if t.first_response_at is None and t.status in sup.ACTIVE),
            "waiting_reporter": sum(1 for t in rows if t.status == "waiting_reporter"), "overdue": sum(1 for t in rows if sup.sla(t)["overdue"]),
            "resolved_period": int(resolved), "days": days, "sla_note": sup.SLA_NOTE}
