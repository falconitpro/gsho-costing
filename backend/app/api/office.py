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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import ROLE_TITLES, Role
from ..core.config import get_settings
from ..core.files import file_response
from ..db import get_db
from ..models import Deal, LegalEntity, OfficeDoc, OfficeRoute, OfficeSigner, User
from ..services import office as svc
from ..services.deal_service import notify
from .common import get_deal_or_404
from ..services.office_ai import state as office_ai_state

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
    note: str = ""
    urgent: bool = False
    confidential: bool = False
    needs_reply: Optional[bool] = None      # по умолчанию: запрос, претензия, рекламация, госорган — да; уведомление, прочее — нет
    pages: Optional[int] = Field(None, ge=0, le=10000)
    attachments_count: Optional[int] = Field(None, ge=0, le=10000)
    address: str = Field("", max_length=512)
    tracking: str = Field("", max_length=128)      # реквизит источника: e-mail отправителя, трек, № в ЭДО, кто передал


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
    note: Optional[str] = None
    urgent: Optional[bool] = None
    confidential: Optional[bool] = None
    needs_reply: Optional[bool] = None
    pages: Optional[int] = Field(None, ge=0, le=10000)
    attachments_count: Optional[int] = Field(None, ge=0, le=10000)
    address: Optional[str] = Field(None, max_length=512)
    tracking: Optional[str] = Field(None, max_length=128)
    delivered_on: Optional[date] = None
    due_on: Optional[date] = None


class CommentIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


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
    action: str                       # take | close | cancel | sent | delivered | in_force
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
    from ..models import Contract, ContractClaim
    if getattr(body, "claim_id", None):
        cl = db.get(ContractClaim, body.claim_id)
        if cl is None:
            raise HTTPException(422, "Претензия не найдена")
        if not getattr(body, "contract_id", None):
            body.contract_id = cl.contract_id          # D-136: претензия → её договор
    if getattr(body, "contract_id", None):
        c = db.get(Contract, body.contract_id)
        if c is None:
            raise HTTPException(422, "Договор не найден")
        if hasattr(body, "deal_ids") and not (body.deal_ids or []) and (c.deal_id or c.deal_ids):
            body.deal_ids = [x for x in [c.deal_id, *(c.deal_ids or [])] if x][:1]   # договор → его сделка, если сделка не выбрана


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
        "letter_types": svc.LETTER_TYPES, "channels": svc.CHANNELS, "channel_detail": svc.CHANNEL_DETAIL, "statuses": svc.STATUSES, "sign_methods": svc.SIGN_METHODS,
        "due_days": svc.DUE_DAYS,
        "signers": [{"id": s.id, "entity_id": s.entity_id, "full_name": s.full_name, "position": s.position, "basis": s.basis,
                     "valid_from": s.valid_from.isoformat() if s.valid_from else None, "valid_to": s.valid_to.isoformat() if s.valid_to else None,
                     "methods": s.methods or [], "is_active": s.is_active, "valid_today": svc.signer_ok(s, date.today())} for s in signers],
        "users": [{"id": u.id, "name": u.display_name} for u in users],
        "ai": office_ai_state(),
        "routes": [_route_view(r) for r in db.scalars(select(OfficeRoute).order_by(OfficeRoute.name)).all()],
        "roles": {r.value: t for r, t in ROLE_TITLES.items()},
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
                              OfficeDoc.summary.ilike(like), OfficeDoc.sender_number.ilike(like), OfficeDoc.body.ilike(like),   # D-138: текст письма
                              OfficeDoc.tracking.ilike(like), OfficeDoc.note.ilike(like), cast(OfficeDoc.files, String).ilike(like)))   # реквизит, отметка, имена вложений
    rows = db.scalars(stmt.order_by(OfficeDoc.created_at.desc()).limit(500)).all()
    rows = [d for d in rows if svc.can_see(user, d)]
    if mine:
        rows = [d for d in rows if user.id in (d.executor_id, d.created_by, d.reviewer_id) or user.id in (d.co_executor_ids or []) or svc.in_route(d, user)]
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
    if body.kind == "in" and not body.channel:
        raise HTTPException(422, "Укажите источник поступления письма (электронная почта, ЭДО, почта, курьер, нарочно…)")
    if body.kind == "in" and body.letter_type == "gov" and not body.due_on:
        raise HTTPException(422, "Для запроса госоргана укажите срок ответа из письма")
    doc = OfficeDoc(entity_id=body.entity_id, kind=body.kind, category=body.category, subject=body.subject.strip(), summary=body.summary,
                    body=body.body, letter_type=body.letter_type, channel=body.channel, counterparty=body.counterparty or (parent.counterparty if parent else ""),
                    contact=body.contact, sender_number=body.sender_number, sender_date=body.sender_date, deal_ids=list(dict.fromkeys(body.deal_ids or (parent.deal_ids if parent else []))),
                    deal_section=body.deal_section or (parent.deal_section if parent else ""), contract_id=body.contract_id or (parent.contract_id if parent else None),
                    claim_id=body.claim_id or (parent.claim_id if parent else None), reply_to_id=body.reply_to_id, effective_on=body.effective_on,
                    note=body.note, urgent=body.urgent, confidential=body.confidential, pages=body.pages, attachments_count=body.attachments_count,
                    address=body.address or (parent.address if parent else ""), tracking=body.tracking.strip(),
                    needs_reply=body.needs_reply if body.needs_reply is not None else (body.letter_type in ("request", "claim", "complaint", "gov", "")),
                    status="draft", created_by=user.id, files=[], history=[], comments=[])
    db.add(doc)
    db.flush()
    svc.hist(doc, user.id, "created")
    if body.kind == "in":   # входящее регистрируется сразу: номер, дата поступления, срок ответа
        svc.allocate_number(db, doc, body.received_on or date.today())
        doc.status = "registered"
        doc.due_on = (body.due_on or svc.default_due(body.letter_type, doc.reg_date)) if doc.needs_reply else None
        svc.hist(doc, user.id, "registered", doc.number)
        if body.executor_id:   # исполнитель получит письмо, когда будет приложен скан (D-129)
            if db.get(User, body.executor_id) is None:
                raise HTTPException(422, "Исполнитель не найден")
            doc.executor_id, doc.co_executor_ids = body.executor_id, body.co_executor_ids
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
    if doc.status in ("approval", "approved") and any(v is not None for k, v in body.model_dump().items() if k in ("subject", "body", "summary")):
        raise HTTPException(409, "Документ на согласовании (или согласован) — чтобы изменить текст, отзовите его с согласования")
    _check_refs(db, body)
    data = body.model_dump(exclude_unset=True)
    if doc.kind == "in" and "channel" in data and not data["channel"]:
        raise HTTPException(422, "Источник поступления — обязательный реквизит входящего")
    if "tracking" in data:
        data["tracking"] = (data["tracking"] or "").strip()
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
    if doc.kind == "in" and not svc.has_scan(doc):
        raise HTTPException(409, "Сначала приложите скан или исходный файл письма")
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
        if doc.kind == "in" and doc.needs_reply and not body.note.strip():
            raise HTTPException(422, "Укажите, как исполнено (например, «ответ не требуется: …» или «ответ дан по телефону»)")
        if doc.kind == "in" and not svc.has_scan(doc):
            raise HTTPException(409, "Сначала приложите скан или исходный файл письма")
        doc.status, doc.closed_on, doc.close_note = "closed", on, body.note.strip() or ("к сведению" if doc.kind == "in" else "исполнено")
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
        if not any(f.get("kind") == "scan" for f in doc.files or []):
            raise HTTPException(409, "Приложите скан подписанного письма — без него отметить отправку нельзя")
        if body.channel:
            if body.channel not in svc.CHANNELS:
                raise HTTPException(422, "Неизвестный способ отправки")
            doc.channel = body.channel
        if not doc.channel:
            raise HTTPException(422, "Укажите способ отправки (электронная почта, ЭДО, почта, курьер…)")
        doc.status, doc.sent_on = "sent", on
        parent = db.get(OfficeDoc, doc.reply_to_id) if doc.reply_to_id else None
        if parent is not None and parent.status in svc.OPEN:   # ответ отправлен — входящее исполнено
            parent.status, parent.closed_on, parent.close_note = "closed", on, f"ответ {doc.number} от {on:%d.%m.%Y}"
            svc.hist(parent, user.id, "closed", parent.close_note)
            _close_tasks(db, parent)
        svc.post_to_deals(db, doc, user.id)
    elif body.action == "delivered":
        if doc.kind != "out" or doc.status != "sent":
            raise HTTPException(409, "Отметить вручение можно отправленное исходящее")
        doc.delivered_on = on
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
    if doc.status not in ("draft", "approved"):
        raise HTTPException(409, "Документ на согласовании" if doc.status == "approval" else "Документ уже подписан")
    if doc.status == "draft" and svc.approval_required(db, doc):
        raise HTTPException(409, "Для этого документа согласование обязательно — отправьте его по типовой схеме или по своему маршруту")
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
    if doc.kind == "in" and doc.status == "registered" and doc.executor_id and svc.has_scan(doc):   # скан приложен — письмо уходит исполнителю
        doc.status = "assigned"
        svc.hist(doc, user.id, "assigned", doc.executor_id)
        _assign_task(db, doc, user)
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



