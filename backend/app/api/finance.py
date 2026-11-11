"""Раздел «Финансы», вариант Б: тонкий роутер /api/finance (docs/finance/04_API_и_права.md).

Бизнес-логика — в services/finance. Каждый endpoint проверяет право на функцию и видимость объекта.
Деньги в JSON — строки с двумя знаками. Mutation банковского факта требуют заголовок Idempotency-Key.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import AuditLog, FinanceAccount, FinanceAllocation, FinanceBalanceSnapshot, FinanceObligation, FinanceTransaction, LegalEntity
from ..services.finance import access, accounts as acc, obligations as obl, overview as ov, transactions as txs
from ..services.finance import imports as imp_fields
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
            "tabs": ["overview", "calendar", "debt", "liquidity"] + (["economy"] if (user.can("finance_analytics", 1) and user.can("cost", 1)) else []) + ["exposure", "reconciliation"], "import_fields": imp_fields.FIELD_TITLES, "statuses": obl.STATUS_TITLES}


@router.get("/overview")
def overview(as_of: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    return ov.build(db, access.Visibility(db, user), as_of or date.today())


# ------------------------------------------------------------------ обязательства

@router.get("/obligations")
def obligations(as_of: Optional[date] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"), currency: Optional[str] = None,
                legal_entity_id: Optional[str] = None, deal_id: Optional[str] = None, contract_id: Optional[str] = None, status: Optional[str] = None,
                source_type: Optional[str] = Query(None, pattern="^(contract|purchase_order|batch|calculation|penalty)$"),
                date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "", include_inactive: bool = False, reconciliation: bool = False,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    f = obl.ObligationFilter(as_of=as_of or date.today(), direction=direction, currency=currency.upper() if currency else None, legal_entity_id=legal_entity_id,
                             deal_id=deal_id, contract_id=contract_id, source_type=source_type, status=status, date_from=date_from, date_to=date_to, search=search.strip(),
                             include_inactive=include_inactive, reconciliation=reconciliation, limit=limit, offset=offset)
    return obl.query(db, access.Visibility(db, user), f)


def _obligation(db: Session, user: CurrentUser, oid: str) -> tuple[FinanceObligation, access.Visibility]:
    vis = access.Visibility(db, user)
    o = db.get(FinanceObligation, oid)
    if o is None or not vis.obligation(o):
        raise HTTPException(404, "Обязательство не найдено")
    return o, vis


@router.get("/undated")
def undated(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ожидаемые суммы без согласованной даты (запчасти): отдельный блок, не входит в датированный календарь (D-018, D-124)."""
    access.require_read(user)
    from ..services.finance.sources import parts_undated
    return {"parts": parts_undated(db, user)}


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
    # dry-run: вся пересборка (обязательства → признание → старые отметки) выполняется целиком и откатывается одним rollback,
    # чтобы отчёт пробного прогона совпадал с настоящей сборкой (связь старой отметки с только что собранной строкой — D-147)
    rep = obl.rebuild(db, user.id, dry_run=False)
    rep["dry_run"] = dry_run
    from ..services.finance import recognition as rec   # старые факты по статьям план/факта → события признания (один раз)
    rep["recognition_migrated"] = rec.migrate_planfact(db, user.id)
    from ..services.finance import legacy as leg   # отметки оплаты, сделанные до включения раздела → «требует сверки» (D-147)
    rep["legacy_marks"] = leg.inventory(db, user.id)
    if not dry_run:
        _commit(db)
    else:
        db.rollback()
    return rep


# ------------------------------------------------------------------ ликвидность

