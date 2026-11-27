"""D-127: обсуждения сделки — одна лента на сделку (сообщения, загруженные письма, события), у записи — раздел сделки.

Права: ленту видит тот, кто видит сделку (can_see_deal); раздел «Расчёт и утверждение» — с правом «Себестоимость»,
«Финансы» — с правом «Себестоимость» или «Раздел «Финансы»». Наблюдатель читает, но не пишет.
Правка своего сообщения — всегда, после 15 минут прежний текст сохраняется в истории («исправлено»); удалить — только в первые 15 минут.
"""
from __future__ import annotations

import email
import email.policy
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.roles import Role
from ..models import Attachment, Deal, FeedEntry, User
from ..core.clock import today as msk_today

SECTIONS = {
    "": "Общее",
    "sale": "Продажа",
    "logistics": "Логистика",
    "service": "Сервис",
    "pricing": "Расчёт и утверждение",
    "legal": "Договор и юр.",
    "execution": "Исполнение",
    "finance": "Финансы",
}
TAGS = {"q_client": "вопрос клиенту", "a_client": "ответ клиента", "q_factory": "вопрос заводу", "a_factory": "ответ завода",
        "q_carrier": "вопрос перевозчику", "a_carrier": "ответ перевозчика", "internal": "внутреннее"}
ANSWER_OF = {"q_client": "a_client", "q_factory": "a_factory", "q_carrier": "a_carrier"}
EDIT_FREE = timedelta(minutes=15)
MESSAGE_KINDS = ("comment", "mail_in", "mail_out", "office_in", "office_out")   # office_* — письма Канцелярии по сделке (D-128)


def visible_sections(user: CurrentUser) -> set[str]:
    out = {"", "sale", "logistics", "service", "legal", "execution"}
    if user.can("cost"):
        out |= {"pricing", "finance"}
    if user.can("finance_section"):
        out.add("finance")
    return out


def can_write(user: CurrentUser, section: str) -> bool:
    if user.has(Role.OBSERVER) and not user.has(Role.MANAGER, Role.LOGISTICIAN, Role.SERVICE_HEAD, Role.CD, Role.FD, Role.GD, Role.LAWYER, Role.ADMIN):
        return False
    return section in visible_sections(user)


def section_of_system(e: FeedEntry) -> str:
    """Старым событиям раздел не записан — подбираем по теме, иначе «Общее»."""
    s = (e.subject or "").lower()
    if e.kind in ("wecom",) or "завод" in s or "производител" in s:
        return "sale"
    if "логист" in s or "партия" in s or "перевоз" in s:
        return "logistics"
    if "юрид" in s or "договор" in s or "контракт" in s:
        return "legal"
    if "исполн" in s or "этап" in s or "приёмк" in s:
        return "execution"
    return ""


def entry_section(e: FeedEntry) -> str:
    return e.section if e.section or e.kind in MESSAGE_KINDS else section_of_system(e)


def serialize_entry(db: Session, e: FeedEntry, user: CurrentUser, users: dict[str, User], atts: dict[str, Attachment]) -> dict:
    now = datetime.now(timezone.utc)
    at = e.at if e.at.tzinfo else e.at.replace(tzinfo=timezone.utc)
    author = users.get(e.user_id or "")
    mine = e.user_id == user.id
    deleted = e.deleted_at is not None
    return {
        "id": e.id, "kind": e.kind, "direction": e.direction, "section": entry_section(e), "subject": e.subject,
        "body": "" if deleted else e.body, "counterparty": e.counterparty, "at": at.isoformat(),
        "author_id": e.user_id, "author": author.display_name if author else "",
        "parent_id": e.parent_id, "tags": e.tags or [], "mentions": [{"id": m, "name": users[m].display_name} for m in (e.mentions or []) if m in users],
        "awaiting": bool(e.awaiting) and not deleted, "due_on": e.due_on.isoformat() if e.due_on else None,
        "overdue": bool(e.awaiting and not e.resolved_at and e.due_on and e.due_on < msk_today()),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None, "pinned": bool(e.pinned) and not deleted,
        "edited": e.edited_at is not None, "deleted": deleted,
        "files": [] if deleted else [{"id": a.id, "name": a.file_name, "size": a.size, "url": f"/api/files/{a.storage_path}"}
                                     for a in (atts.get(i) for i in (e.attachment_ids or [])) if a is not None],
        "can_edit": mine and not deleted and e.kind == "comment",
        "can_delete": mine and not deleted and now - at <= EDIT_FREE,
    }


