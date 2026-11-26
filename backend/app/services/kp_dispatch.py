"""Адресаты и снимок отправки КП (D-188; решение владельца «Единый справочник контактов» v1.0, раздел 3).

* адресаты — только контакты клиента из единого справочника (до завершения перехода — и прежний список карточки клиента);
* ничего не отмечается заранее, «Кому» и «Копия» — раздельно, адрес — конкретный из адресов контакта;
* каждый адрес проверяется на сервере: формат, повтор, принадлежность контактам клиента;
* снимок отправки неизменяем; повтор с тем же ключом новой записи не создаёт."""
from __future__ import annotations

import re
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Deal, KpDispatch, KpDispatchRecipient

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def candidates(db: Session, deal: Deal) -> list[dict]:
    """Контакты клиента сделки с адресами — для выбора; без отметок «выбрано»."""
    from ..api.client_contacts import CONTACT_ROLES, client_contacts, emails_of
    out = []
    if deal.client_id:
        for c, link in client_contacts(db, deal.client_id):
            ems = emails_of(c)
            if ems:
                out.append({"contact_id": c.id, "link_id": link.id, "name": c.full_name, "position": c.position, "roles": link.roles or [],
                            "roles_titles": [CONTACT_ROLES.get(r, r) for r in (link.roles or [])], "is_primary": link.is_primary,
                            "emails": ems, "phone": c.work_phone, "source": "справочник"})
    known = {e for x in out for e in x["emails"]}
    # переходный период: прежний список карточки клиента, если адреса нет в справочнике
    for x in ((deal.client.contacts if deal.client else None) or []):
        em = str((x or {}).get("email") or "").strip().lower() if isinstance(x, dict) else ""
        if em and em not in known:
            known.add(em)
            out.append({"contact_id": None, "link_id": None, "name": x.get("name") or "", "position": x.get("position") or "", "roles": [],
                        "roles_titles": [], "is_primary": False, "emails": [em], "phone": x.get("phone") or "", "source": "прежний список карточки"})
    return out


def resolve(db: Session, deal: Deal, to: list[str], cc: Optional[list[str]] = None) -> list[dict]:
    """Проверка адресатов: хотя бы один «Кому», корректные, без повторов, только из контактов клиента."""
    cand = candidates(db, deal)
    by_email = {e: c for c in cand for e in c["emails"]}
    items, seen, bad, foreign = [], set(), [], []
    for kind, lst in (("to", to or []), ("cc", cc or [])):
        for raw in lst:
            em = (raw or "").strip().lower()
            if not em:
                continue
            if not EMAIL.match(em):
                bad.append(em)
                continue
            if em in seen:
                raise HTTPException(422, f"Адрес указан дважды: {em}")
            seen.add(em)
            c = by_email.get(em)
            if c is None:
                foreign.append(em)
                continue
            items.append({"kind": kind, "email": em, "contact_id": c["contact_id"], "link_id": c["link_id"], "name": c["name"], "position": c["position"],
                          "roles_titles": c["roles_titles"], "source": c["source"]})
    if bad:
        raise HTTPException(422, "Адреса с ошибкой: " + ", ".join(bad))
    if foreign:
        raise HTTPException(422, {"message": "Адресов нет среди контактов клиента — добавьте контакт в карточке клиента: " + ", ".join(foreign),
                                  "unknown": foreign})
    if not any(i["kind"] == "to" for i in items):
        raise HTTPException(422, "Выберите хотя бы одного адресата в поле «Кому»")
    return items


def record(db: Session, deal: Deal, items: list[dict], method: str, user_id: str, subject: str = "", message_id: str = "",
           version_id: Optional[str] = None, revision_no: Optional[int] = None, key: str = "") -> KpDispatch:
    """Неизменяемый снимок отправки; повтор с тем же ключом возвращает прежний снимок."""
    key = (key or "").strip()[:64] or uuid.uuid4().hex
    ex = db.scalars(select(KpDispatch).where(KpDispatch.dispatch_key == key)).first()
    if ex:
        return ex
    d = KpDispatch(deal_id=deal.id, version_id=version_id, revision_no=revision_no, method=method, sent_by=user_id, subject=subject[:1000],
                   message_id=message_id or "", dispatch_key=key)
    db.add(d)
    db.flush()
    for i, it in enumerate(items):
        db.add(KpDispatchRecipient(dispatch_id=d.id, kind=it["kind"], position=i, contact_id=it.get("contact_id"), link_id=it.get("link_id"),
                                   display_name=it.get("name", "")[:255], job_title=it.get("position", "")[:255], email=it["email"]))
    return d


def existing(db: Session, key: str) -> Optional[KpDispatch]:
    key = (key or "").strip()[:64]
    return db.scalars(select(KpDispatch).where(KpDispatch.dispatch_key == key)).first() if key else None


def history(db: Session, deal_id: str) -> list[dict]:
    from ..models import User
    rows = db.scalars(select(KpDispatch).where(KpDispatch.deal_id == deal_id).order_by(KpDispatch.sent_at.desc())).all()
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_({r.sent_by for r in rows if r.sent_by}))).all()) if rows else {}
    out = []
    for d in rows:
        rc = db.scalars(select(KpDispatchRecipient).where(KpDispatchRecipient.dispatch_id == d.id).order_by(KpDispatchRecipient.position)).all()
        out.append({"id": d.id, "sent_at": d.sent_at, "method": d.method, "sent_by": names.get(d.sent_by, ""), "subject": d.subject, "message_id": d.message_id,
                    "revision_no": d.revision_no, "recipients": [{"kind": r.kind, "name": r.display_name, "position": r.job_title, "email": r.email} for r in rc]})
    return out
