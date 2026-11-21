"""Канцелярия (D-128): входящие и исходящие письма, приказы, распоряжения, служебные записки и другие внутренние документы.

Роль «Делопроизводитель» (офис-менеджер) и ЗГД регистрируют и назначают исполнителей (право «Канцелярия» — правка);
КД, ФД, юрист — видят журналы; исполнитель видит свои документы. Приказы по личному составу — отдельное право.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.config import get_settings
from ..core.files import file_response
from ..db import get_db
from ..models import Deal, LegalEntity, OfficeDoc, OfficeSigner, User
from ..services import office as svc
from ..services.deal_service import notify

router = APIRouter(prefix="/api/office", tags=["office"])


def _files_dir(did: str) -> str:
    d = os.path.join(get_settings().storage_local_root, "_office", did)
    os.makedirs(d, exist_ok=True)
    return d


def _need_any(user: CurrentUser) -> None:
    if not (user.can("office") or user.can("office_hr")):
        # исполнитель документа видит его по ссылке (/api/office/docs/{id}), журналы — нет
        raise HTTPException(403, "Канцелярия доступна делопроизводителю, ЗГД, КД, ФД и юристу; исполнитель видит свои документы в «Что сделать»")


def _get(db: Session, did: str, user: CurrentUser) -> OfficeDoc:
    d = db.get(OfficeDoc, did)
    if d is None or not svc.can_see(user, d):
        raise HTTPException(404, "Документ не найден")
    return d


class DocIn(BaseModel):
    entity_id: str
    kind: str
    category: str = ""
    subject: str = Field("", max_length=512)
    summary: str = ""
    body: str = ""
    letter_type: str = ""
    channel: str = ""
    counterparty: str = Field("", max_length=255)
    contact: str = Field("", max_length=255)
    sender_number: str = Field("", max_length=64)
    sender_date: Optional[date] = None
    received_on: Optional[date] = None      # входящее: дата поступления = дата регистрации
    due_on: Optional[date] = None
    executor_id: Optional[str] = None
    co_executor_ids: list[str] = Field(default_factory=list)
    deal_ids: list[str] = Field(default_factory=list)
    deal_section: str = ""
    contract_id: Optional[str] = None
    claim_id: Optional[str] = None
    reply_to_id: Optional[str] = None
    effective_on: Optional[date] = None


class DocPatch(BaseModel):
    subject: Optional[str] = None
    summary: Optional[str] = None
    body: Optional[str] = None
    letter_type: Optional[str] = None
    channel: Optional[str] = None
    counterparty: Optional[str] = None
    contact: Optional[str] = None
    sender_number: Optional[str] = None
    sender_date: Optional[date] = None
    deal_ids: Optional[list[str]] = None
    deal_section: Optional[str] = None
    contract_id: Optional[str] = None
    claim_id: Optional[str] = None
    effective_on: Optional[date] = None


class AssignIn(BaseModel):
    executor_id: str
    co_executor_ids: list[str] = Field(default_factory=list)
    due_on: Optional[date] = None
    note: str = ""


class SignIn(BaseModel):
    signer_id: str
    method: str = "manual"
    signed_on: Optional[date] = None


class ActionIn(BaseModel):
    action: str                       # take | close | cancel | sent | in_force
    note: str = ""
    on: Optional[date] = None
    channel: str = ""


class SignerIn(BaseModel):
    entity_id: str
    full_name: str = Field(..., min_length=2, max_length=255)
    position: str = ""
    basis: str = ""
    poa_id: Optional[str] = None
    user_id: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    methods: list[str] = Field(default_factory=lambda: ["manual"])
    is_active: bool = True


def _check_refs(db: Session, body) -> None:
    for did in getattr(body, "deal_ids", None) or []:
        if db.get(Deal, did) is None:
            raise HTTPException(422, "Сделка не найдена")
    if getattr(body, "deal_section", None) and body.deal_section not in ("", "sale", "logistics", "service", "pricing", "legal", "execution", "finance"):
        raise HTTPException(422, "Неизвестный раздел сделки")
    if getattr(body, "letter_type", None) and body.letter_type not in svc.LETTER_TYPES:
        raise HTTPException(422, "Неизвестный вид письма")
    if getattr(body, "channel", None) and body.channel not in svc.CHANNELS:
        raise HTTPException(422, "Неизвестный способ поступления / отправки")


def _assign_task(db: Session, doc: OfficeDoc, user: CurrentUser) -> None:
    """Исполнитель получает уведомление; документ стоит у него в «Что сделать» до исполнения (рабочее место читает office_docs)."""
    if not doc.executor_id:
        return
    deal_id = (doc.deal_ids or [None])[0]
    due = f", срок {doc.due_on:%d.%m.%Y}" if doc.due_on else ""
    notify(db, db.get(Deal, deal_id) if deal_id else None, doc.executor_id, "office_assigned", None,
           f"Канцелярия: вам назначено {doc.number or svc.KINDS[doc.kind].lower()} «{doc.subject}»{due}")


def _close_tasks(db: Session, doc: OfficeDoc) -> None:
    """Закрытый документ уходит из «Что сделать» сам (список строится по статусу); уведомления о назначении становятся прочитанными."""
    from ..models import NotificationLog
    from sqlalchemy import update
    if doc.executor_id:
        db.execute(update(NotificationLog).where(NotificationLog.user_id == doc.executor_id, NotificationLog.event == "office_assigned",
                                                 NotificationLog.read_at.is_(None), NotificationLog.text.contains(doc.subject[:60]))
                   .values(read_at=datetime.now(timezone.utc)))


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    ents = db.scalars(select(LegalEntity).where(LegalEntity.is_active.is_(True)).order_by(LegalEntity.code)).all()
    signers = db.scalars(select(OfficeSigner).order_by(OfficeSigner.full_name)).all()
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()
    kinds = dict(svc.KINDS)
    return {
        "entities": [{"id": e.id, "code": e.code, "name": e.name, "prefix": svc.prefix_of(e)} for e in ents],
        "kinds": kinds, "order_categories": svc.ORDER_CATEGORIES, "internal_categories": svc.INTERNAL_CATEGORIES,
        "letter_types": svc.LETTER_TYPES, "channels": svc.CHANNELS, "statuses": svc.STATUSES, "sign_methods": svc.SIGN_METHODS,
        "due_days": svc.DUE_DAYS,
        "signers": [{"id": s.id, "entity_id": s.entity_id, "full_name": s.full_name, "position": s.position, "basis": s.basis,
                     "valid_from": s.valid_from.isoformat() if s.valid_from else None, "valid_to": s.valid_to.isoformat() if s.valid_to else None,
                     "methods": s.methods or [], "is_active": s.is_active, "valid_today": svc.signer_ok(s, date.today())} for s in signers],
        "users": [{"id": u.id, "name": u.display_name} for u in users],
        "can_edit": user.can("office", 2), "can_hr": user.can("office_hr"), "can_signers": user.can("office_signers", 2),
    }


@router.get("/docs")
def list_docs(kind: Optional[str] = None, entity_id: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
              overdue: bool = False, mine: bool = False, deal_id: Optional[str] = None, year: Optional[int] = None,
              db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if not mine:
        _need_any(user)
    stmt = select(OfficeDoc)
    if kind:
        stmt = stmt.where(OfficeDoc.kind.in_(kind.split(",")))
    if entity_id:
        stmt = stmt.where(OfficeDoc.entity_id == entity_id)
    if status:
        stmt = stmt.where(OfficeDoc.status.in_(status.split(",")))
    if year:
        stmt = stmt.where(OfficeDoc.year == year)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(OfficeDoc.number.ilike(like), OfficeDoc.subject.ilike(like), OfficeDoc.counterparty.ilike(like),
                              OfficeDoc.summary.ilike(like), OfficeDoc.sender_number.ilike(like)))
    rows = db.scalars(stmt.order_by(OfficeDoc.created_at.desc()).limit(500)).all()
    rows = [d for d in rows if svc.can_see(user, d)]
    if mine:
        rows = [d for d in rows if user.id == d.executor_id or user.id in (d.co_executor_ids or []) or user.id == d.created_by]
    if deal_id:
        rows = [d for d in rows if deal_id in (d.deal_ids or [])]
    if overdue:
        today = date.today()
        rows = [d for d in rows if d.due_on and d.due_on < today and d.status in svc.OPEN]
    uids = {d.executor_id for d in rows if d.executor_id}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    items = [svc.serialize(db, d, user, users) for d in rows]
    today = date.today()
    return {"items": items, "counts": {
        "in_open": sum(1 for d in rows if d.kind == "in" and d.status in svc.OPEN),
        "overdue": sum(1 for d in rows if d.due_on and d.due_on < today and d.status in svc.OPEN),
        "drafts": sum(1 for d in rows if d.status == "draft"),
    }}


@router.post("/docs", status_code=201)
def create_doc(body: DocIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if body.kind not in svc.KINDS:
        raise HTTPException(422, "Неизвестный вид документа")
    if db.get(LegalEntity, body.entity_id) is None:
        raise HTTPException(422, "Юр. лицо не найдено")
    if body.kind == "order" and body.category not in svc.ORDER_CATEGORIES:
        raise HTTPException(422, "Укажите вид приказа: по основной деятельности, по личному составу или по АХВ")
    parent = db.get(OfficeDoc, body.reply_to_id) if body.reply_to_id else None
    if body.reply_to_id and (parent is None or not svc.can_see(user, parent)):
        raise HTTPException(422, "Документ, на который отвечаете, не найден")
    hr = body.kind == "order" and body.category == "hr"
    may = user.can("office_hr", 2) if hr else user.can("office", 2)
    if not may and not (parent is not None and body.kind == "out" and (user.id == parent.executor_id or user.id in (parent.co_executor_ids or []))):
        raise HTTPException(403, "Регистрирует делопроизводитель или ЗГД; исполнитель входящего может подготовить ответ")
    _check_refs(db, body)
    if not body.subject.strip():
        raise HTTPException(422, "Укажите тему (заголовок) документа")
    if body.kind == "in" and body.letter_type == "gov" and not body.due_on:
        raise HTTPException(422, "Для запроса госоргана укажите срок ответа из письма")
    doc = OfficeDoc(entity_id=body.entity_id, kind=body.kind, category=body.category, subject=body.subject.strip(), summary=body.summary,
                    body=body.body, letter_type=body.letter_type, channel=body.channel, counterparty=body.counterparty or (parent.counterparty if parent else ""),
                    contact=body.contact, sender_number=body.sender_number, sender_date=body.sender_date, deal_ids=list(dict.fromkeys(body.deal_ids or (parent.deal_ids if parent else []))),
                    deal_section=body.deal_section or (parent.deal_section if parent else ""), contract_id=body.contract_id or (parent.contract_id if parent else None),
                    claim_id=body.claim_id or (parent.claim_id if parent else None), reply_to_id=body.reply_to_id, effective_on=body.effective_on,
                    status="draft", created_by=user.id, files=[], history=[])
    db.add(doc)
    db.flush()
    svc.hist(doc, user.id, "created")
    if body.kind == "in":   # входящее регистрируется сразу: номер, дата поступления, срок ответа
        svc.allocate_number(db, doc, body.received_on or date.today())
        doc.status = "registered"
        doc.due_on = body.due_on or svc.default_due(body.letter_type, doc.reg_date)
        svc.hist(doc, user.id, "registered", doc.number)
        if body.executor_id:
            if db.get(User, body.executor_id) is None:
                raise HTTPException(422, "Исполнитель не найден")
            doc.executor_id, doc.co_executor_ids, doc.status = body.executor_id, body.co_executor_ids, "assigned"
            svc.hist(doc, user.id, "assigned", body.executor_id)
            _assign_task(db, doc, user)
        svc.post_to_deals(db, doc, user.id)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.get("/docs/{did}")
def get_doc(did: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return svc.serialize(db, _get(db, did, user), user, full=True)


@router.patch("/docs/{did}")
def patch_doc(did: str, body: DocPatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = _get(db, did, user)
    if not (svc.is_editor(user, doc) or (doc.created_by == user.id and doc.status == "draft")):
        raise HTTPException(403, "Править может делопроизводитель, ЗГД или автор черновика")
    if doc.status in ("signed", "sent", "in_force") and any(v is not None for k, v in body.model_dump().items() if k in ("subject", "body")):
        raise HTTPException(409, "Подписанный документ не меняется — подготовьте новый")
    _check_refs(db, body)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(doc, k, v)
    svc.hist(doc, user.id, "edited", ", ".join(sorted(data)))
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/assign")
def assign(did: str, body: AssignIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = _get(db, did, user)
    if not svc.is_editor(user, doc):
        raise HTTPException(403, "Исполнителя назначает делопроизводитель или ЗГД")
    if doc.status in ("closed", "cancelled"):
        raise HTTPException(409, "Документ закрыт")
    if db.get(User, body.executor_id) is None:
        raise HTTPException(422, "Исполнитель не найден")
    doc.executor_id, doc.co_executor_ids = body.executor_id, [u for u in body.co_executor_ids if u != body.executor_id]
    if body.due_on:
        doc.due_on = body.due_on
    if doc.status in ("registered", "assigned", "in_work"):
        doc.status = "assigned"
    svc.hist(doc, user.id, "assigned", body.note)
    _assign_task(db, doc, user)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/action")
def action(did: str, body: ActionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = _get(db, did, user)
    ed = svc.is_editor(user, doc)
    ex = user.id == doc.executor_id or user.id in (doc.co_executor_ids or [])
    on = body.on or date.today()
    if body.action == "take":
        if not ex or doc.status not in ("assigned", "registered"):
            raise HTTPException(409, "Взять в работу может исполнитель назначенного документа")
        doc.status = "in_work"
    elif body.action == "close":
        if not (ed or ex) or doc.status not in svc.OPEN:
            raise HTTPException(409, "Закрыть можно документ в работе")
        if doc.kind == "in" and not body.note.strip():
            raise HTTPException(422, "Укажите, как исполнено (например, «ответ не требуется: …» или «ответ дан по телефону»)")
        doc.status, doc.closed_on, doc.close_note = "closed", on, body.note.strip()
        _close_tasks(db, doc)
    elif body.action == "cancel":
        if not ed:
            raise HTTPException(403, "Отменить может делопроизводитель или ЗГД")
        if not body.note.strip():
            raise HTTPException(422, "Укажите причину отмены")
        doc.status, doc.close_note, doc.closed_on = "cancelled", body.note.strip(), on
        _close_tasks(db, doc)
    elif body.action == "sent":
        if doc.kind != "out" or doc.status != "signed":
            raise HTTPException(409, "Отправить можно подписанное исходящее")
        if not (ed or user.id == doc.created_by):
            raise HTTPException(403)
        doc.status, doc.sent_on = "sent", on
        if body.channel:
            doc.channel = body.channel
        parent = db.get(OfficeDoc, doc.reply_to_id) if doc.reply_to_id else None
        if parent is not None and parent.status in svc.OPEN:   # ответ отправлен — входящее исполнено
            parent.status, parent.closed_on, parent.close_note = "closed", on, f"ответ {doc.number} от {on:%d.%m.%Y}"
            svc.hist(parent, user.id, "closed", parent.close_note)
            _close_tasks(db, parent)
        svc.post_to_deals(db, doc, user.id)
    elif body.action == "in_force":
        if doc.kind not in ("order", "directive", "memo", "internal") or doc.status != "signed":
            raise HTTPException(409, "Ввести в действие можно подписанный приказ, распоряжение или внутренний документ")
        if not ed:
            raise HTTPException(403)
        doc.status, doc.effective_on = "in_force", doc.effective_on or on
    else:
        raise HTTPException(422, "Неизвестное действие")
    svc.hist(doc, user.id, body.action, body.note)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/sign")
def sign(did: str, body: SignIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подпись и номер: исходящее, приказ, распоряжение, служебная записка, внутренний документ.
    Сейчас — отметка «подписано собственноручно» и скан; факсимиле и ЭЦП — следующими этапами."""
    doc = _get(db, did, user)
    if doc.kind == "in":
        raise HTTPException(409, "Входящее не подписывается")
    if doc.status != "draft":
        raise HTTPException(409, "Документ уже подписан")
    s = db.get(OfficeSigner, body.signer_id)
    on = body.signed_on or date.today()
    if s is None or s.entity_id != doc.entity_id:
        raise HTTPException(422, "Подписант не найден у этого юр. лица")
    if not svc.signer_ok(s, on):
        raise HTTPException(422, "Полномочия подписанта не действуют на дату подписания")
    if body.method not in svc.SIGN_METHODS or body.method not in (s.methods or ["manual"]):
        raise HTTPException(422, "Этот способ подписи для подписанта не разрешён")
    if body.method in ("facsimile", "eds"):
        raise HTTPException(409, "Факсимиле и электронная подпись подключаются следующими этапами — пока «собственноручно» со сканом")
    if not (svc.is_editor(user, doc) or user.id == s.user_id):
        raise HTTPException(403, "Отметить подписание может делопроизводитель, ЗГД или сам подписант")
    if not doc.subject.strip():
        raise HTTPException(422, "Укажите тему документа")
    svc.allocate_number(db, doc, on)
    doc.signer_id, doc.signed_on, doc.sign_method, doc.status = s.id, on, body.method, "signed"
    svc.hist(doc, user.id, "signed", f"{doc.number}: {s.full_name}, {svc.SIGN_METHODS[body.method]}")
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/files", status_code=201)
async def upload(did: str, file: UploadFile = File(...), kind: str = Form("attachment"), db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)):
    """Скан, исходное письмо, приложение (антивирус — общий вход загрузок)."""
    doc = _get(db, did, user)
    if not (svc.is_editor(user, doc) or user.id == doc.created_by or user.id == doc.executor_id):
        raise HTTPException(403)
    if kind not in ("original", "scan", "attachment", "draft"):
        raise HTTPException(422, "Неизвестный вид файла")
    fid = uuid.uuid4().hex
    name = os.path.basename(file.filename or "file").replace("/", "-")[:200] or "file"
    path = os.path.join(_files_dir(doc.id), f"{fid}__{name}")
    h = hashlib.sha256()
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
            f.write(chunk)
    doc.files = [*(doc.files or []), {"id": fid, "name": name, "size": size, "sha256": h.hexdigest(), "kind": kind,
                                      "at": datetime.now(timezone.utc).isoformat(), "by": user.id}]
    svc.hist(doc, user.id, "file_added", name)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.get("/docs/{did}/files/{fid}")