# ---------------------------------------------------------------- согласование перед подписанием (D-129)

class StepIn(BaseModel):
    user_id: Optional[str] = None
    role: Optional[str] = None
    title: str = ""


class RouteIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    kinds: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    entity_id: Optional[str] = None
    mode: str = "seq"
    steps: list[StepIn] = Field(..., min_length=1, max_length=20)
    required: bool = False
    is_active: bool = True


class StartIn(BaseModel):
    route_id: Optional[str] = None        # типовая схема; без неё — ручной маршрут из steps
    mode: str = "seq"
    steps: list[StepIn] = Field(default_factory=list, max_length=20)
    note: str = ""


class DecideIn(BaseModel):
    decision: str                         # approve | reject
    comment: str = ""


def _route_view(r: OfficeRoute) -> dict:
    return {"id": r.id, "name": r.name, "kinds": r.kinds or [], "categories": r.categories or [], "entity_id": r.entity_id, "mode": r.mode,
            "steps": r.steps or [], "required": r.required, "is_active": r.is_active}


def _check_steps(db: Session, steps: list[StepIn], mode: str) -> list[dict]:
    if mode not in ("seq", "par"):
        raise HTTPException(422, "Порядок: по очереди или одновременно")
    if not steps:
        raise HTTPException(422, "Добавьте хотя бы одного согласующего")
    roles = {r.value for r in Role}
    out = []
    for st in steps:
        if bool(st.user_id) == bool(st.role):
            raise HTTPException(422, "В шаге укажите сотрудника или роль")
        if st.user_id and (db.get(User, st.user_id) is None or not db.get(User, st.user_id).is_active):
            raise HTTPException(422, "Согласующий не найден или отключён")
        if st.role and st.role not in roles:
            raise HTTPException(422, "Неизвестная роль")
        out.append(st.model_dump())
    return out


