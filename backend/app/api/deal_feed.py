"""D-127: обсуждения сделки — лента сделки (сообщения, вопросы и ответы, закреплённое, загруженные письма, события)."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import Attachment, Deal, FeedEntry
from ..services import deal_feed as feed_svc
from ..services.deal_service import audit, ensure_folders, notify
from .common import get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}", tags=["deal-feed"])

MAX_BODY = 20000


class MessageIn(BaseModel):
    section: str = ""
    body: str = Field("", max_length=MAX_BODY)
    tags: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
    answers: bool = False                   # ответ закрывает вопрос, на который отвечает
    awaiting: bool = False
    due_on: Optional[date] = None
    mentions: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class EditIn(BaseModel):
    body: str = Field(..., max_length=MAX_BODY)


def _entry(db: Session, deal: Deal, mid: str) -> FeedEntry:
    e = db.get(FeedEntry, mid)
    if e is None or e.deal_id != deal.id:
        raise HTTPException(404, "Сообщение не найдено")
    return e


def _check_section(user: CurrentUser, section: str, write: bool = True) -> None:
    if section not in feed_svc.SECTIONS:
        raise HTTPException(422, "Неизвестный раздел сделки")
    if section not in feed_svc.visible_sections(user):
        raise HTTPException(403, "Раздел сделки закрыт для вашей роли")
    if write and not feed_svc.can_write(user, section):
        raise HTTPException(403, "Писать в ленту сделки может участник сделки; наблюдатель только читает")


def _store(db: Session, deal: Deal, user: CurrentUser, name: str, data: bytes, comment: str = "") -> Attachment:
    d = tempfile.mkdtemp()
    name = os.path.basename(name or "file").replace("/", "-")[:200] or "file"
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(data)
    try:
        ensure_folders(db, deal)
        stem, ext = os.path.splitext(name)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        stored = adapters.storage().upload(path, deal.storage_folder, "06_mail", f"{stem} [{stamp}-{uuid.uuid4().hex[:6]}]{ext}")
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, f"Хранилище недоступно: {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    att = Attachment(deal_id=deal.id, folder="06_mail", entity="message", entity_id=None, comment=comment, file_name=name,
                     storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
    db.add(att)
    db.flush()
    return att


def _notify_mentions(db: Session, deal: Deal, user: CurrentUser, e: FeedEntry, ids: list[str]) -> None:
    allowed = {u.id for u in feed_svc.participants(db, deal, e.section)}
    for uid in ids:
        if uid in allowed and uid != user.id:
            notify(db, deal, uid, "feed_mention", None, f"{deal.number}: {user.display_name} упомянул(а) вас в обсуждении «{feed_svc.SECTIONS[e.section]}»: {e.body[:140]}")


@router.get("/feed")
def get_feed(deal_id: str, section: Optional[str] = None, tag: Optional[str] = None, q: Optional[str] = None, open: bool = False,
             messages: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if section is not None and section not in feed_svc.SECTIONS:
        raise HTTPException(422, "Неизвестный раздел сделки")
    return feed_svc.load(db, deal, user, section=section, tag=tag, q=q, open_only=open, kinds=list(feed_svc.MESSAGE_KINDS) if messages else None)


@router.get("/feed/participants")
def feed_participants(deal_id: str, section: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _check_section(user, section, write=False)
    return [{"id": u.id, "name": u.display_name} for u in feed_svc.participants(db, deal, section) if u.id != user.id]


@router.post("/feed/files", status_code=201)
async def upload_feed_file(deal_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Файл или скриншот к сообщению (проверка антивирусом — общий вход загрузок до вызова обработчика)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not any(feed_svc.can_write(user, s) for s in feed_svc.SECTIONS):
        raise HTTPException(403, "Наблюдатель только читает ленту сделки")
    data = await file.read()
    att = _store(db, deal, user, file.filename or "file", data)
    db.commit()
    return {"id": att.id, "name": att.file_name, "size": att.size, "url": f"/api/files/{att.storage_path}"}


@router.post("/feed/messages", status_code=201)
def post_message(deal_id: str, body: MessageIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _check_section(user, body.section)
    text = (body.body or "").strip()
    if not text and not body.attachment_ids:
        raise HTTPException(422, "Пустое сообщение")
    tags = [t for t in dict.fromkeys(body.tags) if t in feed_svc.TAGS]
    if body.awaiting and not body.due_on:
        raise HTTPException(422, "Укажите, до какого числа ждём ответа")
    parent = _entry(db, deal, body.parent_id) if body.parent_id else None
    atts = db.scalars(select(Attachment).where(Attachment.id.in_(body.attachment_ids or ["-"]))).all()
    if any(a.deal_id != deal.id or a.entity != "message" or a.uploaded_by != user.id for a in atts) or len(atts) != len(set(body.attachment_ids)):
        raise HTTPException(422, "Файл не найден среди загруженных к этой сделке")
    e = FeedEntry(deal_id=deal.id, kind="comment", direction="out", section=parent.section if parent else body.section, body=text,
                  user_id=user.id, parent_id=parent.id if parent else None, tags=tags, mentions=list(dict.fromkeys(body.mentions)),
                  awaiting=body.awaiting, due_on=body.due_on if body.awaiting else None, attachment_ids=[a.id for a in atts],
                  subject="", at=datetime.now(timezone.utc))
    db.add(e)
    db.flush()
    for a in atts:
        a.entity_id = e.id
    if parent is not None and (body.answers or any(t.startswith("a_") for t in tags)) and parent.awaiting and parent.resolved_at is None:
        parent.resolved_at, parent.resolved_by = datetime.now(timezone.utc), e.id
        if parent.user_id and parent.user_id != user.id:
            notify(db, deal, parent.user_id, "feed_answer", None, f"{deal.number}: получен ответ на ваш вопрос — {text[:140]}")
    _notify_mentions(db, deal, user, e, e.mentions or [])
    audit(db, user.id, deal.id, "deal_feed", e.id, "create", after={"section": e.section, "tags": tags, "awaiting": e.awaiting, "files": len(atts)})
    db.commit()
    return feed_svc.load(db, deal, user, section=None)


@router.patch("/feed/messages/{mid}")
def edit_message(deal_id: str, mid: str, body: EditIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    e = _entry(db, deal, mid)
    if e.user_id != user.id or e.kind != "comment" or e.deleted_at is not None:
        raise HTTPException(403, "Править можно только своё сообщение")
    text = body.body.strip()
    if not text:
        raise HTTPException(422, "Пустое сообщение")
    now = datetime.now(timezone.utc)
    at = e.at if e.at.tzinfo else e.at.replace(tzinfo=timezone.utc)
    if now - at > feed_svc.EDIT_FREE:   # после 15 минут — «исправлено», прежний текст в истории
        e.edit_history = [*(e.edit_history or []), {"body": e.body, "at": (e.edited_at or at).isoformat()}]
        e.edited_at = now
    e.body = text
    audit(db, user.id, deal.id, "deal_feed", e.id, "edit")
    db.commit()
    return feed_svc.load(db, deal, user, section=None)


@router.delete("/feed/messages/{mid}")
def delete_message(deal_id: str, mid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    e = _entry(db, deal, mid)
    at = e.at if e.at.tzinfo else e.at.replace(tzinfo=timezone.utc)
    if e.user_id != user.id or e.kind != "comment":
        raise HTTPException(403, "Удалить можно только своё сообщение")
    if datetime.now(timezone.utc) - at > feed_svc.EDIT_FREE:
        raise HTTPException(409, "Удалить можно в течение 15 минут — дальше исправьте текст, прежний сохранится в истории")
    e.deleted_at = datetime.now(timezone.utc)
    e.pinned = False
    audit(db, user.id, deal.id, "deal_feed", e.id, "delete")
    db.commit()
    return feed_svc.load(db, deal, user, section=None)


@router.post("/feed/messages/{mid}/pin")
def pin_message(deal_id: str, mid: str, pinned: bool = True, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Зафиксировать»: важный ответ (цена, срок, условие) — вверху раздела."""
    deal = get_deal_or_404(db, deal_id, user)
    e = _entry(db, deal, mid)
    _check_section(user, feed_svc.entry_section(e))
    if e.deleted_at is not None:
        raise HTTPException(409, "Сообщение удалено")
    e.pinned, e.pinned_by = pinned, user.id if pinned else None
    audit(db, user.id, deal.id, "deal_feed", e.id, "pin" if pinned else "unpin")
    db.commit()
    return feed_svc.load(db, deal, user, section=None)