@router.get("/liquidity")
def liquidity(as_of: Optional[date] = None, horizon_days: int = Query(90, ge=7, le=730), group_by: str = Query("week", pattern="^(day|week)$"),
              currency: Optional[str] = None, legal_entity_id: Optional[str] = None, account_id: Optional[str] = None, threshold: Decimal = Decimal(0),
              exclude_intragroup: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Прогноз остатков по валютам от подтверждённого остатка; неизвестный остаток — null, не ноль.
    exclude_intragroup — сводный прогноз группы без платежей между компаниями группы (D-163)."""
    access.require_read(user)
    from ..services.finance import liquidity as liq
    return liq.build(db, access.Visibility(db, user), as_of or date.today(), horizon_days, group_by, currency, legal_entity_id, account_id, _money(threshold, "Порог"),
                     exclude_intragroup=exclude_intragroup)


@router.get("/liquidity/explanation")
def liquidity_explanation(currency: str, on: date, as_of: Optional[date] = None, group_by: str = Query("week", pattern="^(day|week)$"),
                          horizon_days: int = Query(90, ge=7, le=730), exclude_intragroup: bool = False,
                          db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..services.finance import liquidity as liq
    return liq.explain_point(db, access.Visibility(db, user), as_of or date.today(), _ccy(currency), on, group_by, horizon_days, exclude_intragroup)


@router.get("/intragroup")
def intragroup(as_of: Optional[date] = None, currency: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Внутригрупповые расчёты: кто из компаний группы кому должен — по парам и валютам, сальдо пар (D-162)."""
    access.require_read(user)
    from ..services.finance import intragroup as ig
    return ig.build(db, access.Visibility(db, user), as_of or date.today(), _ccy(currency) if currency else None)


# ------------------------------------------------------------------ импорт выписки

@router.post("/imports/preview", status_code=201)
async def import_preview(account_id: str = Form(...), file: UploadFile = File(...), mapping: str = Form(""),
                         db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Разбор CSV/XLSX: строки, ошибки, дубли, итоги. Ничего не проводит."""
    access.require_edit(user)
    import json
    from ..services.finance import imports as imp
    try:
        m = json.loads(mapping) if mapping.strip() else None
    except ValueError:
        raise HTTPException(422, "Сопоставление колонок — JSON")
    b = imp.preview(db, user, _account(db, account_id), file.filename or "", await file.read(), m)
    _commit(db)
    return imp.dto(b)


class ImportConfirmIn(BaseModel):
    rows: Optional[list[int]] = None   # номера строк файла; пусто — все валидные


def _import(db: Session, iid: str):
    from ..models import FinanceImportBatch
    b = db.get(FinanceImportBatch, iid)
    if b is None:
        raise HTTPException(404, "Импорт не найден")
    return b


@router.get("/imports/{iid}")
def import_get(iid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    from ..services.finance import imports as imp
    return imp.dto(_import(db, iid))


@router.post("/imports/{iid}/confirm")
def import_confirm(iid: str, body: ImportConfirmIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                   db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    from ..services.finance import imports as imp
    b = imp.confirm(db, user, _import(db, iid), body.rows, _key(idem))
    _commit(db)
    return imp.dto(b)


# ------------------------------------------------------------------ конвертация валют

class FxIn(BaseModel):
    sell_account_id: str
    buy_account_id: str
    sell_amount: Decimal
    buy_amount: Decimal
    rate: Decimal                       # единиц проданной валюты за 1 единицу купленной
    rate_source: str = ""
    executed_at: datetime
    external_ref: str = ""
    comment: str = ""
    sell_transaction_id: Optional[str] = None   # связать с уже проведёнными операциями (например, из выписки)
    buy_transaction_id: Optional[str] = None
    fee_amount: Optional[Decimal] = None        # комиссия банка — отдельное списание со счёта продажи


@router.get("/fx-operations")
def fx_list(account_id: Optional[str] = None, currency: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..models import FinanceFxOperation
    from ..services.finance import fx
    stmt = select(FinanceFxOperation).order_by(FinanceFxOperation.executed_at.desc())
    rows = [o for o in db.scalars(stmt).all() if (not account_id or account_id in (o.sell_account_id, o.buy_account_id))
            and (not currency or currency.upper() in (o.sell_currency, o.buy_currency))]
    return {"items": [fx.dto(db, o) for o in rows], "can_edit": access.can_edit(user)}


@router.post("/fx-operations", status_code=201)
def fx_create(body: FxIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Конвертация: две ноги (списание проданной, зачисление купленной валюты) — новые или уже проведённые; не выручка и не расход."""
    access.require_edit(user)
    from ..services.finance import fx
    from ..services.finance.money import rate as to_rate
    try:
        r = to_rate(body.rate)
    except ValueError as e:
        raise HTTPException(422, str(e))
    op = fx.create(db, user, sell_account_id=body.sell_account_id, buy_account_id=body.buy_account_id, sell_amount=_money(body.sell_amount), buy_amount=_money(body.buy_amount),
                   rate=r, rate_source=body.rate_source, executed_at=body.executed_at, external_ref=body.external_ref, comment=body.comment, key=_key(idem),
                   sell_transaction_id=body.sell_transaction_id, buy_transaction_id=body.buy_transaction_id,
                   fee_amount=_money(body.fee_amount, "Комиссия") if body.fee_amount not in (None, "") else None)
    _commit(db)
    return fx.dto(db, op)


@router.post("/fx-operations/{fid}/reverse")
def fx_reverse(fid: str, body: ReasonIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_edit(user)
    from ..services.finance import fx
    op = fx.reverse(db, user, fid, body.reason, _key(idem))
    _commit(db)
    return fx.dto(db, op)


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
    from ..services.finance.fx import leg_ids
    out = txs.tx_dto(tx, txs.allocated(db, [tx.id])[tx.id], a.title if a else "", tx.id in leg_ids(db, [tx.id]))
    allocs = db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == tx.id).order_by(FinanceAllocation.created_at)).all()
    obs = {o.id: o for o in db.scalars(select(FinanceObligation).where(FinanceObligation.id.in_([x.obligation_id for x in allocs] or ["-"]))).all()}
    shown = [x for x in allocs if obs.get(x.obligation_id) and vis.obligation(obs[x.obligation_id])]
    out["allocations"] = [txs.alloc_dto(x, ob=obs[x.obligation_id]) for x in shown]
    out["hidden_allocations"] = len(allocs) - len(shown)
    return out


@router.get("/transactions")
def transactions_list(account_id: Optional[str] = None, status: Optional[str] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"),
                      currency: Optional[str] = None, unallocated: bool = False, date_from: Optional[date] = None, date_to: Optional[date] = None, search: str = "",
                      include_withdrawn: bool = False, origin: Optional[str] = Query(None, pattern="^(manual|legacy|reversal)$"),
                      limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                      db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    stmt = select(FinanceTransaction)
    for col, val in ((FinanceTransaction.account_id, account_id), (FinanceTransaction.status, status), (FinanceTransaction.direction, direction),
                     (FinanceTransaction.currency, currency.upper() if currency else None)):
        if val:
            stmt = stmt.where(col == val)
    if not include_withdrawn:
        stmt = stmt.where(FinanceTransaction.withdrawn_at.is_(None))
    if origin == "legacy":
        stmt = stmt.where(FinanceTransaction.origin_type.like("%_legacy"))
    elif origin:
        stmt = stmt.where(FinanceTransaction.origin_type == origin)
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
    from ..services.finance.fx import leg_ids
    legs = leg_ids(db, [t.id for t in rows])
    items = [txs.tx_dto(t, used[t.id], titles.get(t.account_id or "", ""), t.id in legs) for t in rows]
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


class AssignIn(BaseModel):
    account_id: str
    external_ref: Optional[str] = None


@router.post("/transactions/{tid}/assign-account")
def transaction_assign_account(tid: str, body: AssignIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Сверка: указать реальный счёт (и банковский номер) для записи «требует сверки». Проведение — отдельно."""
    access.require_edit(user)
    tx = txs.assign_account(db, user, tid, body.account_id, body.external_ref)
    _commit(db)
    return _tx_out(db, access.Visibility(db, user), tx)


@router.post("/transactions/{tid}/withdraw")
def transaction_withdraw(tid: str, body: ReasonIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отозвать непроведённую запись (дубль, не платёж). Запись и история остаются."""
    access.require_edit(user)
    tx = txs.withdraw(db, user, tid, body.reason)
    _commit(db)
    return _tx_out(db, access.Visibility(db, user), tx)


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


# ------------------------------------------------------------------ экономика сделок и признание (этап 5)

def _require_economy(user: CurrentUser) -> None:
    from ..core.roles import READ
    if not (user.can("finance_analytics", READ) and user.can("cost", READ)):
        raise HTTPException(403, "Экономика сделки: нужны права «Финансовая аналитика» и «Себестоимость»")


@router.get("/economy/deals")
def economy_deals(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    _require_economy(user)
    from ..services.finance import economy
    return {"deals": economy.list_deals(db, user, access.Visibility(db, user))}


@router.get("/deals/{deal_id}/economy")
def deal_economy(deal_id: str, as_of: Optional[date] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """План / текущий прогноз / факт P&L / факт cash по сделке. Раздел «Финансы» не обязателен: тот же отчёт открывается из карточки сделки."""
    _require_economy(user)
    from .common import get_deal_or_404
    from ..services.finance import economy
    deal = get_deal_or_404(db, deal_id, user)
    return economy.build(db, access.Visibility(db, user), deal, as_of or date.today())


@router.get("/deals/{deal_id}/economy/explanation")
def deal_economy_explanation(deal_id: str, metric: str, scenario: str = "base", cost_item_code: Optional[str] = None,
                             db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Трассировка показателя экономики: формула, значение, включённые/исключённые строки, курсы, версия, ссылки (D-144)."""
    _require_economy(user)
    from .common import get_deal_or_404
    from ..services.finance import economy
    deal = get_deal_or_404(db, deal_id, user)
    return economy.explain(db, access.Visibility(db, user), deal, metric, scenario, cost_item_code)


class RecognitionIn(BaseModel):
    kind: str = Field(pattern="^(revenue|expense)$")
    amount: Decimal
    currency: str = "RUB"
    fx_rate: Optional[Decimal] = None
    fx_on: Optional[date] = None
    fx_source: str = ""
    recognized_on: Optional[date] = None
    cost_item_code: Optional[str] = None
    position_id: Optional[str] = None
    obligation_id: Optional[str] = None
    basis_type: str = "primary_document"
    document_type: str = ""
    document_number: str = ""
    document_date: Optional[date] = None
    attachment_id: Optional[str] = None
    comment: str = ""


deal_router = APIRouter(prefix="/api/deals", tags=["finance"])


@deal_router.get("/{deal_id}/finance/recognition-events")
def recognition_list(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from .common import get_deal_or_404
    from ..services.finance import recognition as rec
    deal = get_deal_or_404(db, deal_id, user)
    return {"events": rec.list_for(db, user, deal), "all": rec.can_view_all(user)}


@deal_router.post("/{deal_id}/finance/recognition-events", status_code=201)
def recognition_create(deal_id: str, body: RecognitionIn, idem: Optional[str] = Header(None, alias="Idempotency-Key"),
                       db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Признание выручки (ФД/ЗГД) или расхода по статье (по правам ввода план/факта). Деньги не двигает."""
    from .common import get_deal_or_404
    from ..services.finance import recognition as rec
    deal = get_deal_or_404(db, deal_id, user)
    key = _key(idem)
    dup = db.scalar(select(rec.FinanceRecognitionEvent).where(rec.FinanceRecognitionEvent.idempotency_key == key))
    if dup is not None:
        if dup.deal_id != deal.id:
            raise HTTPException(409, "Idempotency-Key уже использован для другой сделки")
        return rec.dto(dup)
    rec.check_create(user, deal, body.kind, body.cost_item_code)
    ev = rec.create(db, user, deal, body.model_dump(), key)
    _commit(db)
    return rec.dto(ev)


@router.post("/recognition-events/{eid}/reverse")
def recognition_reverse(eid: str, body: ReasonIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from .common import can_see_deal
    from ..models import Deal, FinanceRecognitionEvent
    from ..services.finance import recognition as rec
    ev = db.get(FinanceRecognitionEvent, eid)
    deal = db.get(Deal, ev.deal_id) if ev and ev.deal_id else None
    if ev is None or deal is None or not can_see_deal(user, deal) or not (rec.can_view_all(user) or ev.created_by == user.id):
        raise HTTPException(404, "Событие не найдено")
    rec.check_reverse(user, deal, ev)
    rec.reverse(db, user, ev, body.reason)
    _commit(db)
    return rec.dto(ev)


# ------------------------------------------------------------------ обеспечение и неустойки (этап 5)

@router.get("/debt")
def debt(as_of: Optional[date] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"), group_by: str = Query("counterparty", pattern="^(counterparty|deal)$"),
         currency: Optional[str] = None, legal_entity_id: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Дебиторка и кредиторка: остатки по валютам, ageing от юридического срока, группы, отдельно — без согласованной даты (D-143)."""
    access.require_read(user)
    from ..services.finance import debt as dbt
    return dbt.build(db, user, access.Visibility(db, user), as_of or date.today(), direction, group_by, _ccy(currency) if currency else None, legal_entity_id)


@router.get("/legal-exposure")
def legal_exposure(deal_id: Optional[str] = None, contract_id: Optional[str] = None, direction: Optional[str] = Query(None, pattern="^(receivable|payable)$"),
                   status: Optional[str] = None, kind: Optional[str] = Query(None, pattern="^(security|penalty)$"), as_of: Optional[date] = None,
                   db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Обеспечение и неустойки из юридического блока — только чтение; начисляемые суммы отделены от предъявленных/признанных и от прогноза."""
    access.require_read(user)
    from ..services.finance import exposure
    vis = access.Visibility(db, user)
    if deal_id and not vis.deal(deal_id):
        raise HTTPException(404, "Сделка не найдена")
    if contract_id and not vis.contract(contract_id):
        raise HTTPException(404, "Договор не найден")
    return exposure.build(db, user, vis, as_of or date.today(), deal_id, contract_id, direction, status, kind)


# ------------------------------------------------------------------ сверка и уточнения (этап 6)

@router.get("/reconciliation")
def reconciliation(queue: Optional[str] = None, deal_id: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..services.finance import reconciliation as rec
    if queue and queue not in rec.QUEUES:
        raise HTTPException(422, "Очередь: " + ", ".join(rec.QUEUES))
    vis = access.Visibility(db, user)
    if deal_id and not vis.deal(deal_id):
        raise HTTPException(404, "Сделка не найдена")
    return rec.build(db, user, vis, date.today(), queue, deal_id)


class QuestionIn(BaseModel):
    subject_type: str
    subject_id: str
    assignee_id: str
    question: str
    due_on: Optional[date] = None


class AnswerIn(BaseModel):
    answer: str = ""


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(verified|rejected)$")
    review_comment: str = ""
    source_update_ref: str = ""


def _question(db: Session, qid: str):
    from ..models import FinanceQuestion
    q = db.get(FinanceQuestion, qid)
    if q is None:
        raise HTTPException(404, "Вопрос не найден")
    return q


def _can_see_full(db: Session, user: CurrentUser, q) -> bool:
    return access.can_read(user) and access.Visibility(db, user).deal(q.deal_id)


@router.get("/questions/my")
def questions_my(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Вопросы, назначенные пользователю (и замещаемым). Права на раздел не нужно — только свой ограниченный контекст."""
    from ..models import FinanceQuestion
    from ..services.finance import questions as qs
    rows = db.scalars(select(FinanceQuestion).where(FinanceQuestion.assignee_id.in_({user.id, *user.acting_for})).order_by(FinanceQuestion.created_at.desc())).all()
    return {"items": [qs.scoped_dto(db, q) for q in rows]}


@router.get("/questions/assignees")
def question_assignees(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..models import User
    return {"items": [{"id": u.id, "name": u.display_name, "roles": list(u.roles or [])} for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name)).all()]}


@router.get("/questions/context")
def question_context(subject_type: str, subject_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Что увидит ответственный — показывается перед отправкой (05, раздел 13)."""
    access.require_read(user)
    from ..services.finance import questions as qs
    sub = qs.subject(db, user, access.Visibility(db, user), subject_type, subject_id)
    return {"title": sub["title"], "fields": {k: v for k, v in sub["context"].items() if v not in (None, "")}}


@router.get("/questions")
def questions_list(status: Optional[str] = None, assignee_id: Optional[str] = None, deal_id: Optional[str] = None, subject_type: Optional[str] = None,
                   subject_id: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..models import FinanceQuestion
    from ..services.finance import questions as qs
    q = select(FinanceQuestion).order_by(FinanceQuestion.created_at.desc())
    for col, val in ((FinanceQuestion.status, status), (FinanceQuestion.assignee_id, assignee_id), (FinanceQuestion.deal_id, deal_id),
                     (FinanceQuestion.subject_type, subject_type), (FinanceQuestion.subject_id, subject_id)):
        if val:
            q = q.where(col == val)
    vis = access.Visibility(db, user)
    return {"items": [qs.full_dto(db, user, vis, x) for x in db.scalars(q.limit(500)).all() if vis.deal(x.deal_id)], "statuses": qs.STATUS}


@router.post("/questions", status_code=201)
def question_create(body: QuestionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    access.require_read(user)
    from ..services.finance import questions as qs
    vis = access.Visibility(db, user)
    q = qs.create(db, user, vis, body.model_dump())
    _commit(db)
    return qs.full_dto(db, user, vis, q)


@router.get("/questions/{qid}")
def question_get(qid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..services.finance import questions as qs
    q = _question(db, qid)
    if _can_see_full(db, user, q):
        return qs.full_dto(db, user, access.Visibility(db, user), q)
    if qs.is_assignee(user, q):
        return qs.scoped_dto(db, q)
    raise HTTPException(404, "Вопрос не найден")


@router.post("/questions/{qid}/answer")
def question_answer(qid: str, body: AnswerIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..services.finance import questions as qs
    q = qs.answer(db, user, _question(db, qid), body.answer)
    _commit(db)
    return qs.scoped_dto(db, q)


@router.post("/questions/{qid}/files", status_code=201)
async def question_file(qid: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Файл к ответу: загружает ответственный, пока ответ не дан (или отклонён)."""
    import os
    import uuid as _uuid
    from datetime import timezone as _tz
    from ..core.config import get_settings
    from ..core.files import read_limited
    from ..services.finance import questions as qs
    q = _question(db, qid)
    if not qs.is_assignee(user, q):
        raise HTTPException(404, "Вопрос не найден")
    if q.status not in ("open", "rejected"):
        raise HTTPException(409, "Ответ уже дан — файл добавить нельзя")
    name = os.path.basename(file.filename or "file")
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".xls", ".csv", ".docx", ".doc", ".zip"):
        raise HTTPException(422, "Файл: PDF, JPG, PNG, XLSX, XLS, CSV, DOCX, DOC или ZIP")
    data = read_limited(file)
    d = os.path.join(get_settings().storage_local_root, "_finance", "questions", q.id)
    os.makedirs(d, exist_ok=True)
    fid = _uuid.uuid4().hex
    with open(os.path.join(d, f"{fid}{ext}"), "wb") as f:
        f.write(data)
    q.files = [*(q.files or []), {"id": fid, "name": name[:200], "size": len(data), "uploaded_by": user.id, "uploaded_at": datetime.now(_tz.utc).isoformat()}]
    from ..services.deal_service import audit as _audit
    _audit(db, user.id, q.deal_id, "finance_question", q.id, "file", after={"name": name[:200], "size": len(data)})
    _commit(db)
    return qs.scoped_dto(db, q)


@router.get("/questions/{qid}/files/{fid}")
def question_file_get(qid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    import os
    from ..core.config import get_settings
    from ..core.files import file_response
    from ..services.finance import questions as qs
    q = _question(db, qid)
    if not (_can_see_full(db, user, q) or qs.is_assignee(user, q)):
        raise HTTPException(404, "Вопрос не найден")
    f = next((x for x in (q.files or []) if x["id"] == fid), None)
    d = os.path.join(get_settings().storage_local_root, "_finance", "questions", q.id)
    path = next((os.path.join(d, x) for x in (os.listdir(d) if os.path.isdir(d) else []) if x.startswith(fid)), None) if f else None
    if not path:
        raise HTTPException(404, "Файл не найден")
    return file_response(path, f["name"], inline=False)


@router.post("/questions/{qid}/review")
def question_review(qid: str, body: ReviewIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Проверка ответа ФД/ЗГД. «Данные исправлены» — только со ссылкой на фактическое изменение источника (source_update_ref)."""
    access.require_edit(user)
    from ..services.finance import questions as qs
    q = _question(db, qid)
    if not _can_see_full(db, user, q):
        raise HTTPException(404, "Вопрос не найден")
    qs.review(db, user, q, body.decision, body.review_comment, body.source_update_ref)
    _commit(db)
    return qs.full_dto(db, user, access.Visibility(db, user), q)


@router.post("/questions/{qid}/close")
def question_close(qid: str, body: ReasonIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..services.finance import questions as qs
    q = _question(db, qid)
    if not _can_see_full(db, user, q):
        raise HTTPException(404, "Вопрос не найден")
    if not (q.asked_by == user.id or access.can_edit(user)):
        raise HTTPException(403, "Закрыть вопрос может автор, ФД или ЗГД")
    qs.close(db, user, q, body.reason)
    _commit(db)
    return qs.full_dto(db, user, access.Visibility(db, user), q)


# ------------------------------------------------------------------ экспорт XLSX (этап 6)

@router.get("/export")
def export_xlsx(kind: str = Query(..., pattern="^(obligations|debt|liquidity|economy|reconciliation)$"), format: str = Query("xlsx", pattern="^xlsx$"),
                as_of: Optional[date] = None, direction: Optional[str] = Query(None, pattern="^(in|out)$"), currency: Optional[str] = None,
                legal_entity_id: Optional[str] = None, deal_id: Optional[str] = None, contract_id: Optional[str] = None, status: Optional[str] = None,
                source_type: Optional[str] = Query(None, pattern="^(contract|purchase_order|batch|calculation|penalty)$"), date_from: Optional[date] = None,
                date_to: Optional[date] = None, search: str = "", include_inactive: bool = False, reconciliation: bool = False,
                group_by: Optional[str] = Query(None, pattern="^(counterparty|deal|day|week)$"), horizon_days: int = Query(90, ge=7, le=730), queue: Optional[str] = None,
                db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """XLSX из тех же read-сервисов и с теми же правами, что и экран (D-145). Клиент передаёт только фильтры; выгрузка записывается в журнал."""
    from urllib.parse import quote
    from fastapi.responses import Response
    from ..services.deal_service import audit as _audit
    from ..services.finance import export as ex
    if kind == "economy":
        _require_economy(user)
        access.require_read(user)
    else:
        access.require_read(user)
    vis = access.Visibility(db, user)
    if deal_id and not vis.deal(deal_id):
        raise HTTPException(404, "Сделка не найдена")
    params = {"as_of": as_of or date.today(), "direction": direction, "currency": _ccy(currency) if currency else None, "legal_entity_id": legal_entity_id,
              "deal_id": deal_id, "contract_id": contract_id, "status": status, "source_type": source_type, "date_from": date_from, "date_to": date_to,
              "search": search.strip(), "include_inactive": include_inactive, "reconciliation": reconciliation, "horizon_days": horizon_days, "queue": queue,
              "group_by": group_by if kind != "liquidity" or group_by in ("day", "week") else "week"}
    if kind == "debt" and group_by not in (None, "counterparty", "deal"):
        raise HTTPException(422, "group_by для задолженности: counterparty | deal")
    data, name, n, did = ex.build(db, user, vis, kind, params)
    from ..core.download_audit import note   # журнал выгрузок: число строк и фильтры по белому списку (04 §7.2)
    note(entity_type="finance_" + kind, row_count=n, deal_id=did or deal_id,
         filters={k: (v.isoformat() if isinstance(v, date) else v) for k, v in params.items() if v not in (None, "", False)})
    _audit(db, user.id, did or deal_id, "finance_export", kind, "export",
           after={"kind": kind, "rows": n, "filters": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in params.items() if v not in (None, "", False)}})
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
