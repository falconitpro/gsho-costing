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
from ..models import Deal, FeedEntry, LegalEntity, OfficeCounter, OfficeDoc, OfficeRoute, OfficeSigner, User
from ..core.clock import today as msk_today

KINDS = {"in": "Входящее письмо", "out": "Исходящее письмо", "order": "Приказ", "directive": "Распоряжение",
         "memo": "Служебная записка", "internal": "Внутренний документ"}
ORDER_CATEGORIES = {"od": "по основной деятельности", "hr": "по личному составу", "ah": "по административно-хозяйственным вопросам"}
INTERNAL_CATEGORIES = {"regulation": "положение", "instruction": "инструкция", "act": "акт", "protocol": "протокол", "other": "другое"}
LETTER_TYPES = {"request": "запрос", "claim": "претензия", "complaint": "рекламация", "gov": "запрос госоргана", "notice": "уведомление",
                "other": "прочее"}
# Источник поступления (входящее) / способ отправки (исходящее) — обязательный реквизит регистрации (D-131).
CHANNELS = {"email": "электронная почта", "edo": "ЭДО (Диадок, СБИС…)", "post": "Почта России — простое", "post_reg": "Почта России — заказное / с уведомлением",
            "courier": "курьер / экспресс-доставка", "hand": "нарочно (лично)", "portal": "портал / личный кабинет заказчика",
            "messenger": "мессенджер (WeCom, WhatsApp, Telegram)", "fax": "факс", "phone": "телефонограмма"}
# Какой уточняющий реквизит просим для источника: адрес отправителя, трек-номер, номер в ЭДО и т. п.
CHANNEL_DETAIL = {"email": "адрес e-mail отправителя", "edo": "оператор и № документа в ЭДО", "post": "", "post_reg": "почтовый идентификатор (трек)",
                  "courier": "служба и № накладной", "hand": "кто передал", "portal": "портал / № обращения", "messenger": "мессенджер и отправитель",
                  "fax": "номер факса", "phone": "кто передал, телефон"}
STATUSES = {"draft": "черновик", "review": "на рассмотрении", "approval": "на согласовании", "approved": "согласовано", "registered": "зарегистрировано", "assigned": "назначен исполнитель", "in_work": "в работе",
            "signed": "подписано", "sent": "отправлено", "in_force": "действует", "closed": "закрыто", "cancelled": "отменено"}
SIGN_METHODS = {"manual": "собственноручно", "facsimile": "факсимиле", "eds": "электронная подпись"}
RESOLUTION_KINDS = {"execute": "к исполнению", "info": "к сведению", "file": "в дело"}
# Сроки ответа по умолчанию (решение владельца 21.11.2026), рабочие дни по производственному календарю РФ
DUE_DAYS = {"request": 2, "claim": 5, "complaint": 5}
DEFAULT_PREFIX = {"ru": "РФ", "kg": "КГ", "cn": "КН"}
OPEN = ("registered", "review", "assigned", "in_work")


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
    return add_business_days(start or msk_today(), n) if n else None


def allocate_number(db: Session, doc: OfficeDoc, on: Optional[date] = None) -> None:
    """Номер под блокировкой строки счётчика (SELECT … FOR UPDATE): два одновременных запроса не получат один номер."""
    if doc.number:
        return
    ent = db.get(LegalEntity, doc.entity_id)
    on = on or msk_today()
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


def in_route(doc: OfficeDoc, user: CurrentUser) -> bool:
    """Согласующий (сотрудник или по роли) — видит документ, пока он в маршруте и после."""
    roles = {r.value for r in user.roles}
    return any(st.get("user_id") == user.id or (st.get("role") in roles) or st.get("by") == user.id for st in (doc.approval or {}).get("steps", []))


def has_scan(doc: OfficeDoc) -> bool:
    """D-129: у письма есть скан или исходный файл (обязателен: входящее — до назначения исполнителя, исходящее — до отправки)."""
    return any(f.get("kind") in ("scan", "original") for f in (doc.files or []))


