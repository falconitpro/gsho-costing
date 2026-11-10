"""Финансовые счета на существующих банковских профилях компаний группы и подтверждённые остатки.

Реквизиты не дублируются: номер счёта, банк, БИК/SWIFT остаются в BankProfile (D-030).
Остаток без подтверждённого снимка — «неизвестен» (None), а не ноль (D-023).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import BankProfile, FinanceAccount, FinanceBalanceSnapshot, FinanceTransaction, LegalEntity, LegalProfile
from ..deal_service import audit
from .money import ZERO, dec, s

MOVING = ("posted", "reversed")   # движение денег состоялось; сторно — отдельная проведённая операция обратного направления


def mask(account: str) -> str:
    a = (account or "").replace(" ", "")
    return f"…{a[-4:]}" if len(a) >= 4 else ("—" if not a else "…")


def own_banks(db: Session) -> list[dict]:
    """Банковские профили компаний группы, на которых можно открыть финансовый счёт."""
    ents = {e.id: e for e in db.scalars(select(LegalEntity)).all()}
    taken = {a.bank_profile_id for a in db.scalars(select(FinanceAccount).where(FinanceAccount.status == "active")).all()}
    out = []
    for p in db.scalars(select(LegalProfile).where(LegalProfile.owner_kind == "own", LegalProfile.is_active.is_(True))).all():
        e = ents.get(p.owner_id)
        if not e:
            continue
        for b in p.banks:
            if b.status != "active":
                continue
            out.append({"bank_profile_id": b.id, "legal_entity_id": e.id, "legal_entity": e.name, "profile": p.title, "title": b.title or b.bank_name,
                        "bank_name": b.bank_name, "account_masked": mask(b.account), "currency": (b.currency or "").upper(), "has_account": b.id in taken})
    return out


def _bank_of_entity(db: Session, bank_profile_id: str, legal_entity_id: str) -> BankProfile:
    b = db.get(BankProfile, bank_profile_id)
    if b is None:
        raise HTTPException(422, "Банковский профиль не найден")
    p = db.get(LegalProfile, b.profile_id)
    if p is None or p.owner_kind != "own" or p.owner_id != legal_entity_id:
        raise HTTPException(422, "Банковский профиль не принадлежит выбранной компании группы")
    if b.status != "active":
        raise HTTPException(422, "Банковский профиль в архиве")
    return b


def create(db: Session, user, legal_entity_id: str, bank_profile_id: str, title: str, currency: Optional[str], note: str) -> FinanceAccount:
    if db.get(LegalEntity, legal_entity_id) is None:
        raise HTTPException(422, "Компания группы не найдена")
    b = _bank_of_entity(db, bank_profile_id, legal_entity_id)
    bank_ccy = (b.currency or "").upper()
    if currency and currency.upper() != bank_ccy:
        raise HTTPException(422, f"Валюта счёта должна совпадать с валютой банковского профиля ({bank_ccy})")
    if db.scalar(select(FinanceAccount).where(FinanceAccount.bank_profile_id == b.id, FinanceAccount.status == "active")):
        raise HTTPException(409, "Для этого банковского профиля уже есть действующий финансовый счёт")
    a = FinanceAccount(legal_entity_id=legal_entity_id, bank_profile_id=b.id, title=(title or b.title or b.bank_name or bank_ccy)[:255], currency=bank_ccy,
                       status="active", opened_on=date.today(), note=note or "", created_by=user.id)
    db.add(a)
    db.flush()
    audit(db, user.id, None, "finance_account", a.id, "create", after={"legal_entity_id": legal_entity_id, "bank_profile_id": b.id, "currency": bank_ccy, "title": a.title})
    return a


def archive(db: Session, user, a: FinanceAccount, reason: str) -> FinanceAccount:
    if a.status == "archived":
        return a
    a.status, a.archived_on = "archived", date.today()
    audit(db, user.id, None, "finance_account", a.id, "archive", before={"status": "active"}, after={"status": "archived", "reason": reason})
    return a


def add_snapshot(db: Session, user, a: FinanceAccount, as_of: datetime, amount: Decimal, source: str, external_ref: str, note: str, key: str) -> FinanceBalanceSnapshot:
    dup = db.scalar(select(FinanceBalanceSnapshot).where(FinanceBalanceSnapshot.idempotency_key == key))
    if dup:
        if dup.account_id != a.id:
            raise HTTPException(409, "Ключ идемпотентности уже использован для другого счёта")
        return dup
    if a.status != "active":
        raise HTTPException(409, "Счёт в архиве")
    if as_of.tzinfo is None:
        raise HTTPException(422, "Укажите момент остатка с часовым поясом")
    if as_of > datetime.now(timezone.utc):
        raise HTTPException(422, "Остаток нельзя подтвердить на будущую дату")
    snap = FinanceBalanceSnapshot(account_id=a.id, as_of=as_of, amount=amount, source=source or "manual", external_ref=(external_ref or "")[:255],
                                  note=note or "", idempotency_key=key, confirmed_by=user.id)
    db.add(snap)
    db.flush()
    audit(db, user.id, None, "finance_balance_snapshot", snap.id, "create", after={"account_id": a.id, "as_of": as_of, "amount": s(amount), "currency": a.currency, "idempotency_key": key})
    return snap


def balance(db: Session, a: FinanceAccount, at: Optional[datetime] = None) -> dict:
    """Остаток = последний подтверждённый снимок ≤ T + проведённые поступления − списания после снимка и ≤ T."""
    at = at or datetime.now(timezone.utc)
    snap = db.scalar(select(FinanceBalanceSnapshot).where(FinanceBalanceSnapshot.account_id == a.id, FinanceBalanceSnapshot.as_of <= at)
                     .order_by(FinanceBalanceSnapshot.as_of.desc(), FinanceBalanceSnapshot.confirmed_at.desc()).limit(1))
    if snap is None:
        return {"value": None, "status": "unknown", "snapshot": None, "movements_in": None, "movements_out": None}
    moves = dict(db.execute(select(FinanceTransaction.direction, func.coalesce(func.sum(FinanceTransaction.amount), 0)).where(
        FinanceTransaction.account_id == a.id, FinanceTransaction.status.in_(MOVING), FinanceTransaction.booked_at > snap.as_of,
        FinanceTransaction.booked_at <= at).group_by(FinanceTransaction.direction)).all())
    mi, mo = dec(moves.get("in", 0)) or ZERO, dec(moves.get("out", 0)) or ZERO
    return {"value": snap.amount + mi - mo, "status": "confirmed", "movements_in": mi, "movements_out": mo,
            "snapshot": {"id": snap.id, "as_of": snap.as_of.isoformat(), "amount": s(snap.amount), "source": snap.source}}


def dto(db: Session, a: FinanceAccount, ents: Optional[dict] = None) -> dict:
    b = db.get(BankProfile, a.bank_profile_id)
    e = (ents or {}).get(a.legal_entity_id) or db.get(LegalEntity, a.legal_entity_id)
    bal = balance(db, a)
    return {"id": a.id, "title": a.title, "currency": a.currency, "status": a.status, "legal_entity_id": a.legal_entity_id, "legal_entity": e.name if e else "",
            "bank_profile_id": a.bank_profile_id, "bank_name": b.bank_name if b else "", "account_masked": mask(b.account if b else ""),
            "opened_on": a.opened_on.isoformat() if a.opened_on else None, "archived_on": a.archived_on.isoformat() if a.archived_on else None, "note": a.note,
            "balance": s(bal["value"]), "balance_status": bal["status"], "last_snapshot": bal["snapshot"],
            "movements_in": s(bal["movements_in"]), "movements_out": s(bal["movements_out"])}


def snapshot_dto(x: FinanceBalanceSnapshot) -> dict:
    return {"id": x.id, "as_of": x.as_of.isoformat(), "amount": s(x.amount), "source": x.source, "external_ref": x.external_ref, "note": x.note,
            "confirmed_by": x.confirmed_by, "confirmed_at": x.confirmed_at.isoformat() if x.confirmed_at else None}
