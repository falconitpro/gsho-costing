"""D-211 (аудит CNT-001…003, CNT-005): сверка переноса контактов в единый справочник и приёмка владельцем данных.

Перенос (scripts/contacts_migrate --yes) выполнен на рабочем сервере 27.11.2026. Здесь — проверка результата без изменения данных:
- итоги переноса по источникам (карточка клиента, юр. профиль клиента): перенесено, сопоставлено, создано;
- что ещё не перенесено (строки прежних списков без записи в журнале переноса — например, правки после переноса) и как их перенёс бы скрипт;
- контакты без канала связи (нет почты и телефонов), одинаковая почта у разных контактов одного клиента, почта с ошибкой в источнике;
- контрольная выборка: прежний список и справочник одного клиента рядом;
- приёмка: кто и когда принял перенос, с цифрами на момент приёмки (настройка contacts_migration_accepted, с историей).
Права: просмотр и приёмка — «Контакты клиентов: редактирование» (как правка справочника). Данные не меняются — только отметка о приёмке.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import EDIT
from ..db import get_db
from ..models import Client, ClientContact, ClientContactLink, ContactMigrationMap, LegalProfile, User
from ..services import settings_service
from ..services.deal_service import audit

router = APIRouter(prefix="/api/contacts-migration", tags=["client-contacts"])
SRC = {"client_card": "Карточка клиента", "legal_profile": "Юр. профиль клиента"}
OUT = {"created": "создан контакт", "matched": "сопоставлен с существующим", "ambiguous": "вручную"}
LIMIT = 300


def _need(user: CurrentUser) -> None:
    if not user.can("client_contacts", EDIT):
        raise HTTPException(403, "Сверку переноса проводит редактор справочника контактов")


def _legacy(row: dict) -> dict:
    return {"name": row["full_name"], "position": row["position"], "email": row["work_email"] or (str(row["raw"].get("email") or "") if row["bad_email"] else ""),
            "phone": row["work_phone"], "mobile": row["mobile_phone"], "bad_email": row["bad_email"]}


def _contact(c: ClientContact, link: ClientContactLink) -> dict:
    return {"id": c.id, "name": c.full_name, "position": c.position, "email": c.work_email, "phone": c.work_phone, "mobile": c.mobile_phone,
            "roles": link.roles or [], "active": bool(link.is_active and c.is_active)}


def _review(db: Session, sample: int, seed: str) -> dict:
    from scripts.contacts_migrate import _key, _rows, plan
    from .client_contacts import client_contacts, emails_of
    clients = {c.id: c for c in db.scalars(select(Client))}
    maps = {m.source_key: m for m in db.scalars(select(ContactMigrationMap))}
    # итоги по источникам: что в журнале переноса
    done = Counter()
    for m in maps.values():
        done[(m.source, m.outcome)] += 1
    totals = Counter()
    bad_emails = []
    for c in clients.values():
        for i, r in _rows(c.contacts):
            totals["client_card"] += 1
            if r["bad_email"]:
                bad_emails.append({"client_id": c.id, "client": c.name, "source": SRC["client_card"], **_legacy(r)})
    for p in db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == "client")):
        for i, r in _rows(p.contacts):
            totals["legal_profile"] += 1
            if r["bad_email"]:
                cl = clients.get(p.owner_id)
                bad_emails.append({"client_id": p.owner_id, "client": cl.name if cl else "", "source": SRC["legal_profile"], **_legacy(r)})
    sources = [{"source": s, "title": t, "rows": totals[s], "created": done[(s, "created")], "matched": done[(s, "matched")],
                "ambiguous": done[(s, "ambiguous")]} for s, t in SRC.items()]
    # что не перенесено сейчас (новые или изменённые строки прежних списков) — как поступил бы скрипт
    pl = plan(db)
    pending = [{"client_id": it["client_id"], "client": clients[it["client_id"]].name if it["client_id"] in clients else "",
                "source": SRC.get(it["src"], it["src"]), "outcome": it["outcome"], "outcome_title": OUT.get(it["outcome"], it["outcome"]), **_legacy(it["row"])}
               for it in pl["items"]]
    # справочник: без канала связи, одинаковая почта внутри клиента
    no_channel, dup_email = [], []
    per_client: dict[str, list[tuple[ClientContact, ClientContactLink]]] = defaultdict(list)
    for c, link in db.execute(select(ClientContact, ClientContactLink).join(ClientContactLink, ClientContactLink.contact_id == ClientContact.id)
                              .where(ClientContact.anonymized_at.is_(None))).all():
        per_client[link.client_id].append((c, link))
    for cid, rows in per_client.items():
        name = clients[cid].name if cid in clients else ""
        seen: dict[str, list[str]] = defaultdict(list)
        for c, link in rows:
            if not (link.is_active and c.is_active):
                continue
            if not (emails_of(c) or c.work_phone or c.mobile_phone):
                no_channel.append({"client_id": cid, "client": name, **_contact(c, link)})
            for e in emails_of(c):
                seen[e.lower()].append(c.full_name)
        for e, names in seen.items():
            if len(set(names)) > 1:
                dup_email.append({"client_id": cid, "client": name, "email": e, "names": sorted(set(names))})
    # контрольная выборка: детерминированная по «зерну», только клиенты, у которых был прежний список
    with_legacy = sorted(cid for cid, c in clients.items() if _rows(c.contacts))
    picked = sorted(with_legacy, key=lambda x: hashlib.sha1(f"{seed}|{x}".encode()).hexdigest())[:sample]
    samples = []
    for cid in picked:
        c = clients[cid]
        legacy = []
        for i, r in _rows(c.contacts):
            m = maps.get(_key("client_card", cid, i, r["raw"]))
            legacy.append({**_legacy(r), "outcome": m.outcome if m else "pending", "contact_id": m.contact_id if m else None})
        samples.append({"client_id": cid, "client": c.name, "legacy": legacy,
                        "directory": [_contact(x, link) for x, link in client_contacts(db, cid, True)]})
    return {"sources": sources, "pending": pending[:LIMIT], "pending_total": len(pending),
            "pending_ambiguous": sum(1 for p in pending if p["outcome"] == "ambiguous"),
            "no_channel": no_channel[:LIMIT], "no_channel_total": len(no_channel),
            "dup_email": dup_email[:LIMIT], "dup_email_total": len(dup_email),
            "bad_email": bad_emails[:LIMIT], "bad_email_total": len(bad_emails),
            "sample": samples, "sample_seed": seed,
            "directory_total": sum(len(v) for v in per_client.values()), "clients_with_legacy": len(with_legacy)}


def _acceptance(db: Session) -> Optional[dict]:
    v = settings_service.get(db, "contacts_migration_accepted")
    if not v:
        return None
    u = db.get(User, v.get("by")) if v.get("by") else None
    return {**v, "by_name": u.display_name if u else ""}


@router.get("")
def review(sample: int = 10, seed: str = "", user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    _need(user)
    out = _review(db, max(1, min(sample, 50)), seed or datetime.now(timezone.utc).strftime("%Y%m%d"))
    return {**out, "accepted": _acceptance(db)}


class AcceptIn(BaseModel):
    comment: str = Field(..., min_length=3, max_length=2000)
    sample_seed: str = Field("", max_length=32)
    checked_clients: int = Field(0, ge=0, le=1000)


@router.post("/accept")
def accept(body: AcceptIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Приёмка переноса владельцем данных. Пока есть строки «вручную» — не принимается. Цифры фиксируются на момент приёмки."""
    _need(user)
    r = _review(db, 1, body.sample_seed or "accept")
    if r["pending_ambiguous"]:
        raise HTTPException(409, f"Есть {r['pending_ambiguous']} строк для ручного разбора — сначала разберите их")
    snap = {"by": user.id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "comment": body.comment.strip(),
            "sample_seed": body.sample_seed, "checked_clients": body.checked_clients,
            "counts": {"sources": r["sources"], "pending": r["pending_total"], "no_channel": r["no_channel_total"], "dup_email": r["dup_email_total"],
                       "bad_email": r["bad_email_total"], "directory": r["directory_total"]}}
    settings_service.set_value(db, "contacts_migration_accepted", snap, user.id, "приёмка переноса контактов")
    audit(db, user.id, None, "client_contact", "migration", "accept", None, {"counts": snap["counts"], "checked_clients": body.checked_clients})
    db.commit()
    return _acceptance(db)
