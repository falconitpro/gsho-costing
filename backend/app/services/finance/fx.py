"""Двухногая конвертация валют (docs/finance/02, раздел 4.8; 01, раздел 8; D-025, D-131).

- нога продажи — проведённое списание со счёта в проданной валюте; нога покупки — проведённое зачисление на счёт в купленной;
- ноги создаются вместе с конвертацией (origin_type=fx) или связываются с уже проведёнными операциями (например, из выписки);
- курс — единиц проданной валюты за 1 единицу купленной; суммы согласуются с курсом;
- комиссия банка — отдельное проведённое списание (origin_type=fx_fee), не обязательство; признание расхода — этап 5;
- конвертация не создаёт выручку/расход и не разносится на обязательства; одна нога — не более чем в одной конвертации;
- исправление — сторно конвертации: сторнируются обе ноги (и комиссия), записи не удаляются.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...models import FinanceAccount, FinanceAllocation, FinanceFxOperation, FinanceTransaction
from ..deal_service import audit
from . import transactions as txs
from .money import TOLERANCE, s


def leg_ids(db: Session, tx_ids: Optional[list[str]] = None) -> set[str]:
    """Операции, являющиеся ногами или комиссией конвертации: на обязательства они не разносятся и «неразнесёнными» не считаются."""
    stmt = select(FinanceFxOperation.sell_transaction_id, FinanceFxOperation.buy_transaction_id, FinanceFxOperation.fee_transaction_id)
    if tx_ids is not None:
        if not tx_ids:
            return set()
        stmt = stmt.where(or_(FinanceFxOperation.sell_transaction_id.in_(tx_ids), FinanceFxOperation.buy_transaction_id.in_(tx_ids),
                              FinanceFxOperation.fee_transaction_id.in_(tx_ids)))
    out: set[str] = set()
    for row in db.execute(stmt).all():
        out.update(x for x in row if x)
    return out


def _active_account(db: Session, aid: str, what: str) -> FinanceAccount:
    a = db.get(FinanceAccount, aid) if aid else None
    if a is None or a.status != "active":
        raise HTTPException(422, f"{what}: выберите действующий финансовый счёт")
    return a


def _leg(db: Session, tid: str, direction: str, account: FinanceAccount, amount: Decimal, what: str) -> FinanceTransaction:
    tx = db.scalar(select(FinanceTransaction).where(FinanceTransaction.id == tid).with_for_update())
    if tx is None:
        raise HTTPException(404, f"{what}: операция не найдена")
    if tx.status != "posted" or tx.origin_type == "reversal" or tx.withdrawn_at:
        raise HTTPException(409, f"{what}: нужна проведённая, не сторнированная операция")
    if tx.direction != direction:
        raise HTTPException(422, f"{what}: нужна операция «{'списание' if direction == 'out' else 'поступление'}»")
    if tx.account_id != account.id or tx.currency != account.currency:
        raise HTTPException(422, f"{what}: операция другого счёта или валюты")
    if tx.amount != amount:
        raise HTTPException(422, f"{what}: сумма операции {s(tx.amount)} ≠ {s(amount)}")
    if db.scalar(select(FinanceAllocation.id).where(FinanceAllocation.transaction_id == tx.id, FinanceAllocation.status.in_(("provisional", "confirmed")))):
        raise HTTPException(409, f"{what}: операция уже разнесена на обязательства — сначала отмените разнесение")
    if tx.id in leg_ids(db, [tx.id]):
        raise HTTPException(409, f"{what}: операция уже входит в другую конвертацию")
    return tx


def _new_leg(db: Session, user, account: FinanceAccount, direction: str, amount: Decimal, at: datetime, ref: str, purpose: str, key: str, origin: str) -> FinanceTransaction:
    tx = FinanceTransaction(account_id=account.id, direction=direction, amount=amount, currency=account.currency, booked_at=at, purpose=purpose[:1000],
                            external_ref=ref[:128], status="posted", origin_type=origin, idempotency_key=key, created_by=user.id, posted_by=user.id,
                            posted_at=datetime.now(timezone.utc), counterparty_name="конвертация валют")
    db.add(tx)
    db.flush()
    audit(db, user.id, None, "finance_transaction", tx.id, "create", after={"origin": origin, "account_id": account.id, "direction": direction, "amount": s(amount),
                                                                           "currency": account.currency, "idempotency_key": key})
    return tx


def create(db: Session, user, *, sell_account_id: str, buy_account_id: str, sell_amount: Decimal, buy_amount: Decimal, rate: Decimal, rate_source: str,
           executed_at: datetime, external_ref: str, comment: str, key: str, sell_transaction_id: Optional[str] = None,
           buy_transaction_id: Optional[str] = None, fee_amount: Optional[Decimal] = None) -> FinanceFxOperation:
    dup = db.scalar(select(FinanceFxOperation).where(FinanceFxOperation.idempotency_key == key))
    if dup is not None:
        return dup
    if executed_at.tzinfo is None:
        raise HTTPException(422, "Укажите дату и время конвертации с часовым поясом")
    sa, ba = _active_account(db, sell_account_id, "Счёт продажи"), _active_account(db, buy_account_id, "Счёт покупки")
    if sa.id == ba.id or sa.currency == ba.currency:
        raise HTTPException(422, "Конвертация — между счетами в разных валютах")
    if sell_amount <= 0 or buy_amount <= 0 or rate <= 0:
        raise HTTPException(422, "Суммы и курс положительные")
    if abs(buy_amount * rate - sell_amount) > TOLERANCE * max(Decimal(1), rate):
        raise HTTPException(422, f"Суммы не согласуются с курсом: {s(buy_amount)} {ba.currency} × {rate} ≠ {s(sell_amount)} {sa.currency}")
    if fee_amount is not None and fee_amount <= 0:
        raise HTTPException(422, "Комиссия — положительная сумма или пусто")
    ref = (external_ref or "").strip()
    sell = (_leg(db, sell_transaction_id, "out", sa, sell_amount, "Нога продажи") if sell_transaction_id
            else _new_leg(db, user, sa, "out", sell_amount, executed_at, ref, f"Продажа {sa.currency} → покупка {ba.currency} по курсу {rate}", f"{key}:sell", "fx"))
    buy = (_leg(db, buy_transaction_id, "in", ba, buy_amount, "Нога покупки") if buy_transaction_id
           else _new_leg(db, user, ba, "in", buy_amount, executed_at, ref, f"Покупка {ba.currency} за {sa.currency} по курсу {rate}", f"{key}:buy", "fx"))
    fee = _new_leg(db, user, sa, "out", fee_amount, executed_at, f"{ref}/комиссия" if ref else "", f"Комиссия банка за конвертацию {sa.currency} → {ba.currency}", f"{key}:fee", "fx_fee") if fee_amount else None
    op = FinanceFxOperation(sell_transaction_id=sell.id, buy_transaction_id=buy.id, fee_transaction_id=fee.id if fee else None, sell_account_id=sa.id, buy_account_id=ba.id,
                            sell_amount=sell_amount, sell_currency=sa.currency, buy_amount=buy_amount, buy_currency=ba.currency, rate=rate,
                            rate_source=(rate_source or "")[:64], executed_at=executed_at, external_ref=ref[:128], idempotency_key=key, comment=comment or "", created_by=user.id)
    db.add(op)
    db.flush()
    audit(db, user.id, None, "finance_fx_operation", op.id, "create", after={"sell": f"{s(sell_amount)} {sa.currency}", "buy": f"{s(buy_amount)} {ba.currency}", "rate": str(rate),
                                                                            "sell_tx": sell.id, "buy_tx": buy.id, "fee_tx": fee.id if fee else None, "idempotency_key": key})
    return op


def reverse(db: Session, user, op_id: str, reason: str, key: str) -> FinanceFxOperation:
    op = db.scalar(select(FinanceFxOperation).where(FinanceFxOperation.id == op_id).with_for_update())
    if op is None:
        raise HTTPException(404, "Конвертация не найдена")
    if op.status == "reversed":
        return op
    if len((reason or "").strip()) < 3:
        raise HTTPException(422, "Укажите причину сторно конвертации")
    for i, tid in enumerate([op.sell_transaction_id, op.buy_transaction_id, op.fee_transaction_id]):
        if tid:
            tx = db.get(FinanceTransaction, tid)
            if tx is not None and tx.status == "posted":
                txs.reverse(db, user, tid, f"сторно конвертации: {reason.strip()}", None, f"{key}:{i}")
    op.status, op.reverse_reason = "reversed", reason.strip()
    audit(db, user.id, None, "finance_fx_operation", op.id, "reverse", before={"status": "posted"}, after={"status": "reversed", "reason": reason.strip(), "idempotency_key": key})
    return op


def dto(db: Session, op: FinanceFxOperation) -> dict:
    titles = {a.id: a.title for a in db.scalars(select(FinanceAccount).where(FinanceAccount.id.in_([op.sell_account_id, op.buy_account_id]))).all()}
    return {"id": op.id, "status": op.status, "executed_at": op.executed_at.isoformat(), "rate": str(op.rate), "rate_source": op.rate_source, "external_ref": op.external_ref,
            "sell": {"account_id": op.sell_account_id, "account_title": titles.get(op.sell_account_id, ""), "amount": s(op.sell_amount), "currency": op.sell_currency,
                     "transaction_id": op.sell_transaction_id},
            "buy": {"account_id": op.buy_account_id, "account_title": titles.get(op.buy_account_id, ""), "amount": s(op.buy_amount), "currency": op.buy_currency,
                    "transaction_id": op.buy_transaction_id},
            "fee_transaction_id": op.fee_transaction_id, "fee": s(db.get(FinanceTransaction, op.fee_transaction_id).amount) if op.fee_transaction_id else None,
            "comment": op.comment, "reverse_reason": op.reverse_reason}