def download(did: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = _get(db, did, user)
    f = next((x for x in (doc.files or []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "Файл не найден")
    d = _files_dir(doc.id)
    path = next((os.path.join(d, x) for x in os.listdir(d) if x.startswith(fid)), None)
    if not path:
        raise HTTPException(404, "Файл не найден на сервере")
    return file_response(path, f["name"], inline=True)


# ---------------------------------------------------------------- подписанты и коды юр. лиц (ЗГД)

def _signers_admin(user: CurrentUser) -> None:
    if not user.can("office_signers", 2):
        raise HTTPException(403, "Подписантов ведёт ЗГД")


@router.post("/signers", status_code=201)
def add_signer(body: SignerIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _signers_admin(user)
    if db.get(LegalEntity, body.entity_id) is None:
        raise HTTPException(422, "Юр. лицо не найдено")
    if any(m not in svc.SIGN_METHODS for m in body.methods):
        raise HTTPException(422, "Неизвестный способ подписи")
    s = OfficeSigner(**body.model_dump(), created_by=user.id)
    db.add(s)
    db.commit()
    return meta(db, user)


@router.patch("/signers/{sid}")
def edit_signer(sid: str, body: SignerIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _signers_admin(user)
    s = db.get(OfficeSigner, sid)
    if s is None:
        raise HTTPException(404)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return meta(db, user)


class PrefixIn(BaseModel):
    doc_prefix: str = Field(..., min_length=1, max_length=8)


@router.patch("/entities/{eid}")
def set_prefix(eid: str, body: PrefixIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _signers_admin(user)
    e = db.get(LegalEntity, eid)
    if e is None:
        raise HTTPException(404)
    e.doc_prefix = body.doc_prefix.strip().upper()
    db.commit()
    return meta(db, user)
