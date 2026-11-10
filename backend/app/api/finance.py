"""Раздел «Финансы», вариант Б: тонкий роутер /api/finance (docs/finance/04_API_и_права.md).

Бизнес-логика — в services/finance. Каждый endpoint проверяет право на функцию и видимость объекта.
Деньги в JSON — строки с двумя знаками. Mutation банковского факта требуют заголовок Idempotency-Key.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import AuditLog, FinanceAccount, FinanceAllocation, FinanceBalanceSnapshot, FinanceObligation, FinanceTransaction, LegalEntity
from ..services.finance import access, accounts as acc, obligations as obl, overview as ov, transactions as txs
from ..services.finance.money import ccy, dec, s

router = APIRouter(prefix="/api/finance", tags=["finance"])


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Конфликт: такая запись уже существует (дубль операции, ключа или счёта)")


def _key(idem: Optional[str], required: bool = True) -> str:
    k = (idem or "").strip()
    if not k:
        if required:
            raise HTTPException(400, "Заголовок Idempotency-Key обязателен для этого действия")
        import uuid
        k = f"auto:{uuid.uuid4()}"
    if len(k) > 120:
        raise HTTPException(400, "Idempotency-Key длиннее 120 символов")
    return k


def _money(v, what: str = "Сумма") -> Decimal:
    try:
        d = dec(v)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if d is None:
        raise HTTPException(422, f"{what}: не указана")
    return d


def _ccy(v) -> str:
    try:
        return ccy(v)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ------------------------------------------------------------------ мета и обзор

@router.get("/meta")
def meta(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    ents = [{"id": e.id, "code": e.code, "name": e.name, "currency": e.currency} for e in db.scalars(select(LegalEntity).order_by(LegalEntity.name)).all()]
    currencies = sorted({c for (c,) in db.execute(select(FinanceObligation.currency).distinct()).all()} | {a.currency for a in db.scalars(select(FinanceAccount)).all()})
    return {"can_edit": access.can_edit(user), "entities": ents, "currencies": currencies, "today": date.today().isoformat(),
            "tabs": ["overview", "calendar", "liquidity"], "statuses": obl.STATUS_TITLES}


@router.get("/overview")
def overview(as_of: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    return ov.build(db, access.Visibility(db, user), as_of or date.today())


# ------------------------------------------------------------------ обязательства

@router.get("/obligations")
def obligations(as_of: Optional[date] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"), currency: Optional[str] = None,
                legal_entity_id: Optional[str] = None, deal_id: Optional[str] = None, contract_id: Optional[str] = None, status: Optional[str] = None,
                date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", include_inactive: bool = False, reconciliation: bool = False,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    f = obl.ObligationFilter(as_of=as_of or date.today(), direction=direction, currency=currency.upper() if currency else None, legal_entity_id=legal_entity_id,
                             deal_id=deal_id, contract_id=contract_id, status=status, date_from=date_from, date_to=date_to, search=search.strip(),
                             include_inactive=include_inactive, reconciliation=reconciliation, limit=limit, offset=offset)
    return obl.query(db, access.Visibility(db, user), f)


def _obligation(db: Session, user: CurrentUser, oid: str) -> tuple[FinanceObligation, access.Visibility]:
    vis = access.Visibility(db, user)
    o = db.get(FinanceObligation, oid)
    if o is None or not vis.obligation(o):
        raise HTTPException(404, "Обязательство не найдено")
    return o, vis


@router.get("/obligations/{oid}")
def obligation_card(oid: str, as_of: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка: строка реестра + разнесения с операциями + история синхронизации. Юридические условия — только чтение."""
    access.require_read(user)
    o, vis = _obligation(db, user, oid)
    row = obl.query(db, vis, obl.ObligationFilter(as_of=as_of or date.today(), ids=[o.id], include_inactive=True, limit=1))["items"][0]
    allocs = db.scalars(select(FinanceAllocation).where(FinanceAllocation.obligation_id == o.id).order_by(FinanceAllocation.created_at)).all()
    tx_by = {t.id: t for t in db.scalars(select(FinanceTransaction).where(FinanceTransaction.id.in_([a.transaction_id for a in allocs] or ["-"]))).all()}
    hist = db.scalars(select(AuditLog).where(AuditLog.entity == "finance_obligation", AuditLog.entity_id == o.id).order_by(AuditLog.at.desc()).limit(50)).all()
    basis = o.basis_snapshot or {}
    return {**row, "terms_text": basis.get("terms_text", ""), "event_source": basis.get("event_source", ""), "pending": basis.get("pending"),
            "allocations": [txs.alloc_dto(a, tx_by.get(a.transaction_id)) for a in allocs],
            "history": [{"at": h.at.isoformat(), "action": h.action, "user_id": h.user_id, "before": h.before, "after": h.after, "comment": h.comment} for h in hist],
            "readonly_note": "Сумма и срок редактируются в договоре (доп. соглашением). Здесь — только проекция строки графика."}


