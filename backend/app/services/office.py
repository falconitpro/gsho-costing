"""Канцелярия (D-128): журналы, номера, сроки, видимость, связь со сделками.

Номер: <код юр. лица>-<журнал>-<год>-<порядковый>, например РФ-ВХ-2026-0001, КГ-ИСХ-2026-0003, РФ-ПР-ОД-2026-0012.
Входящее получает номер при регистрации, исходящее и внутренние документы — при подписании. Номер не меняется и не
пропускается: отменённый документ остаётся в журнале с отметкой «отменён».
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import Role
from ..core.workflow import add_business_days
from ..models import Deal, FeedEntry, LegalEntity, OfficeCounter, OfficeDoc, OfficeSigner, User

KINDS = {"in": "Входящее письмо", "out": "Исходящее письмо", "order": "Приказ", "directive": "Распоряжение",
         "memo": "Служебная записка", "internal": "Внутренний документ"}
ORDER_CATEGORIES = {"od": "по основной деятельности", "hr": "по личному составу", "ah": "по административно-хозяйственным вопросам"}
INTERNAL_CATEGORIES = {"regulation": "положение", "instruction": "инструкция", "act": "акт", "protocol": "протокол", "other": "другое"}
LETTER_TYPES = {"request": "запрос", "claim": "претензия", "complaint": "рекламация", "gov": "запрос госоргана", "notice": "уведомление",
                "other": "прочее"}
CHANNELS = {"email": "email", "post": "почта", "courier": "курьер", "edo": "ЭДО", "portal": "портал заказчика", "hand": "нарочно"}
STATUSES = {"draft": "черновик", "registered": "зарегистрировано", "assigned": "назначен исполнитель", "in_work": "в работе",
            "signed": "подписано", "sent": "отправлено", "in_force": "действует", "closed": "закрыто", "cancelled": "отменено"}
SIGN_METHODS = {"manual": "собственноручно", "facsimile": "факсимиле", "eds": "электронная подпись"}
# Сроки ответа по умолчанию (решение владельца 21.11.2026), рабочие дни по производственному календарю РФ
DUE_DAYS = {"request": 2, "claim": 5, "complaint": 5}
DEFAULT_PREFIX = {"ru": "РФ", "kg": "КГ", "cn": "КН"}
OPEN = ("registered", "assigned", "in_work")


def journal_of(kind: str, category: str = "") -> str:
    if kind == "in":
        return "ВХ"
    if kind == "out":
        return "ИСХ"
    if kind == "order":
        return {"od": "ПР-ОД", "hr": "ПР-К", "ah": "ПР-АХ"}.get(category, "ПР-ОД")
    if kind == "directive":
        return "РП"
    if kind == "memo":
        return "СЗ"
    return "ВН"


def prefix_of(e: LegalEntity) -> str:
    return (e.doc_prefix or DEFAULT_PREFIX.get(e.code, e.code.upper()))[:8]


def default_due(letter_type: str, start: Optional[date] = None) -> Optional[date]:
    n = DUE_DAYS.get(letter_type)
    return add_business_days(start or date.today(), n) if n else None


def allocate_number(db: Session, doc: OfficeDoc, on: Optional[date] = None) -> None:
    """Номер под блокировкой строки счётчика (SELECT … FOR UPDATE): два одновременных запроса не получат один номер."""
    if doc.number:
        return
    ent = db.get(LegalEntity, doc.entity_id)
    on = on or date.today()
    journal = journal_of(doc.kind, doc.category)
    stmt = select(OfficeCounter).where(OfficeCounter.entity_id == doc.entity_id, OfficeCounter.journal == journal, OfficeCounter.year == on.year)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    c = db.scalars(stmt).first()
    if c is None:
        c = OfficeCounter(entity_id=doc.entity_id, journal=journal, year=on.year, seq=0)
        db.add(c)
        db.flush()
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            c = db.scalars(stmt).first()
    c.seq += 1
    doc.journal, doc.year, doc.seq, doc.reg_date = journal, on.year, c.seq, on
    doc.number = f"{prefix_of(ent)}-{journal}-{on.year}-{c.seq:04d}"


def hist(doc: OfficeDoc, user_id: Optional[str], action: str, note: str = "") -> None:
    doc.history = [*(doc.history or []), {"at": datetime.now(timezone.utc).isoformat(), "by": user_id, "action": action, "note": note}]


def is_editor(user: CurrentUser, doc: Optional[OfficeDoc] = None) -> bool:
    if doc is not None and doc.kind == "order" and doc.category == "hr":
        return user.can("office_hr", 2)
    return user.can("office", 2)


def can_see(user: CurrentUser, doc: OfficeDoc) -> bool:
    if doc.kind == "order" and doc.category == "hr":
        return user.can("office_hr") or user.id in (doc.executor_id, doc.created_by)
    if user.can("office"):
        return True
    return user.id in (doc.executor_id, doc.created_by) or user.id in (doc.co_executor_ids or [])


def signer_ok(s: OfficeSigner, on: date) -> bool:
    return s.is_active and (s.valid_from is None or s.valid_from <= on) and (s.valid_to is None or s.valid_to >= on)


def post_to_deals(db: Session, doc: OfficeDoc, user_id: Optional[str]) -> None:
    """Письмо по сделке — в ленту каждой связанной сделки (раздел из карточки письма)."""
    if not doc.deal_ids or doc.kind not in ("in", "out"):
        return
    when = doc.reg_date or date.today()
    title = f"{'Вх.' if doc.kind == 'in' else 'Исх.'} № {doc.number} от {when:%d.%m.%Y}" + (f" — {doc.subject}" if doc.subject else "")
    for did in doc.deal_ids:
        if db.get(Deal, did) is None:
            continue
        db.add(FeedEntry(deal_id=did, kind="office_in" if doc.kind == "in" else "office_out", direction="in" if doc.kind == "in" else "out",
                         section=doc.deal_section or "", subject=title[:512], counterparty=doc.counterparty, body=doc.summary or "",
                         external_id=f"office:{doc.id}", user_id=user_id, at=datetime.now(timezone.utc)))


def serialize(db: Session, doc: OfficeDoc, user: CurrentUser, users: Optional[dict[str, User]] = None, full: bool = False) -> dict:
    users = users or {}
    ex = users.get(doc.executor_id or "") or (db.get(User, doc.executor_id) if doc.executor_id else None)
    sg = db.get(OfficeSigner, doc.signer_id) if doc.signer_id else None
    ent = db.get(LegalEntity, doc.entity_id)
    today = date.today()
    out = {
        "id": doc.id, "entity_id": doc.entity_id, "entity": ent.name if ent else "", "entity_prefix": prefix_of(ent) if ent else "",
        "kind": doc.kind, "kind_title": KINDS.get(doc.kind, doc.kind), "category": doc.category, "number": doc.number,
        "reg_date": doc.reg_date.isoformat() if doc.reg_date else None, "status": doc.status, "status_title": STATUSES.get(doc.status, doc.status),
        "subject": doc.subject, "summary": doc.summary, "letter_type": doc.letter_type, "channel": doc.channel,
        "counterparty": doc.counterparty, "contact": doc.contact, "sender_number": doc.sender_number,
        "sender_date": doc.sender_date.isoformat() if doc.sender_date else None, "due_on": doc.due_on.isoformat() if doc.due_on else None,
        "overdue": bool(doc.due_on and doc.due_on < today and doc.status in OPEN),
        "executor_id": doc.executor_id, "executor": ex.display_name if ex else "", "co_executor_ids": doc.co_executor_ids or [],
        "deal_ids": doc.deal_ids or [], "deal_section": doc.deal_section, "contract_id": doc.contract_id, "claim_id": doc.claim_id,
        "reply_to_id": doc.reply_to_id, "signer_id": doc.signer_id, "signer": f"{sg.position} {sg.full_name}".strip() if sg else "",
        "signed_on": doc.signed_on.isoformat() if doc.signed_on else None, "sign_method": doc.sign_method,
        "sent_on": doc.sent_on.isoformat() if doc.sent_on else None, "effective_on": doc.effective_on.isoformat() if doc.effective_on else None,
        "closed_on": doc.closed_on.isoformat() if doc.closed_on else None, "close_note": doc.close_note,
        "files": [{k: f.get(k) for k in ("id", "name", "size", "kind", "at")} for f in (doc.files or [])],
        "can_edit": is_editor(user, doc) or (user.id == doc.created_by and doc.status == "draft"),
        "is_executor": user.id == doc.executor_id or user.id in (doc.co_executor_ids or []),
    }
    if full:
        deals = [db.get(Deal, d) for d in doc.deal_ids or []]
        out["deals"] = [{"id": d.id, "number": d.number} for d in deals if d is not None]
        out["body"] = doc.body
        out["history"] = doc.history or []
        replies = db.scalars(select(OfficeDoc).where(OfficeDoc.reply_to_id == doc.id)).all()
        out["replies"] = [{"id": r.id, "number": r.number, "status": r.status, "subject": r.subject} for r in replies]
        parent = db.get(OfficeDoc, doc.reply_to_id) if doc.reply_to_id else None
        out["reply_to"] = {"id": parent.id, "number": parent.number, "subject": parent.subject} if parent else None
    return out
