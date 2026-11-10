"""Банковские операции и разнесение на обязательства.

- ручная операция создаётся как draft (со счётом) или needs_reconciliation (без счёта); проведение — отдельным действием;
- проведённая операция не меняется и не удаляется: исправление — сторно (новая проведённая операция обратного направления),
  связанные разнесения сторнируются в той же транзакции;
- разнесение: частичное, many-to-many; provisional → confirmed только для проведённой операции;
  сумма разнесений не превышает ни операцию, ни обязательство; строки блокируются SELECT … FOR UPDATE.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import FinanceAccount, FinanceAllocation, FinanceObligation, FinanceTransaction
from ..deal_service import audit
from .money import TOLERANCE, ZERO, dec, rate, s

ACTIVE_ALLOC = ("provisional", "confirmed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_tx(db: Session, tx_id: str) -> FinanceTransaction:
    tx = db.scalar(select(FinanceTransaction).where(FinanceTransaction.id == tx_id).with_for_update())
    if tx is None:
        raise HTTPException(404, "Операция не найдена")
    return tx


def allocated(db: Session, tx_ids: list[str]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    if not tx_ids:
        return out
    for tid, total in db.execute(select(FinanceAllocation.transaction_id, func.sum(FinanceAllocation.amount_tx)).where(
            FinanceAllocation.transaction_id.in_(tx_ids), FinanceAllocation.status.in_(ACTIVE_ALLOC)).group_by(FinanceAllocation.transaction_id)).all():
        out[tid] = dec(total) or ZERO
    return out


def _snap(tx: FinanceTransaction) -> dict:
    return {"status": tx.status, "account_id": tx.account_id, "direction": tx.direction, "amount": s(tx.amount), "currency": tx.currency,
            "booked_at": tx.booked_at, "external_ref": tx.external_ref}


def create(db: Session, user, *, account_id: Optional[str], direction: str, amount: Decimal, currency: str, booked_at: datetime,
           value_date: Optional[date], counterparty_name: str, purpose: str, external_ref: str, note: str, key: str) -> tuple[FinanceTransaction, bool]:
    dup = db.scalar(select(FinanceTransaction).where(FinanceTransaction.idempotency_key == key))
    if dup:
        return dup, False
    if direction not in ("in", "out"):
        raise HTTPException(422, "direction: in | out")
    if amount is None or amount <= 0:
        raise HTTPException(422, "Сумма операции должна быть положительной; направление задаётся отдельно")
    if booked_at.tzinfo is None:
        raise HTTPException(422, "Укажите дату и время операции с часовым поясом")
    if account_id:
        a = db.get(FinanceAccount, account_id)
        if a is None:
            raise HTTPException(422, "Счёт не найден")
        if a.status != "active":
            raise HTTPException(409, "Счёт в архиве")
        if a.currency != currency:
            raise HTTPException(422, f"Валюта операции ({currency}) не совпадает с валютой счёта ({a.currency})")
    tx = FinanceTransaction(account_id=account_id or None, direction=direction, amount=amount, currency=currency, booked_at=booked_at, value_date=value_date,
                            counterparty_name=(counterparty_name or "")[:255], purpose=purpose or "", external_ref=(external_ref or "").strip()[:128],
                            status="draft" if account_id else "needs_reconciliation", origin_type="manual", idempotency_key=key, note=note or "", created_by=user.id)
    db.add(tx)
    db.flush()
    audit(db, user.id, None, "finance_transaction", tx.id, "create", after={**_snap(tx), "idempotency_key": key})
    return tx, True


def post(db: Session, user, tx_id: str, key: str) -> FinanceTransaction:
    tx = _lock_tx(db, tx_id)
    if tx.status == "posted":
        return tx   # повтор проведения — тот же результат
    if tx.status not in ("draft", "needs_reconciliation"):
        raise HTTPException(409, "Провести можно только черновик или операцию, требующую сверки")
    if tx.withdrawn_at:
        raise HTTPException(409, "Запись отозвана — проведение недоступно")
    a = db.scalar(select(FinanceAccount).where(FinanceAccount.id == tx.account_id).with_for_update()) if tx.account_id else None
    if a is None:
        raise HTTPException(422, "Для проведения выберите действующий финансовый счёт — без счёта операция остаётся «требует сверки»")
    if a.status != "active":
        raise HTTPException(409, "Счёт в архиве")
    if a.currency != tx.currency:
        raise HTTPException(422, f"Валюта операции ({tx.currency}) не совпадает с валютой счёта ({a.currency})")
    if tx.external_ref and db.scalar(select(FinanceTransaction.id).where(FinanceTransaction.account_id == a.id, FinanceTransaction.external_ref == tx.external_ref,
                                                                          FinanceTransaction.status.in_(("posted", "reversed")), FinanceTransaction.origin_type != "reversal",
                                                                          FinanceTransaction.id != tx.id)):
        raise HTTPException(409, f"По счёту уже проведена операция с банковским номером «{tx.external_ref}» — возможен дубль")
    before = _snap(tx)
    tx.status, tx.posted_by, tx.posted_at = "posted", user.id, _now()
    audit(db, user.id, None, "finance_transaction", tx.id, "post", before=before, after={**_snap(tx), "idempotency_key": key})
    return tx


def reverse(db: Session, user, tx_id: str, reason: str, booked_at: Optional[datetime], key: str) -> FinanceTransaction:
    """Сторно: новая проведённая операция обратного направления; исходная — reversed, её разнесения — reversed. Ничего не удаляется."""
    dup = db.scalar(select(FinanceTransaction).where(FinanceTransaction.idempotency_key == key))
    if dup:
        if dup.reversal_of_id != tx_id:
            raise HTTPException(409, "Ключ идемпотентности уже использован другой операцией")
        return dup
    if len((reason or "").strip()) < 3:
        raise HTTPException(422, "Укажите причину сторно")
    tx = _lock_tx(db, tx_id)
    if tx.origin_type == "reversal":
        raise HTTPException(409, "Сторнирующую операцию нельзя сторнировать повторно — создайте новую операцию")
    if tx.status != "posted":
        raise HTTPException(409, "Сторнировать можно только проведённую операцию" if tx.status != "reversed" else "Операция уже сторнирована")
    at = booked_at or _now()
    if at.tzinfo is None:
        raise HTTPException(422, "Укажите дату сторно с часовым поясом")
    if at < tx.booked_at:
        raise HTTPException(422, "Дата сторно не может быть раньше исходной операции")
    rev = FinanceTransaction(account_id=tx.account_id, direction="out" if tx.direction == "in" else "in", amount=tx.amount, currency=tx.currency, booked_at=at,
                             counterparty_name=tx.counterparty_name, purpose=f"Сторно: {reason.strip()}", external_ref=tx.external_ref, status="posted",
                             reversal_of_id=tx.id, origin_type="reversal", origin_id=tx.id, idempotency_key=key, note=reason.strip(),
                             created_by=user.id, posted_by=user.id, posted_at=_now())
    db.add(rev)
    db.flush()
    n = 0
    for al in db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == tx.id, FinanceAllocation.status.in_(ACTIVE_ALLOC)).with_for_update()).all():
        _reverse_alloc(db, user, al, f"сторно операции: {reason.strip()}")
        n += 1
    before = _snap(tx)
    tx.status, tx.reversed_by_id = "reversed", rev.id
    audit(db, user.id, None, "finance_transaction", tx.id, "reverse", before=before,
          after={**_snap(tx), "reversal_id": rev.id, "reason": reason.strip(), "allocations_reversed": n, "idempotency_key": key})
    return rev


def assign_account(db: Session, user, tx_id: str, account_id: str, external_ref: Optional[str]) -> FinanceTransaction:
    """Сверка записи «требует сверки» (старая отметка или операция без счёта): ФД указывает реальный счёт и банковский номер.
    Проведение — отдельным действием, как у любой операции."""
    tx = _lock_tx(db, tx_id)
    if tx.status not in ("draft", "needs_reconciliation") or tx.withdrawn_at:
        raise HTTPException(409, "Счёт меняется только у непроведённой и не отозванной записи")
    a = db.get(FinanceAccount, account_id)
    if a is None or a.status != "active":
        raise HTTPException(422, "Выберите действующий финансовый счёт")
    if a.currency != tx.currency:
        raise HTTPException(422, f"Валюта записи ({tx.currency}) не совпадает с валютой счёта ({a.currency})")
    before = _snap(tx)
    tx.account_id = a.id
    if external_ref is not None:
        tx.external_ref = external_ref.strip()[:128]
    audit(db, user.id, None, "finance_transaction", tx.id, "assign_account", before=before, after=_snap(tx))
    return tx


def withdraw(db: Session, user, tx_id: str, reason: str) -> FinanceTransaction:
    """Отозвать непроведённую запись (дубль банковской операции, не платёж, ошибка ввода). Не удаляется; временные разнесения сторнируются."""
    if len((reason or "").strip()) < 3:
        raise HTTPException(422, "Укажите причину отзыва")
    tx = _lock_tx(db, tx_id)
    if tx.withdrawn_at:
        return tx
    if tx.status not in ("draft", "needs_reconciliation"):
        raise HTTPException(409, "Проведённую операцию не отзывают — её сторнируют")
    n = 0
    for al in db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == tx.id, FinanceAllocation.status.in_(ACTIVE_ALLOC)).with_for_update()).all():
        _reverse_alloc(db, user, al, f"запись отозвана: {reason.strip()}")
        n += 1
    tx.withdrawn_at, tx.withdrawn_by, tx.withdrawn_reason = _now(), user.id, reason.strip()
    audit(db, user.id, None, "finance_transaction", tx.id, "withdraw", before={"status": tx.status}, after={"withdrawn": True, "reason": reason.strip(), "allocations_reversed": n})
    return tx


# ------------------------------------------------------------------ разнесение

def _reverse_alloc(db: Session, user, al: FinanceAllocation, reason: str) -> None:
    before = {"status": al.status}
    al.status, al.reversed_by, al.reversed_at, al.reverse_reason = "reversed", user.id, _now(), reason
    audit(db, user.id, None, "finance_allocation", al.id, "reverse", before=before, after={"status": "reversed", "reason": reason})


def allocate(db: Session, user, vis, tx_id: str, items: list[dict], status: str, key: str) -> list[FinanceAllocation]:
    existing = list(db.scalars(select(FinanceAllocation).where(FinanceAllocation.idempotency_key.like(f"{key}:%"))).all())
    if existing:
        if any(a.transaction_id != tx_id for a in existing):
            raise HTTPException(409, "Ключ идемпотентности уже использован для другой операции")
        return existing   # повтор запроса — тот же результат
    if status not in ("provisional", "confirmed"):
        raise HTTPException(422, "status: provisional | confirmed")
    if not items:
        raise HTTPException(422, "Выберите хотя бы одно обязательство")
    tx = _lock_tx(db, tx_id)
    if tx.origin_type == "reversal" or tx.status == "reversed":
        raise HTTPException(409, "Сторнированную операцию и сторно не разносят")
    if tx.withdrawn_at:
        raise HTTPException(409, "Запись отозвана — разнесение недоступно")
    if status == "confirmed" and tx.status != "posted":
        raise HTTPException(409, "Подтвердить разнесение можно только для проведённой операции")
    ob_ids = sorted({str(i["obligation_id"]) for i in items})
    if len(ob_ids) != len(items):
        raise HTTPException(422, "Одно обязательство указано несколько раз — объедините суммы")
    obs = {o.id: o for o in db.scalars(select(FinanceObligation).where(FinanceObligation.id.in_(ob_ids)).order_by(FinanceObligation.id).with_for_update()).all()}
    used_tx = allocated(db, [tx.id])[tx.id]
    total_tx = ZERO
    planned = []
    for i, it in enumerate(items):
        o = obs.get(str(it["obligation_id"]))
        if o is None or not vis.obligation(o):
            raise HTTPException(404, "Обязательство не найдено")
        if not o.is_active:
            raise HTTPException(409, f"«{o.title}» исключено из итогов ({o.excluded_reason or 'неактивно'}) — разнесение недоступно")
        if o.sync_status == "conflict":
            raise HTTPException(409, f"«{o.title}»: источник изменился после разнесения — сначала сверка")
        if o.direction != tx.direction:
            raise HTTPException(422, f"«{o.title}»: направление обязательства не совпадает с операцией ({'поступление' if tx.direction == 'in' else 'списание'})")
        a_tx, a_ob = dec(it.get("amount_tx")), dec(it.get("amount_obligation"))
        try:
            fx = rate(it.get("fx_rate"))
        except ValueError as e:
            raise HTTPException(422, str(e))
        if o.currency == tx.currency:
            a_ob = a_ob if a_ob is not None else a_tx
            if a_tx is None or a_ob != a_tx:
                raise HTTPException(422, f"«{o.title}»: в одной валюте сумма погашения равна сумме разнесения")
            fx = None
        else:
            if a_tx is None or a_ob is None or fx is None or not it.get("fx_on") or not it.get("fx_source"):
                raise HTTPException(422, f"«{o.title}»: обязательство в {o.currency}, операция в {tx.currency} — укажите обе суммы, курс, дату и источник курса")
            if abs(a_tx * fx - a_ob) > TOLERANCE * max(Decimal(1), fx):
                raise HTTPException(422, f"«{o.title}»: сумма погашения не согласуется с курсом ({s(a_tx)} × {fx} ≠ {s(a_ob)})")
        if a_tx <= 0 or a_ob <= 0:
            raise HTTPException(422, "Суммы разнесения положительные")
        used_ob = db.scalar(select(func.coalesce(func.sum(FinanceAllocation.amount_obligation), 0)).where(
            FinanceAllocation.obligation_id == o.id, FinanceAllocation.status.in_(ACTIVE_ALLOC)))
        left_ob = o.amount - (dec(used_ob) or ZERO)
        if a_ob > left_ob:
            raise HTTPException(409, f"«{o.title}»: остаток к разнесению {s(left_ob)} {o.currency}, указано {s(a_ob)} — переплата не разносится на обязательство")
        total_tx += a_tx
        planned.append((i, o, a_tx, a_ob, fx, it))
    if used_tx + total_tx > tx.amount:
        raise HTTPException(409, f"Сумма разнесений ({s(used_tx + total_tx)}) превышает сумму операции ({s(tx.amount)} {tx.currency})")
    out = []
    now = _now()
    for i, o, a_tx, a_ob, fx, it in planned:
        al = FinanceAllocation(transaction_id=tx.id, obligation_id=o.id, amount_tx=a_tx, amount_obligation=a_ob, fx_rate=fx,
                               fx_on=it.get("fx_on"), fx_source=(it.get("fx_source") or "")[:64], status=status, allocated_on=date.today(),
                               idempotency_key=f"{key}:{i}", note=(it.get("note") or ""), created_by=user.id,
                               confirmed_by=user.id if status == "confirmed" else None, confirmed_at=now if status == "confirmed" else None)
        db.add(al)
        db.flush()
        audit(db, user.id, o.deal_id, "finance_allocation", al.id, "create",
              after={"transaction_id": tx.id, "obligation_id": o.id, "amount_tx": s(a_tx), "amount_obligation": s(a_ob), "fx_rate": str(fx) if fx else None,
                     "status": status, "idempotency_key": al.idempotency_key})
        out.append(al)
    return out


def confirm_allocation(db: Session, user, vis, alloc_id: str) -> FinanceAllocation:
    al = db.scalar(select(FinanceAllocation).where(FinanceAllocation.id == alloc_id).with_for_update())
    if al is None or not vis.obligation(db.get(FinanceObligation, al.obligation_id)):
        raise HTTPException(404, "Разнесение не найдено")
    if al.status == "confirmed":
        return al
    if al.status != "provisional":
        raise HTTPException(409, "Разнесение сторнировано")
    tx = _lock_tx(db, al.transaction_id)
    if tx.status != "posted":
        raise HTTPException(409, "Подтвердить разнесение можно только после проведения операции")
    al.status, al.confirmed_by, al.confirmed_at = "confirmed", user.id, _now()
    audit(db, user.id, None, "finance_allocation", al.id, "confirm", before={"status": "provisional"}, after={"status": "confirmed"})
    return al


def reverse_allocation(db: Session, user, vis, alloc_id: str, reason: str) -> FinanceAllocation:
    if len((reason or "").strip()) < 3:
        raise HTTPException(422, "Укажите причину отмены разнесения")
    al = db.scalar(select(FinanceAllocation).where(FinanceAllocation.id == alloc_id).with_for_update())
    if al is None or not vis.obligation(db.get(FinanceObligation, al.obligation_id)):
        raise HTTPException(404, "Разнесение не найдено")
    if al.status == "reversed":
        return al
    _reverse_alloc(db, user, al, reason.strip())
    return al


def alloc_dto(al: FinanceAllocation, tx: Optional[FinanceTransaction] = None, ob: Optional[FinanceObligation] = None) -> dict:
    out = {"id": al.id, "transaction_id": al.transaction_id, "obligation_id": al.obligation_id, "amount_tx": s(al.amount_tx), "amount_obligation": s(al.amount_obligation),
           "fx_rate": str(al.fx_rate) if al.fx_rate is not None else None, "fx_on": al.fx_on.isoformat() if al.fx_on else None, "fx_source": al.fx_source,
           "status": al.status, "allocated_on": al.allocated_on.isoformat(), "confirmed_at": al.confirmed_at.isoformat() if al.confirmed_at else None,
           "reverse_reason": al.reverse_reason, "note": al.note}
    if tx is not None:
        out["transaction"] = {"id": tx.id, "booked_at": tx.booked_at.isoformat(), "amount": s(tx.amount), "currency": tx.currency, "status": tx.status,
                              "external_ref": tx.external_ref, "counterparty_name": tx.counterparty_name}
    if ob is not None:
        out["obligation"] = {"id": ob.id, "title": ob.title, "currency": ob.currency, "direction": ob.direction}
    return out


LEGACY_TITLES = {"execution_legacy": "старая отметка: этап исполнения", "contract_legacy": "старая отметка: договор", "planfact_legacy": "старая отметка: план/факт"}


def tx_dto(tx: FinanceTransaction, used: Decimal, account_title: str = "") -> dict:
    movable = tx.status in ("posted", "draft", "needs_reconciliation") and tx.origin_type != "reversal" and not tx.withdrawn_at
    return {"id": tx.id, "account_id": tx.account_id, "account_title": account_title, "direction": tx.direction, "amount": s(tx.amount), "currency": tx.currency,
            "booked_at": tx.booked_at.isoformat(), "value_date": tx.value_date.isoformat() if tx.value_date else None,
            "counterparty_name": tx.counterparty_name, "purpose": tx.purpose, "external_ref": tx.external_ref, "status": tx.status, "origin_type": tx.origin_type,
            "reversal_of_id": tx.reversal_of_id, "reversed_by_id": tx.reversed_by_id, "note": tx.note,
            "allocated": s(used), "unallocated": s(max(tx.amount - used, ZERO)) if movable else s(ZERO),
            "posted_at": tx.posted_at.isoformat() if tx.posted_at else None,
            "withdrawn": bool(tx.withdrawn_at), "withdrawn_reason": tx.withdrawn_reason, "origin_title": LEGACY_TITLES.get(tx.origin_type, "")}