def acked_by(doc: OfficeDoc, user_id: str) -> Optional[dict]:
    return next((a for a in doc.acks or [] if a.get("user_id") == user_id), None)


def can_see(user: CurrentUser, doc: OfficeDoc) -> bool:
    if acked_by(doc, user.id) is not None and doc.status in ("signed", "in_force", "closed"):   # D-140: кого ознакомить — видит документ
        return True
    if doc.confidential and not (user.can("office", 2) or user.id in (doc.executor_id, doc.created_by) or user.id in (doc.co_executor_ids or [])
                                 or in_route(doc, user)):
        return False
    if doc.kind == "order" and doc.category == "hr":
        return user.can("office_hr") or user.id in (doc.executor_id, doc.created_by) or in_route(doc, user)
    if user.can("office"):
        return True
    return user.id in (doc.executor_id, doc.created_by, doc.reviewer_id) or user.id in (doc.co_executor_ids or []) or in_route(doc, user)


def signer_ok(s: OfficeSigner, on: date) -> bool:
    return s.is_active and (s.valid_from is None or s.valid_from <= on) and (s.valid_to is None or s.valid_to >= on)


def post_to_deals(db: Session, doc: OfficeDoc, user_id: Optional[str]) -> None:
    """Письмо по сделке — в ленту каждой связанной сделки (раздел из карточки письма)."""
    if not doc.deal_ids or doc.kind not in ("in", "out"):
        return
    when = doc.reg_date or msk_today()
    title = f"{'Вх.' if doc.kind == 'in' else 'Исх.'} № {doc.number} от {when:%d.%m.%Y}" + (f" — {doc.subject}" if doc.subject else "")
    for did in doc.deal_ids:
        if db.get(Deal, did) is None:
            continue
        db.add(FeedEntry(deal_id=did, kind="office_in" if doc.kind == "in" else "office_out", direction="in" if doc.kind == "in" else "out",
                         section=doc.deal_section or "", subject=title[:512], counterparty=doc.counterparty, body=doc.summary or "",
                         external_id=f"office:{doc.id}", user_id=user_id, at=datetime.now(timezone.utc)))


def _approval_view(db: Session, doc: OfficeDoc, user: CurrentUser) -> Optional[dict]:
    a = doc.approval or {}
    if not a.get("steps"):
        return None
    from ..core.roles import ROLE_TITLES
    names = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(
        [x for st in a["steps"] for x in (st.get("user_id"), st.get("by")) if x]))).all()} if a["steps"] else {}
    mine = is_approver(db, doc, user)
    return {"name": a.get("name", ""), "mode": a.get("mode", "seq"), "round": a.get("round", 1), "started_at": a.get("started_at"),
            "my_step": mine["id"] if mine else None,
            "steps": [{**st, "who": names.get(st.get("user_id") or "", "") or (f"любой: {ROLE_TITLES.get(Role(st['role']), st['role'])}" if st.get("role") else ""),
                       "by_name": names.get(st.get("by") or "", ""), "status_title": STEP_STATUS.get(st["status"], st["status"])} for st in a["steps"]]}