@router.post("/feed/messages/{mid}/resolve")
def resolve_question(deal_id: str, mid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Закрыть открытый вопрос без ответа в ленте (ответили по телефону, вопрос снят)."""
    deal = get_deal_or_404(db, deal_id, user)
    e = _entry(db, deal, mid)
    _check_section(user, feed_svc.entry_section(e))
    if not e.awaiting or e.resolved_at is not None:
        raise HTTPException(409, "Вопрос уже закрыт")
    e.resolved_at, e.resolved_by = datetime.now(timezone.utc), user.id
    audit(db, user.id, deal.id, "deal_feed", e.id, "resolve")
    db.commit()
    return feed_svc.load(db, deal, user, section=None)


@router.post("/feed/mail", status_code=201)
async def upload_mail(deal_id: str, file: UploadFile = File(...), section: str = Form(""), direction: str = Form("in"), tags: str = Form(""),
                      db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Обычное (не официальное) письмо клиента или завода — файл .eml в ленту раздела: тема, отправитель, текст, вложения.
    .msg (Outlook) пока сохраняется как файл к сообщению — откройте его и скопируйте текст при необходимости."""
    deal = get_deal_or_404(db, deal_id, user)
    _check_section(user, section)
    name = file.filename or "mail"
    raw = await file.read()
    tag_list = [t for t in tags.split(",") if t in feed_svc.TAGS]
    if name.lower().endswith(".eml"):
        try:
            m = feed_svc.parse_eml(raw)
        except Exception:   # noqa: BLE001
            raise HTTPException(422, "Не удалось прочитать письмо .eml")
        src = _store(db, deal, user, name, raw, "исходное письмо")
        files = [src] + [_store(db, deal, user, n, b) for n, b in m["files"][:30]]
        e = FeedEntry(deal_id=deal.id, kind="mail_in" if direction == "in" else "mail_out", direction="in" if direction == "in" else "out",
                      section=section, subject=m["subject"], counterparty=m["from"] if direction == "in" else m["to"], body=m["body"],
                      attachment_ids=[f.id for f in files], user_id=user.id, tags=tag_list, at=m["date"] or datetime.now(timezone.utc))
    elif name.lower().endswith(".msg"):
        src = _store(db, deal, user, name, raw, "исходное письмо Outlook")
        e = FeedEntry(deal_id=deal.id, kind="mail_in" if direction == "in" else "mail_out", direction="in" if direction == "in" else "out",
                      section=section, subject=os.path.splitext(name)[0][:500], body="Письмо Outlook (.msg) — во вложении.",
                      attachment_ids=[src.id], user_id=user.id, tags=tag_list, at=datetime.now(timezone.utc))
    else:
        raise HTTPException(415, "Загрузите письмо в формате .eml или .msg")
    db.add(e)
    db.flush()
    for aid in e.attachment_ids:
        a = db.get(Attachment, aid)
        if a:
            a.entity_id = e.id
    audit(db, user.id, deal.id, "deal_feed", e.id, "mail_upload", after={"section": section, "file": name})
    db.commit()
    return feed_svc.load(db, deal, user, section=None)