@router.get("/obligations/{oid}/explanation")
def obligation_explanation(oid: str, as_of: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    o, vis = _obligation(db, user, oid)
    day = as_of or date.today()
    allocs = db.scalars(select(FinanceAllocation).where(FinanceAllocation.obligation_id == o.id)).all()
    st = obl.status_of(o, obl.paid_map(db, [o.id])[o.id], day)
    return {
        "value": s(st["remaining"]), "currency": o.currency, "as_of": day.isoformat(),
        "formula": "остаток = max(сумма обязательства − Σ подтверждённых разнесений, 0); временные разнесения показываются отдельно и в оплату не входят",
        "amount": s(o.amount), "status": st["status"], "status_rule": "просрочка — только от юридического срока; прогнозная дата её не отменяет",
        "included": [{"id": a.id, "transaction_id": a.transaction_id, "amount": s(a.amount_obligation), "status": a.status} for a in allocs if a.status == "confirmed"],
        "excluded": [{"id": a.id, "transaction_id": a.transaction_id, "amount": s(a.amount_obligation), "reason": "временное разнесение — требует подтверждения" if a.status == "provisional" else f"сторнировано: {a.reverse_reason}"}
                     for a in allocs if a.status != "confirmed"],
        "source": {"type": o.source_type, "id": o.source_id, "line_key": o.source_line_key, "revision": o.source_revision, "synced_at": o.synced_at.isoformat() if o.synced_at else None,
                   "url": obl.source_url(o)},
        "dates": {"legal_due_on": o.legal_due_on.isoformat() if o.legal_due_on else None, "legal_due_text": o.legal_due_text,
                  "forecast_on": o.forecast_on.isoformat() if o.forecast_on else None, "event_on": o.event_on.isoformat() if o.event_on else None, "event_is_fact": o.event_is_fact},
        "flags": st["flags"], "fx": [{"allocation_id": a.id, "rate": str(a.fx_rate), "on": a.fx_on.isoformat() if a.fx_on else None, "source": a.fx_source} for a in allocs if a.fx_rate],
    }


@router.post("/sync")
def sync(dry_run: bool = True, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Пересборка проекции подписанных договоров. По умолчанию dry-run — только отчёт, без изменений."""
    access.require_edit(user)
    rep = obl.rebuild(db, user.id, dry_run=dry_run)
    if not dry_run:
        _commit(db)
    else:
        db.rollback()
    return rep


# ------------------------------------------------------------------ счета и остатки

class AccountIn(BaseModel):
    legal_entity_id: str
    bank_profile_id: str
    title: str = ""
    currency: Optional[str] = None
    note: str = ""


class ReasonIn(BaseModel):
    reason: str = ""


class SnapshotIn(BaseModel):
    as_of: datetime
    amount: Decimal
    source: str = Field("manual", pattern="^(manual|statement_import|migration)$")
    external_ref: str = ""
    note: str = ""


def _account(db: Session, aid: str) -> FinanceAccount:
    a = db.get(FinanceAccount, aid)
    if a is None:
        raise HTTPException(404, "Счёт не найден")
    return a


@router.get("/accounts")
def accounts_list(include_archived: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    stmt = select(FinanceAccount).order_by(FinanceAccount.currency, FinanceAccount.title)
    if not include_archived:
        stmt = stmt.where(FinanceAccount.status == "active")
    ents = {e.id: e for e in db.scalars(select(LegalEntity)).all()}
    return {"items": [acc.dto(db, a, ents) for a in db.scalars(stmt).all()], "can_edit": access.can_edit(user)}


@router.get("/accounts/options")
def account_options(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    return {"items": acc.own_banks(db)}


@router.post("/accounts", status_code=201)
def account_create(body: AccountIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    a = acc.create(db, user, body.legal_entity_id, body.bank_profile_id, body.title, body.currency, body.note)
    _commit(db)
    return acc.dto(db, a)


@router.post("/accounts/{aid}/archive")
def account_archive(aid: str, body: ReasonIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    a = acc.archive(db, user, _account(db, aid), body.reason)
    _commit(db)
    return acc.dto(db, a)


@router.get("/accounts/{aid}/balance-snapshots")
def snapshots(aid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    a = _account(db, aid)
    rows = db.scalars(select(FinanceBalanceSnapshot).where(FinanceBalanceSnapshot.account_id == a.id).order_by(FinanceBalanceSnapshot.as_of.desc())).all()
    return {"account": acc.dto(db, a), "items": [acc.snapshot_dto(x) for x in rows]}


@router.post("/accounts/{aid}/balance-snapshots", status_code=201)
def snapshot_add(aid: str, body: SnapshotIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                 db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    a = _account(db, aid)
    snap = acc.add_snapshot(db, user, a, body.as_of, _money(body.amount), body.source, body.external_ref, body.note, _key(idem))
    _commit(db)
    return {**acc.snapshot_dto(snap), "account": acc.dto(db, a)}


# ------------------------------------------------------------------ банковские операции и разнесение

class TransactionIn(BaseModel):
    account_id: Optional[str] = None
    direction: str = Field(pattern="^(in|out)$")
    amount: Decimal
    currency: str
    booked_at: datetime
    value_date: Optional[date] = None
    counterparty_name: str = ""
    purpose: str = ""
    external_ref: str = ""
    note: str = ""


class ReverseIn(BaseModel):
    reason: str
    booked_at: Optional[datetime] = None


class AllocItem(BaseModel):
    obligation_id: str
    amount_tx: Decimal
    amount_obligation: Optional[Decimal] = None
    fx_rate: Optional[Decimal] = None
    fx_on: Optional[date] = None
    fx_source: Optional[str] = None
    note: str = ""


class AllocIn(BaseModel):
    items: list[AllocItem]
    status: str = Field("provisional", pattern="^(provisional|confirmed)$")


def _tx_out(db: Session, vis: access.Visibility, tx: FinanceTransaction) -> dict:
    a = db.get(FinanceAccount, tx.account_id) if tx.account_id else None
    out = txs.tx_dto(tx, txs.allocated(db, [tx.id])[tx.id], a.title if a else "")
    allocs = db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == tx.id).order_by(FinanceAllocation.created_at)).all()
    obs = {o.id: o for o in db.scalars(select(FinanceObligation).where(FinanceObligation.id.in_([x.obligation_id for x in allocs] or ["-"]))).all()}
    shown = [x for x in allocs if obs.get(x.obligation_id) and vis.obligation(obs[x.obligation_id])]
    out["allocations"] = [txs.alloc_dto(x, ob=obs[x.obligation_id]) for x in shown]
    out["hidden_allocations"] = len(allocs) - len(shown)
    return out


@router.get("/transactions")
def transactions_list(account_id: Optional[str] = None, status: Optional[str] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"),
                      currency: Optional[str] = None, unallocated: bool = False, date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "",
                      limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                      db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    stmt = select(FinanceTransaction)
    for col, val in ((FinanceTransaction.account_id, account_id), (FinanceTransaction.status, status), (FinanceTransaction.direction, direction),
                     (FinanceTransaction.currency, currency.upper() if currency else None)):
        if val:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(FinanceTransaction.booked_at >= datetime.combine(date_from, datetime.min.time()).astimezone())
    if date_to:
        stmt = stmt.where(FinanceTransaction.booked_at <= datetime.combine(date_to, datetime.max.time()).astimezone())
    rows = db.scalars(stmt.order_by(FinanceTransaction.booked_at.desc(), FinanceTransaction.created_at.desc())).all()
    if search.strip():
        q = search.strip().lower()
        rows = [t for t in rows if q in " ".join([t.counterparty_name, t.purpose, t.external_ref, t.note]).lower()]
    used = txs.allocated(db, [t.id for t in rows])
    titles = {a.id: a.title for a in db.scalars(select(FinanceAccount)).all()}
    items = [txs.tx_dto(t, used[t.id], titles.get(t.account_id or "", "")) for t in rows]
    if unallocated:
        items = [x for x in items if Decimal(x["unallocated"]) > 0]
    return {"total": len(items), "limit": limit, "offset": offset, "items": items[offset:offset + limit], "can_edit": access.can_edit(user)}


@router.get("/transactions/{tid}")
def transaction_card(tid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    tx = db.get(FinanceTransaction, tid)
    if tx is None:
        raise HTTPException(404, "Операция не найдена")
    return _tx_out(db, access.Visibility(db, user), tx)


@router.post("/transactions", status_code=201)
def transaction_create(body: TransactionIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                       db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ручная запись: draft (со счётом) или «требует сверки» (без счёта). Проведение — отдельным действием."""
    access.require_edit(user)
    tx, _ = txs.create(db, user, account_id=body.account_id, direction=body.direction, amount=_money(body.amount), currency=_ccy(body.currency),
                       booked_at=body.booked_at, value_date=body.value_date, counterparty_name=body.counterparty_name, purpose=body.purpose,
                       external_ref=body.external_ref, note=body.note, key=_key(idem, required=False))
    _commit(db)
    return _tx_out(db, access.Visibility(db, user), tx)


@router.post("/transactions/{tid}/post")
def transaction_post(tid: str, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                     db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    tx = txs.post(db, user, tid, _key(idem))
    _commit(db)
    return _tx_out(db, access.Visibility(db, user), tx)


@router.post("/transactions/{tid}/reverse")
def transaction_reverse(tid: str, body: ReverseIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                        db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    rev = txs.reverse(db, user, tid, body.reason, body.booked_at, _key(idem))
    _commit(db)
    vis = access.Visibility(db, user)
    return {"reversal": _tx_out(db, vis, rev), "original": _tx_out(db, vis, db.get(FinanceTransaction, tid))}


@router.post("/transactions/{tid}/allocations", status_code=201)
def transaction_allocate(tid: str, body: AllocIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                         db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    vis = access.Visibility(db, user)
    items = [i.model_dump() for i in body.items]
    txs.allocate(db, user, vis, tid, items, body.status, _key(idem))
    _commit(db)
    return _tx_out(db, vis, db.get(FinanceTransaction, tid))


@router.post("/allocations/{alid}/confirm")
def allocation_confirm(alid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    vis = access.Visibility(db, user)
    al = txs.confirm_allocation(db, user, vis, alid)
    _commit(db)
    return txs.alloc_dto(al)


@router.post("/allocations/{alid}/reverse")
def allocation_reverse(alid: str, body: ReasonIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    vis = access.Visibility(db, user)
    al = txs.reverse_allocation(db, user, vis, alid, body.reason)
    _commit(db)
    return txs.alloc_dto(al)