def serialize(db: Session, doc: OfficeDoc, user: CurrentUser, users: Optional[dict[str, User]] = None, full: bool = False) -> dict:
    users = users or {}
    ex = users.get(doc.executor_id or "") or (db.get(User, doc.executor_id) if doc.executor_id else None)
    sg = db.get(OfficeSigner, doc.signer_id) if doc.signer_id else None
    rv = db.get(User, doc.reviewer_id) if doc.reviewer_id else None
    ent = db.get(LegalEntity, doc.entity_id)
    today = msk_today()
    out = {
        "id": doc.id, "entity_id": doc.entity_id, "entity": ent.name if ent else "", "entity_prefix": prefix_of(ent) if ent else "",
        "kind": doc.kind, "kind_title": KINDS.get(doc.kind, doc.kind), "category": doc.category, "number": doc.number,
        "reg_date": doc.reg_date.isoformat() if doc.reg_date else None, "status": doc.status, "status_title": STATUSES.get(doc.status, doc.status),
        "subject": doc.subject, "summary": doc.summary, "letter_type": doc.letter_type, "channel": doc.channel,
        "counterparty": doc.counterparty, "contact": doc.contact, "sender_number": doc.sender_number,
        "sender_date": doc.sender_date.isoformat() if doc.sender_date else None, "due_on": doc.due_on.isoformat() if doc.due_on else None,
        "overdue": bool(doc.due_on and doc.due_on < today and doc.status in OPEN),
        "urgent": doc.urgent, "confidential": doc.confidential, "needs_reply": doc.needs_reply, "has_scan": has_scan(doc),
        "pages": doc.pages, "attachments_count": doc.attachments_count, "address": doc.address, "tracking": doc.tracking,
        "delivered_on": doc.delivered_on.isoformat() if doc.delivered_on else None, "note": doc.note,
        "comments_count": len(doc.comments or []),
        "reviewer_id": doc.reviewer_id, "reviewer": rv.display_name if rv else "",
        "is_reviewer": doc.status == "review" and user.id == doc.reviewer_id,
        "executor_id": doc.executor_id, "executor": ex.display_name if ex else "", "co_executor_ids": doc.co_executor_ids or [],
        "deal_ids": doc.deal_ids or [], "deal_section": doc.deal_section, "contract_id": doc.contract_id, "claim_id": doc.claim_id,
        "reply_to_id": doc.reply_to_id, "signer_id": doc.signer_id, "signer": f"{sg.position} {sg.full_name}".strip() if sg else "",
        "signed_on": doc.signed_on.isoformat() if doc.signed_on else None, "sign_method": doc.sign_method,
        "sent_on": doc.sent_on.isoformat() if doc.sent_on else None, "effective_on": doc.effective_on.isoformat() if doc.effective_on else None,
        "closed_on": doc.closed_on.isoformat() if doc.closed_on else None, "close_note": doc.close_note,
        "files": [{k: f.get(k) for k in ("id", "name", "size", "kind", "at")} for f in (doc.files or [])],
        "can_edit": is_editor(user, doc) or (user.id == doc.created_by and doc.status == "draft"),
        "approval": _approval_view(db, doc, user),
        "is_executor": user.id == doc.executor_id or user.id in (doc.co_executor_ids or []),
        "acks_total": len(doc.acks or []), "acks_done": sum(1 for a in doc.acks or [] if a.get("acked_at")),
        "my_ack": (lambda a: None if a is None else {"acked_at": a.get("acked_at")})(acked_by(doc, user.id)),
    }
    if full:
        deals = [db.get(Deal, d) for d in doc.deal_ids or []]
        out["deals"] = [{"id": d.id, "number": d.number} for d in deals if d is not None]
        out["body"] = doc.body
        out["history"] = doc.history or []
        cu = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_([c.get("by") for c in doc.comments or [] if c.get("by")]))).all()} if doc.comments else {}
        out["comments"] = [{**c, "author": cu.get(c.get("by"), "")} for c in doc.comments or []]
        ru = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(
            [x for r in doc.resolutions or [] for x in (r.get("by"), r.get("executor_id"), *(r.get("co") or [])) if x]))).all()} if doc.resolutions else {}
        out["resolutions"] = [{**r, "author": ru.get(r.get("by"), ""), "executor": ru.get(r.get("executor_id") or "", ""),
                               "co_names": [ru.get(x, "") for x in r.get("co") or []], "kind_title": RESOLUTION_KINDS.get(r.get("kind"), "")}
                              for r in doc.resolutions or []]
        replies = db.scalars(select(OfficeDoc).where(OfficeDoc.reply_to_id == doc.id)).all()
        out["replies"] = [{"id": r.id, "number": r.number, "status": r.status, "subject": r.subject} for r in replies]
        parent = db.get(OfficeDoc, doc.reply_to_id) if doc.reply_to_id else None
        out["reply_to"] = {"id": parent.id, "number": parent.number, "subject": parent.subject} if parent else None
        out["contract"], out["claim"] = links_view(db, doc)
        au = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_([a["user_id"] for a in doc.acks or []]))).all()} if doc.acks else {}
        out["acks"] = sorted([{"user_id": a["user_id"], "name": au.get(a["user_id"], ""), "at": a.get("at"), "acked_at": a.get("acked_at")} for a in doc.acks or []],
                             key=lambda a: (a["acked_at"] is not None, a["name"]))
    return out