def load(db: Session, deal: Deal, user: CurrentUser, section: Optional[str] = None, tag: Optional[str] = None,
         q: Optional[str] = None, open_only: bool = False, kinds: Optional[list[str]] = None) -> dict:
    vis = visible_sections(user)
    stmt = select(FeedEntry).where(FeedEntry.deal_id == deal.id)
    if kinds:
        stmt = stmt.where(FeedEntry.kind.in_(kinds))
    if open_only:
        stmt = stmt.where(FeedEntry.awaiting.is_(True), FeedEntry.resolved_at.is_(None), FeedEntry.deleted_at.is_(None))
    if q and q.strip():
        qq = q.strip()
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            doc = func.to_tsvector("russian", func.coalesce(FeedEntry.subject, "") + " " + func.coalesce(FeedEntry.body, ""))
            stmt = stmt.where(or_(doc.op("@@")(func.plainto_tsquery("russian", qq)), FeedEntry.body.ilike(f"%{qq}%"),
                                  FeedEntry.subject.ilike(f"%{qq}%"), FeedEntry.counterparty.ilike(f"%{qq}%")))
        else:
            stmt = stmt.where(or_(FeedEntry.body.ilike(f"%{qq}%"), FeedEntry.subject.ilike(f"%{qq}%")))
    rows = db.scalars(stmt.order_by(FeedEntry.at.desc())).all()
    rows = [e for e in rows if entry_section(e) in vis]
    counts: dict[str, int] = {}
    open_counts: dict[str, int] = {}
    for e in rows:
        if e.kind in MESSAGE_KINDS and e.deleted_at is None:
            counts[entry_section(e)] = counts.get(entry_section(e), 0) + 1
        if e.awaiting and e.resolved_at is None and e.deleted_at is None:
            open_counts[entry_section(e)] = open_counts.get(entry_section(e), 0) + 1
    if section is not None:
        rows = [e for e in rows if entry_section(e) == section]
    if tag:
        rows = [e for e in rows if tag in (e.tags or [])]
    uids = {e.user_id for e in rows if e.user_id} | {m for e in rows for m in (e.mentions or [])}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    aids = {i for e in rows for i in (e.attachment_ids or [])}
    atts = {a.id: a for a in db.scalars(select(Attachment).where(Attachment.id.in_(aids))).all()} if aids else {}
    items = [serialize_entry(db, e, user, users, atts) for e in rows]
    return {
        "items": items,
        "pinned": [i for i in items if i["pinned"]],
        "counts": counts, "open": open_counts,
        "sections": {k: v for k, v in SECTIONS.items() if k in vis},
        "writable": sorted(s for s in vis if can_write(user, s)),
        "tags": TAGS,
    }


def as_current(db: Session, u: User) -> CurrentUser:
    from .access_service import acting_for, perms_for
    return CurrentUser(id=u.id, email=u.email, display_name=u.display_name, roles={Role(r) for r in (u.roles or [])},
                       observer_scope=u.observer_scope or {}, perms=perms_for(db, u), acting_for=acting_for(db, u))


def participants(db: Session, deal: Deal, section: str = "") -> list[User]:
    """Кому можно написать @: активные сотрудники, которые видят сделку и раздел (без наблюдателей)."""
    from ..api.common import can_see_deal
    out = []
    for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all():
        cu = as_current(db, u)
        if can_see_deal(cu, deal) and can_write(cu, section):
            out.append(u)
    return out


def parse_eml(raw: bytes) -> dict:
    """Письмо .eml → тема, отправитель, дата, текст и вложения (имя, байты)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            body = re.sub(r"<br\s*/?>|</p>", "\n", body, flags=re.I)
            body = re.sub(r"<[^>]+>", "", body)
    files = []
    for a in msg.iter_attachments():
        name = a.get_filename()
        if name:
            files.append((name, a.get_payload(decode=True) or b""))
    when = None
    try:
        when = email.utils.parsedate_to_datetime(msg["date"]) if msg["date"] else None
    except (TypeError, ValueError):
        when = None
    return {"subject": str(msg["subject"] or "")[:500], "from": str(msg["from"] or "")[:255], "to": str(msg["to"] or "")[:255],
            "date": when, "body": re.sub(r"\n{3,}", "\n\n", body or "").strip()[:20000], "files": files}