@router.post("/routes", status_code=201)
def add_route(body: RouteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _signers_admin(user)
    if any(k not in svc.APPROVABLE for k in body.kinds):
        raise HTTPException(422, "Согласуются исходящие, приказы, распоряжения, служебные и внутренние документы")
    steps = _check_steps(db, body.steps, body.mode)
    db.add(OfficeRoute(name=body.name.strip(), kinds=body.kinds, categories=body.categories, entity_id=body.entity_id, mode=body.mode,
                       steps=steps, required=body.required, is_active=body.is_active, created_by=user.id))
    db.commit()
    return meta(db, user)


@router.patch("/routes/{rid}")
def edit_route(rid: str, body: RouteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _signers_admin(user)
    r = db.get(OfficeRoute, rid)
    if r is None:
        raise HTTPException(404)
    steps = _check_steps(db, body.steps, body.mode)
    r.name, r.kinds, r.categories, r.entity_id, r.mode, r.steps, r.required, r.is_active = (
        body.name.strip(), body.kinds, body.categories, body.entity_id, body.mode, steps, body.required, body.is_active)
    db.commit()
    return meta(db, user)


def _notify_step(db: Session, doc: OfficeDoc, st: dict) -> None:
    deal_id = (doc.deal_ids or [None])[0]
    for uid in svc.step_people(db, st):
        notify(db, db.get(Deal, deal_id) if deal_id else None, uid, "office_approval", None,
               f"Канцелярия: на согласование — {svc.KINDS[doc.kind].lower()} «{doc.subject}»")


@router.post("/docs/{did}/approval")
def start_approval(did: str, body: StartIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отправить черновик на согласование: по типовой схеме или по своему маршруту (сотрудники и роли, по очереди или одновременно)."""
    doc = _get(db, did, user)
    if doc.kind not in svc.APPROVABLE:
        raise HTTPException(409, "Входящее не согласуется")
    if doc.status != "draft":
        raise HTTPException(409, "Отправить на согласование можно черновик")
    if not (svc.is_editor(user, doc) or user.id == doc.created_by):
        raise HTTPException(403, "Отправить на согласование может автор, делопроизводитель или ЗГД")
    if not doc.subject.strip():
        raise HTTPException(422, "Укажите тему документа")
    if body.route_id:
        r = db.get(OfficeRoute, body.route_id)
        if r is None or not r.is_active or r not in svc.routes_for(db, doc):
            raise HTTPException(422, "Типовая схема не подходит этому документу")
        steps, mode, name = [StepIn(**x) for x in r.steps], r.mode, r.name
        extra = body.steps            # к типовой схеме можно добавить согласующих
    else:
        steps, mode, name, extra = body.steps, body.mode, "Свой маршрут", []
    steps = _check_steps(db, [*steps, *extra], mode)
    prev = doc.approval or {}
    doc.approval = {"route_id": body.route_id, "name": name, "mode": mode, "round": int(prev.get("round", 0)) + 1,
                    "started_at": datetime.now(timezone.utc).isoformat(), "started_by": user.id, "steps": svc.start_steps(steps, mode),
                    "rounds": [*(prev.get("rounds") or []), *([{k: prev[k] for k in ("name", "round", "steps") if k in prev}] if prev.get("steps") else [])]}
    doc.status = "approval"
    svc.hist(doc, user.id, "approval_started", f"{name}: {len(steps)} шаг(ов), {'по очереди' if mode == 'seq' else 'одновременно'}{'; ' + body.note if body.note else ''}")
    for st in svc.active_steps(doc):
        _notify_step(db, doc, st)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/approval/decide")
def decide(did: str, body: DecideIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = db.get(OfficeDoc, did)
    if doc is None:
        raise HTTPException(404, "Документ не найден")
    st = svc.is_approver(db, doc, user)
    if st is None:
        raise HTTPException(403, "Сейчас не ваша очередь согласовывать этот документ")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "Решение: согласовать или отклонить")
    if body.decision == "reject" and not body.comment.strip():
        raise HTTPException(422, "Напишите, что исправить")
    a = dict(doc.approval)
    steps = [dict(x) for x in a["steps"]]
    me = next(x for x in steps if x["id"] == st["id"])
    me.update({"status": "approved" if body.decision == "approve" else "rejected", "by": user.id, "comment": body.comment.strip(),
               "at": datetime.now(timezone.utc).isoformat()})
    a["steps"] = steps
    doc.approval = a
    author = doc.approval.get("started_by") or doc.created_by
    deal = db.get(Deal, (doc.deal_ids or [None])[0]) if doc.deal_ids else None
    if body.decision == "reject":          # отказ — документ возвращается автору в черновик
        doc.status = "draft"
        svc.hist(doc, user.id, "approval_rejected", body.comment.strip())
        if author and author != user.id:
            notify(db, deal, author, "office_approval_result", None, f"Канцелярия: «{doc.subject}» отклонён — {user.display_name}: {body.comment.strip()[:160]}")
    else:
        svc.hist(doc, user.id, "approval_approved", body.comment.strip())
        if svc.advance(doc):
            doc.status = "approved"
            svc.hist(doc, None, "approved", "все согласовали — можно подписывать")
            if author and author != user.id:
                notify(db, deal, author, "office_approval_result", None, f"Канцелярия: «{doc.subject}» согласован — можно подписывать")
        else:
            for nxt in svc.active_steps(doc):
                if nxt["id"] != me["id"] and a.get("mode", "seq") == "seq":
                    _notify_step(db, doc, nxt)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/approval/recall")
def recall(did: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отозвать с согласования (изменить текст) — документ снова черновик, решения сохраняются в истории маршрута."""
    doc = _get(db, did, user)
    if doc.status not in ("approval", "approved"):
        raise HTTPException(409, "Документ не на согласовании")
    if not (svc.is_editor(user, doc) or user.id == (doc.approval or {}).get("started_by") or user.id == doc.created_by):
        raise HTTPException(403)
    doc.status = "draft"
    svc.hist(doc, user.id, "approval_recalled")
    db.commit()
    return svc.serialize(db, doc, user, full=True)



@router.post("/docs/{did}/comments", status_code=201)
def add_comment(did: str, body: CommentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Комментарий к документу: исполнитель, согласующие, делопроизводитель — пояснения, вопросы, решения."""
    doc = _get(db, did, user)
    doc.comments = [*(doc.comments or []), {"id": uuid.uuid4().hex[:12], "by": user.id, "text": body.text.strip(), "at": datetime.now(timezone.utc).isoformat()}]
    db.commit()
    return svc.serialize(db, doc, user, full=True)



# ---------------------------------------------------------------- рассмотрение и резолюция (D-130)

class ReviewIn(BaseModel):
    reviewer_id: str
    note: str = ""


class ResolutionIn(BaseModel):
    kind: str = "execute"                 # execute — к исполнению | info — к сведению | file — в дело
    text: str = Field("", max_length=2000)
    executor_id: Optional[str] = None
    co_executor_ids: list[str] = Field(default_factory=list)
    due_on: Optional[date] = None


class ReturnIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=2000)   # «не мой вопрос — прошу передать …», «прошу продлить срок до …»
    new_due: Optional[date] = None


@router.post("/docs/{did}/review")
def send_to_review(did: str, body: ReviewIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Делопроизводитель передаёт входящее на рассмотрение руководителю (ЗГД, КД, ФД…); тот накладывает резолюцию."""
    doc = _get(db, did, user)
    if doc.kind != "in":
        raise HTTPException(409, "На рассмотрение передаётся входящее")
    if not svc.is_editor(user, doc):
        raise HTTPException(403, "На рассмотрение передаёт делопроизводитель или ЗГД")
    if doc.status not in ("registered", "assigned", "review"):
        raise HTTPException(409, "Письмо уже в работе или закрыто")
    if not svc.has_scan(doc):
        raise HTTPException(409, "Сначала приложите скан или исходный файл письма")
    rv = db.get(User, body.reviewer_id)
    if rv is None or not rv.is_active:
        raise HTTPException(422, "Руководитель не найден")
    doc.reviewer_id, doc.status = rv.id, "review"
    svc.hist(doc, user.id, "to_review", f"{rv.display_name}{'; ' + body.note if body.note else ''}")
    deal = db.get(Deal, doc.deal_ids[0]) if doc.deal_ids else None
    notify(db, deal, rv.id, "office_review", None, f"Канцелярия: на рассмотрение — {doc.number} «{doc.subject}»" + (f", срок ответа {doc.due_on:%d.%m.%Y}" if doc.due_on else ""))
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/resolution")
def resolution(did: str, body: ResolutionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Резолюция: исполнитель (ответственный), соисполнители, срок и указание; или «к сведению» / «в дело» — письмо закрывается."""
    doc = _get(db, did, user)
    if doc.kind != "in":
        raise HTTPException(409, "Резолюция накладывается на входящее")
    if not (user.id == doc.reviewer_id or user.can("office_signers", 2)):
        raise HTTPException(403, "Резолюцию накладывает руководитель, которому письмо передано на рассмотрение, или ЗГД")
    if doc.status not in ("review", "registered", "assigned", "in_work"):
        raise HTTPException(409, "Письмо закрыто")
    if body.kind not in svc.RESOLUTION_KINDS:
        raise HTTPException(422, "Резолюция: к исполнению, к сведению или в дело")
    if not svc.has_scan(doc):
        raise HTTPException(409, "Сначала приложите скан или исходный файл письма")
    today = date.today()
    rec = {"id": uuid.uuid4().hex[:12], "by": user.id, "at": datetime.now(timezone.utc).isoformat(), "kind": body.kind, "text": body.text.strip(),
           "executor_id": None, "co": [], "due_on": None}
    if body.kind == "execute":
        ex = db.get(User, body.executor_id) if body.executor_id else None
        if ex is None or not ex.is_active:
            raise HTTPException(422, "Укажите исполнителя (ответственного)")
        due = body.due_on or doc.due_on or svc.default_due(doc.letter_type, today) or today
        if due < today:
            raise HTTPException(422, "Срок исполнения — не раньше сегодняшнего дня")
        rec.update({"executor_id": ex.id, "co": [u for u in body.co_executor_ids if u != ex.id], "due_on": due.isoformat()})
        doc.executor_id, doc.co_executor_ids, doc.due_on, doc.status = ex.id, rec["co"], due, "assigned"
        doc.resolutions = [*(doc.resolutions or []), rec]
        _assign_task(db, doc, user)
        for cid in rec["co"]:
            notify(db, db.get(Deal, doc.deal_ids[0]) if doc.deal_ids else None, cid, "office_assigned", None,
                   f"Канцелярия: вы соисполнитель — {doc.number} «{doc.subject}», срок {due:%d.%m.%Y}")
    else:
        doc.resolutions = [*(doc.resolutions or []), rec]
        doc.status, doc.closed_on = "closed", today
        doc.close_note = f"{svc.RESOLUTION_KINDS[body.kind]}" + (f": {body.text.strip()}" if body.text.strip() else "")
        _close_tasks(db, doc)
    svc.hist(doc, user.id, "resolution", f"{svc.RESOLUTION_KINDS[body.kind]}{': ' + body.text.strip() if body.text.strip() else ''}")
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/return")
def return_to_review(did: str, body: ReturnIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Исполнитель возвращает письмо руководителю: «не мой вопрос» или «прошу продлить срок» — руководитель накладывает новую резолюцию."""
    doc = _get(db, did, user)
    if user.id != doc.executor_id:
        raise HTTPException(403, "Вернуть может ответственный исполнитель")
    if doc.status not in ("assigned", "in_work"):
        raise HTTPException(409, "Письмо не в работе")
    back_to = doc.reviewer_id
    if not back_to:   # письмо назначено без рассмотрения — возвращается делопроизводителю
        doc.status = "registered"
    else:
        doc.status = "review"
    note = body.reason.strip() + (f" (прошу срок до {body.new_due:%d.%m.%Y})" if body.new_due else "")
    doc.comments = [*(doc.comments or []), {"id": uuid.uuid4().hex[:12], "by": user.id, "text": f"Возврат: {note}", "at": datetime.now(timezone.utc).isoformat()}]
    svc.hist(doc, user.id, "returned", note)
    _close_tasks(db, doc)
    deal = db.get(Deal, doc.deal_ids[0]) if doc.deal_ids else None
    targets = [back_to] if back_to else [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if "clerk" in (u.roles or [])]
    for t in targets:
        notify(db, deal, t, "office_review", None, f"Канцелярия: {user.display_name} вернул {doc.number} «{doc.subject}» — {note[:160]}")
    db.commit()
    return svc.serialize(db, doc, user, full=True)



# ---------------------------------------------------------------- контроль сроков и выгрузка журнала (D-133)

@router.get("/control")
def control(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Панель «на контроле»: по исполнителям — в работе, срок сегодня, просрочено; за 30 дней — закрыто и сколько в срок."""
    _need_any(user)
    from ..services.office_watch import control as build
    data = build(db)
    for r in data["items"]:   # конфиденциальные и приказы по ЛС — без темы, если их не видно этому сотруднику
        r["late_docs"] = [x if svc.can_see(user, db.get(OfficeDoc, x["id"])) else {**x, "subject": "(конфиденциально)"} for x in r["late_docs"]]
    return data


EXPORT_COLS = [("number", "№"), ("reg_date", "Дата регистрации"), ("entity", "Юр. лицо"), ("kind_title", "Вид"), ("letter_type", "Вид письма"),
               ("channel", "Источник / способ"), ("tracking", "Реквизит источника"), ("counterparty", "Контрагент"), ("sender_number", "Исх. № отправителя"),
               ("sender_date", "от"), ("subject", "Тема"), ("summary", "Кратко"), ("reviewer", "Рассматривает"), ("executor", "Исполнитель"),
               ("due_on", "Срок"), ("status_title", "Статус"), ("overdue", "Просрочено"), ("signer", "Подписал"), ("signed_on", "Дата подписи"),
               ("sent_on", "Отправлено"), ("closed_on", "Закрыто"), ("close_note", "Исполнение"), ("deals", "Сделки"), ("urgent", "Срочно")]


@router.get("/export")
def export_journal(kind: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None, status: Optional[str] = None,
                   entity_id: Optional[str] = None, q: Optional[str] = None, overdue: bool = False,
                   db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Журнал в Excel с теми же правами, что и экран: конфиденциальные и приказы по ЛС — только тем, кому они видны. Выгрузка — в журнале."""
    import io
    from urllib.parse import quote
    from fastapi.responses import Response
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    _need_any(user)
    stmt = select(OfficeDoc)
    kinds = [k for k in (kind or "").split(",") if k]
    if kinds:
        if any(k not in svc.KINDS for k in kinds):
            raise HTTPException(422, "Неизвестный вид документа")
        stmt = stmt.where(OfficeDoc.kind.in_(kinds))
    if entity_id:
        stmt = stmt.where(OfficeDoc.entity_id == entity_id)
    if status:
        stmt = stmt.where(OfficeDoc.status.in_(status.split(",")))
    if date_from:
        stmt = stmt.where(or_(OfficeDoc.reg_date >= date_from, OfficeDoc.reg_date.is_(None) & (OfficeDoc.created_at >= datetime.combine(date_from, datetime.min.time(), timezone.utc))))
    if date_to:
        stmt = stmt.where(or_(OfficeDoc.reg_date <= date_to, OfficeDoc.reg_date.is_(None)))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(OfficeDoc.number.ilike(like), OfficeDoc.subject.ilike(like), OfficeDoc.counterparty.ilike(like)))
    rows = [d for d in db.scalars(stmt.order_by(OfficeDoc.reg_date.asc().nulls_last(), OfficeDoc.number)).all() if svc.can_see(user, d)]
    if overdue:
        rows = [d for d in rows if d.due_on and d.due_on < date.today() and d.status in svc.OPEN]
    wb = Workbook()
    ws = wb.active
    ws.title = "Журнал"
    title = "Журнал Канцелярии" + (f": {', '.join(svc.KINDS[k].lower() for k in kinds)}" if kinds else "")
    period = f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}" if date_from and date_to else (f"с {date_from:%d.%m.%Y}" if date_from else f"по {date_to:%d.%m.%Y}" if date_to else "за всё время")
    ws.append([title, "", "", f"Период: {period}", "", "", f"Выгружено {datetime.now():%d.%m.%Y %H:%M}, {user.display_name}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append([t for _, t in EXPORT_COLS])
    for c in ws[3]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="EEEEEE")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    rev = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    for d in rows:
        v = svc.serialize(db, d, user)
        v["letter_type"] = svc.LETTER_TYPES.get(d.letter_type, "") if d.letter_type else (svc.ORDER_CATEGORIES.get(d.category, "") if d.kind == "order" else "")
        v["channel"] = svc.CHANNELS.get(d.channel, d.channel) if d.channel else ""
        v["reviewer"] = rev.get(d.reviewer_id or "", "")
        v["deals"] = ", ".join(x.number for x in (db.get(Deal, i) for i in d.deal_ids or []) if x is not None)
        v["overdue"] = "да" if v["overdue"] else ""
        v["urgent"] = "да" if d.urgent else ""
        out = []
        for k, _ in EXPORT_COLS:
            x = v.get(k)
            if k in ("reg_date", "sender_date", "due_on", "signed_on", "sent_on", "closed_on") and x:
                x = date.fromisoformat(x)
            out.append(x if x not in (None,) else "")
        ws.append(out)
        if v["overdue"]:
            for c in ws[ws.max_row]:
                c.fill = PatternFill("solid", fgColor="FDECEC")
    for i, (k, t) in enumerate(EXPORT_COLS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 40 if k in ("subject", "summary", "close_note") else 16
        for row in ws.iter_rows(min_row=4, min_col=i, max_col=i):
            for c in row:
                if k in ("reg_date", "sender_date", "due_on", "signed_on", "sent_on", "closed_on"):
                    c.number_format = "DD.MM.YYYY"
                c.alignment = Alignment(wrap_text=k in ("subject", "summary", "close_note"), vertical="top")
    ws.freeze_panes = "B4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(EXPORT_COLS))}{max(ws.max_row, 3)}"
    buf = io.BytesIO()
    wb.save(buf)
    filters = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in
               {"kind": kind, "date_from": date_from, "date_to": date_to, "status": status, "entity_id": entity_id, "q": q, "overdue": overdue}.items() if v}
    from ..core.download_audit import note
    note(kind="export", entity_type="office_journal", row_count=len(rows), filters=filters)
    from ..services.deal_service import audit as _audit
    _audit(db, user.id, None, "office_export", "journal", "export", after={"rows": len(rows), "filters": filters})
    db.commit()
    name = f"Журнал_Канцелярии_{date.today():%Y-%m-%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})



# ---------------------------------------------------------------- ИИ для писем (D-134)

class AiIn(BaseModel):
    action: str                         # analyze | edit | check | translate
    mode: str = "business"              # edit: business | softer | firmer | shorter | grammar
    lang: str = "en"                    # translate: en | zh
    text: Optional[str] = Field(None, max_length=60000)   # свой текст (например выделенный фрагмент); пусто — текст документа


@router.post("/docs/{did}/ai")
def ai(did: str, body: AiIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ИИ предлагает — человек принимает. Ничего в документе не меняется, пока пользователь не нажмёт «Применить»."""
    import time
    from ..adapters.llm import LLMNotConfigured, LLMProviderError
    from ..services import office_ai as oa
    doc = _get(db, did, user)
    if not (svc.is_editor(user, doc) or user.id in (doc.created_by, doc.executor_id, doc.reviewer_id) or user.id in (doc.co_executor_ids or [])
            or svc.in_route(doc, user)):
        raise HTTPException(403, "ИИ доступен делопроизводителю, автору, исполнителю, руководителю и согласующим документа")
    oa._guard(db, doc, user.id)
    started, llm = time.monotonic(), None
    try:
        if body.action == "analyze":
            if doc.kind != "in":
                raise HTTPException(409, "Разбор — для входящих писем")
            text = (body.text or "").strip() or "\n\n".join(x for x in (doc.body, oa.file_text(doc, _files_dir(doc.id))) if x).strip()
            if len(text) < 40:
                raise HTTPException(409, "Нет текста письма: скан без текстового слоя. Вставьте текст письма в поле «Текст письма» или приложите PDF с текстом / .eml")
            res, llm = oa.analyze(text[:oa.MAX_CHARS], {"counterparty": doc.counterparty, "subject": doc.subject, "reg_date": str(doc.reg_date or "")})
            out = {"action": "analyze", **res}
        elif body.action == "edit":
            text = (body.text or doc.body or "").strip()
            if len(text) < 20:
                raise HTTPException(409, "Нет текста для правки")
            new, warn, llm = oa.edit(text[:oa.MAX_CHARS], body.mode, svc.KINDS.get(doc.kind, ""))
            out = {"action": "edit", "mode": body.mode, "before": text, "after": new, "warnings": warn}
        elif body.action == "check":
            if doc.kind not in svc.APPROVABLE or not (doc.body or "").strip():
                raise HTTPException(409, "Проверка — для проекта исходящего с текстом")
            parent = db.get(OfficeDoc, doc.reply_to_id) if doc.reply_to_id else None
            ptext = ""
            if parent is not None and not parent.confidential:
                ptext = "\n\n".join(x for x in (parent.summary, parent.body, oa.file_text(parent, _files_dir(parent.id))) if x)
            res, llm = oa.check(db, doc, ptext)
            out = {"action": "check", "with_parent": bool(ptext), **res}
        elif body.action == "translate":
            text = (body.text or doc.body or "").strip()
            if len(text) < 5:
                raise HTTPException(409, "Нет текста для перевода")
            new, warn, llm = oa.translate(text[:oa.MAX_CHARS], body.lang)
            out = {"action": "translate", "lang": body.lang, "before": text, "after": new, "warnings": warn}
        else:
            raise HTTPException(422, "Действие: analyze, edit, check, translate")
    except (LLMProviderError, LLMNotConfigured, ValueError) as e:
        oa.log_call(db, user.id, doc, body.action, llm, started, False, type(e).__name__)
        db.commit()
        raise HTTPException(502, f"ИИ не ответил: {str(e)[:300]}")
    oa.log_call(db, user.id, doc, body.action, llm, started, True)
    db.commit()
    out["model"] = getattr(llm, "model", "")
    out["note"] = "Предложение ИИ — проверьте перед применением"
    return out



# ---------------------------------------------------------------- связи с юр. блоком, «Оформить письмом» (D-136, D-137)

@router.get("/lookup")
def lookup(q: str = "", what: str = "contract", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подбор договора или претензии для письма: номер, наименование, контрагент. Только для сотрудников Канцелярии."""
    _need_any(user)
    from ..models import Contract, ContractClaim
    like = f"%{q.strip()}%"
    if what == "claim":
        rows = db.scalars(select(ContractClaim).where(or_(ContractClaim.number.ilike(like), ContractClaim.demand.ilike(like)) if q.strip() else True)
                          .order_by(ContractClaim.created_at.desc()).limit(20)).all()
        out = []
        for cl in rows:
            c = db.get(Contract, cl.contract_id)
            out.append({"id": cl.id, "label": f"{cl.number or 'черновик'} · {'исходящая' if cl.direction == 'outgoing' else 'входящая'} · по договору {c.number if c else '—'}",
                        "contract_id": cl.contract_id})
        return out
    rows = db.scalars(select(Contract).where(Contract.deleted_at.is_(None) if hasattr(Contract, "deleted_at") else True)
                      .where(or_(Contract.number.ilike(like), Contract.title.ilike(like), Contract.cp_name.ilike(like), Contract.cp_number.ilike(like)) if q.strip() else True)
                      .order_by(Contract.created_at.desc()).limit(20)).all()
    return [{"id": c.id, "label": f"{c.number or 'проект'} · {c.cp_name or ''} · {c.title or ''}".strip(" ·"), "deal_id": c.deal_id} for c in rows]


@router.get("/linked")
def linked(contract_id: Optional[str] = None, claim_id: Optional[str] = None, deal_id: Optional[str] = None,
           db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Письма, привязанные к договору, претензии или сделке — для карточек юр. блока. Видны только документы, доступные сотруднику."""
    if not (contract_id or claim_id or deal_id):
        raise HTTPException(422, "Укажите договор, претензию или сделку")
    stmt = select(OfficeDoc).where(OfficeDoc.kind.in_(("in", "out")))
    if claim_id:
        stmt = stmt.where(OfficeDoc.claim_id == claim_id)
    elif contract_id:
        stmt = stmt.where(OfficeDoc.contract_id == contract_id)
    rows = db.scalars(stmt.order_by(OfficeDoc.created_at.desc())).all()
    if deal_id:
        rows = [d for d in db.scalars(select(OfficeDoc).where(OfficeDoc.kind.in_(("in", "out")))).all() if deal_id in (d.deal_ids or [])]
    return [{"id": d.id, "kind": d.kind, "number": d.number, "reg_date": d.reg_date.isoformat() if d.reg_date else None, "subject": d.subject,
             "counterparty": d.counterparty, "status": d.status, "status_title": svc.STATUSES.get(d.status, d.status)}
            for d in rows if svc.can_see(user, d) and d.status != "cancelled"]


class FromFeedIn(BaseModel):
    message_id: str
    entity_id: Optional[str] = None


@router.post("/docs/from-feed", status_code=201)
def from_feed(body: FromFeedIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Оформить письмом»: из сообщения в ленте сделки — черновик исходящего: текст, сделка и раздел, адресат, договор, вложения.
    Черновик видит автор; номер присваивается при подписании, как у любого исходящего."""
    import shutil
    from ..adapters import storage
    from ..core.config import get_settings
    from ..models import Attachment, Contract, FeedEntry
    from ..services import deal_feed
    e = db.get(FeedEntry, body.message_id)
    if e is None or e.deleted_at or e.kind not in deal_feed.MESSAGE_KINDS:
        raise HTTPException(404, "Сообщение не найдено")
    deal = get_deal_or_404(db, e.deal_id, user)      # видимость сделки — по общим правилам
    if deal_feed.entry_section(e) not in deal_feed.visible_sections(user):
        raise HTTPException(404, "Сообщение не найдено")
    if not (user.can("office", 2) or deal_feed.can_write(user, deal_feed.entry_section(e))):
        raise HTTPException(403, "Оформить письмом может участник сделки с правом писать в этот раздел")
    ent = db.get(LegalEntity, body.entity_id) if body.entity_id else None
    if ent is None:
        ent = db.scalar(select(LegalEntity).where(LegalEntity.code == "ru")) or db.scalar(select(LegalEntity))
    contract = db.scalar(select(Contract).where(Contract.deal_id == deal.id, Contract.kind == "client").order_by(Contract.created_at.desc())) \
        if hasattr(Contract, "kind") else None
    client = getattr(deal, "client", None)
    text = (e.body or "").strip()
    subject = (e.subject or "").strip() or (text.split("\n", 1)[0][:120] if text else f"Письмо по сделке {deal.number}")
    doc = OfficeDoc(entity_id=ent.id, kind="out", subject=subject, body=text, counterparty=e.counterparty or (client.name if client else ""),
                    deal_ids=[deal.id], deal_section=deal_feed.entry_section(e) if deal_feed.entry_section(e) != "pricing" else "",
                    contract_id=contract.id if contract else None, status="draft", created_by=user.id, files=[], history=[], comments=[],
                    note=f"Из ленты сделки {deal.number}, сообщение от {e.at:%d.%m.%Y %H:%M}" if e.at else f"Из ленты сделки {deal.number}")
    db.add(doc)
    db.flush()
    st = storage(get_settings())
    for aid in e.attachment_ids or []:
        a = db.get(Attachment, aid)
        if a is None:
            continue
        fid = uuid.uuid4().hex
        dst = os.path.join(_files_dir(doc.id), f"{fid}{os.path.splitext(a.file_name)[1].lower()}")
        try:
            st.download(a.storage_path, dst)
        except Exception:   # noqa: BLE001 — файл недоступен: письмо создаётся без него, в комментарии отметка
            doc.comments = [*(doc.comments or []), {"id": uuid.uuid4().hex[:12], "by": user.id, "text": f"Вложение «{a.file_name}» не скопировано — приложите вручную",
                                                    "at": datetime.now(timezone.utc).isoformat()}]
            continue
        doc.files = [*(doc.files or []), {"id": fid, "name": a.file_name, "size": os.path.getsize(dst), "kind": "attachment", "by": user.id,
                                          "at": datetime.now(timezone.utc).isoformat()}]
    svc.hist(doc, user.id, "from_feed", f"сделка {deal.number}")
    db.commit()
    return svc.serialize(db, doc, user, full=True)


# ---------------------------------------------------------------- ознакомление с приказами (D-140)

ACKABLE = ("order", "directive", "internal", "memo")


class AckAssignIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    everyone: bool = False


@router.post("/docs/{did}/acks")
def assign_acks(did: str, body: AckAssignIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Назначить ознакомление: сотрудники, роли или все. Сотрудник получает задачу «Ознакомиться» и видит документ."""
    doc = _get(db, did, user)
    if doc.kind not in ACKABLE:
        raise HTTPException(409, "Ознакомление — для приказов, распоряжений и внутренних документов")
    if not svc.is_editor(user, doc):
        raise HTTPException(403, "Назначает делопроизводитель или ЗГД")
    if doc.status not in ("signed", "in_force"):
        raise HTTPException(409, "Ознакомить можно с подписанным документом")
    people = db.scalars(select(User).where(User.is_active.is_(True))).all()
    ids = {u.id for u in people if body.everyone or u.id in body.user_ids or set(u.roles or []) & set(body.roles)}
    ids -= {"system"}
    if not ids:
        raise HTTPException(422, "Выберите сотрудников, роли или «все сотрудники»")
    have = {a["user_id"] for a in doc.acks or []}
    now = datetime.now(timezone.utc).isoformat()
    new = [{"user_id": i, "by": user.id, "at": now, "acked_at": None, "reminded": []} for i in sorted(ids - have)]
    doc.acks = [*(doc.acks or []), *new]
    deal = db.get(Deal, doc.deal_ids[0]) if doc.deal_ids else None
    for a in new:
        notify(db, deal, a["user_id"], "office_ack", None, f"Канцелярия: ознакомьтесь — {doc.number} «{doc.subject}»")
    svc.hist(doc, user.id, "acks_assigned", f"+{len(new)}")
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.delete("/docs/{did}/acks/{uid}")
def remove_ack(did: str, uid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    doc = _get(db, did, user)
    if not svc.is_editor(user, doc):
        raise HTTPException(403)
    a = svc.acked_by(doc, uid)
    if a is None:
        raise HTTPException(404)
    if a.get("acked_at"):
        raise HTTPException(409, "Сотрудник уже ознакомился — отметку не снять")
    doc.acks = [x for x in doc.acks or [] if x["user_id"] != uid]
    svc.hist(doc, user.id, "ack_removed", uid)
    db.commit()
    return svc.serialize(db, doc, user, full=True)


@router.post("/docs/{did}/ack")
def acknowledge(did: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Ознакомлен»: отметка с датой и временем; снять её нельзя."""
    doc = db.get(OfficeDoc, did)
    a = svc.acked_by(doc, user.id) if doc is not None else None
    if doc is None or a is None:
        raise HTTPException(404, "Ознакомление вам не назначено")
    if not a.get("acked_at"):
        doc.acks = [{**x, "acked_at": datetime.now(timezone.utc).isoformat(), "ip": request.client.host if request.client else ""}
                    if x["user_id"] == user.id else x for x in doc.acks or []]
        svc.hist(doc, user.id, "acked", user.display_name)
        db.commit()
    return svc.serialize(db, doc, user, full=True)