def links_view(db: Session, doc: OfficeDoc) -> tuple[Optional[dict], Optional[dict]]:
    """D-136: договор и претензия юр. блока — номер и наименование (открывать — по правам юр. блока)."""
    from ..models import Contract, ContractClaim
    c = db.get(Contract, doc.contract_id) if doc.contract_id else None
    cl = db.get(ContractClaim, doc.claim_id) if doc.claim_id else None
    return ({"id": c.id, "number": c.number, "title": c.title, "cp_name": c.cp_name} if c else None,
            {"id": cl.id, "number": cl.number or "черновик", "direction": cl.direction, "contract_id": cl.contract_id} if cl else None)



# ---------------------------------------------------------------- согласование (D-129)

APPROVABLE = ("out", "order", "directive", "memo", "internal")
STEP_STATUS = {"waiting": "ожидает очереди", "pending": "на согласовании", "approved": "согласовано", "rejected": "отклонено"}


def routes_for(db: Session, doc: OfficeDoc) -> list[OfficeRoute]:
    """Типовые схемы, подходящие документу: вид, вид приказа, юр. лицо."""
    out = []
    for r in db.scalars(select(OfficeRoute).where(OfficeRoute.is_active.is_(True)).order_by(OfficeRoute.name)).all():
        if r.kinds and doc.kind not in r.kinds:
            continue
        if r.categories and doc.category not in r.categories:
            continue
        if r.entity_id and r.entity_id != doc.entity_id:
            continue
        out.append(r)
    return out


def approval_required(db: Session, doc: OfficeDoc) -> bool:
    return any(r.required for r in routes_for(db, doc))


def user_has_role(db: Session, user_id: str, role: str) -> bool:
    u = db.get(User, user_id)
    return bool(u and u.is_active and role in (u.roles or []))


def active_steps(doc: OfficeDoc) -> list[dict]:
    return [s for s in (doc.approval or {}).get("steps", []) if s.get("status") == "pending"]


def is_approver(db: Session, doc: OfficeDoc, user: CurrentUser) -> Optional[dict]:
    """Шаг, который этот сотрудник может согласовать сейчас (сам или по роли; ЗГД — за отсутствующего по замещению — нет, только своё)."""
    if doc.status != "approval":
        return None
    for st in active_steps(doc):
        role = st.get("role")
        if st.get("user_id") == user.id or (role and any(r.value == role for r in user.roles)):
            return st
    return None


def start_steps(steps: list[dict], mode: str) -> list[dict]:
    import uuid as _u
    out = []
    for i, st in enumerate(steps):
        out.append({"id": _u.uuid4().hex[:12], "user_id": st.get("user_id") or None, "role": st.get("role") or None,
                    "title": (st.get("title") or "").strip()[:120], "status": "pending" if (mode == "par" or i == 0) else "waiting",
                    "by": None, "comment": "", "at": None})
    return out


def advance(doc: OfficeDoc) -> bool:
    """После решения: в последовательном порядке открыть следующий шаг; True — все согласовали."""
    a = dict(doc.approval or {})
    steps = [dict(x) for x in a.get("steps", [])]
    if all(x["status"] == "approved" for x in steps):
        a["steps"] = steps
        doc.approval = a
        return True
    if a.get("mode", "seq") == "seq" and not any(x["status"] == "pending" for x in steps):
        nxt = next((x for x in steps if x["status"] == "waiting"), None)
        if nxt:
            nxt["status"] = "pending"
    a["steps"] = steps
    doc.approval = a
    return False


def step_people(db: Session, st: dict) -> list[str]:
    """Кого уведомить по шагу: сотрудник или все активные с ролью."""
    if st.get("user_id"):
        return [st["user_id"]]
    if st.get("role"):
        return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if st["role"] in (u.roles or [])]
    return []
