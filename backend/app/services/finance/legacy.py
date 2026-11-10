"""Переходный период старых платёжных отметок (docs/finance/02, раздел 10; 03, разделы 5–6).

Старые точки ввода оплаты продолжают работать, но больше не являются банковским фактом:
- факт платёжного этапа исполнения (POST /deals/{id}/execution/stages/{id}/actual) → origin `execution_legacy`, ключ `stage:{id}`;
- отметка «оплачено» в договоре (POST /legal/contracts/{id}/facts, kind=pay) → `contract_legacy`, ключ `{contract_id}:pay:{no}`;
- ручной платёж в план/факте (POST /deals/{id}/fact, payment_in/out без ref этапа) → `planfact_legacy`, ключ — id записи.

Каждая отметка — ОДНА запись FinanceTransaction со статусом `needs_reconciliation`, без счёта: в подтверждённый остаток
не входит (D-021). Временное разнесение создаётся только при однозначном соответствии строке обязательства;
иначе запись ждёт ручной сверки. ФД выбирает реальный счёт и проводит запись, либо отзывает её (дубль, не платёж).
Уже проведённую ФД запись повторная старая отметка не меняет — только аудит и предупреждение.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Contract, FinanceAllocation, FinanceObligation, FinanceTransaction
from ..deal_service import audit
from .money import ZERO, dec, s
from .obligations import paid_map

MSK = ZoneInfo("Europe/Moscow")
ORIGINS = {"execution_legacy": "этап исполнения", "contract_legacy": "отметка в договоре", "planfact_legacy": "план/факт"}


def _key(origin_type: str, origin_id: str) -> str:
    return f"legacy:{origin_type}:{origin_id}"


def claim_for(db: Session, origin_type: str, origin_id: str) -> Optional[FinanceTransaction]:
    return db.scalar(select(FinanceTransaction).where(FinanceTransaction.idempotency_key == _key(origin_type, origin_id)))


def _booked(on: date) -> datetime:
    """Старые отметки хранят только дату — момент берём полднем по Москве (без выдуманного времени банка)."""
    return datetime.combine(on, time(12, 0), tzinfo=MSK).astimezone(timezone.utc)


def _unique_obligation(db: Session, *, deal_id: Optional[str], contract_id: Optional[str], line_key: Optional[str],
                       direction: str, currency: str, amount: Decimal) -> Optional[FinanceObligation]:
    """Однозначное соответствие: явная строка договора, либо единственная активная строка сделки того же направления
    и валюты с точно такой же суммой и достаточным остатком. Иначе — None (ручная сверка, D-118)."""
    stmt = select(FinanceObligation).where(FinanceObligation.is_active.is_(True), FinanceObligation.sync_status == "ok",
                                           FinanceObligation.direction == direction, FinanceObligation.currency == currency)
    if contract_id and line_key:
        o = db.scalar(stmt.where(FinanceObligation.source_type == "contract", FinanceObligation.source_id == contract_id,
                                 FinanceObligation.source_line_key == line_key))
        cands = [o] if o else []
    elif deal_id:
        cands = [o for o in db.scalars(stmt.where(FinanceObligation.deal_id == deal_id)).all() if o.amount == amount]
    else:
        cands = []
    if len(cands) != 1:
        return None
    o = cands[0]
    paid = paid_map(db, [o.id])[o.id]
    left = o.amount - paid["confirmed"] - paid["provisional"]
    return o if left >= amount else None


def _reverse_allocs(db: Session, user_id: Optional[str], tx: FinanceTransaction, reason: str) -> int:
    n = 0
    for al in db.scalars(select(FinanceAllocation).where(FinanceAllocation.transaction_id == tx.id,
                                                        FinanceAllocation.status.in_(("provisional", "confirmed"))).with_for_update()).all():
        al.status, al.reversed_by, al.reversed_at, al.reverse_reason = "reversed", user_id, datetime.now(timezone.utc), reason
        audit(db, user_id, None, "finance_allocation", al.id, "reverse", before={"status": "provisional"}, after={"status": "reversed", "reason": reason})
        n += 1
    return n


def record(db: Session, user_id: Optional[str], *, origin_type: str, origin_id: str, deal_id: Optional[str], direction: str,
           amount, currency: str, on: Optional[date], note: str = "", contract_id: Optional[str] = None, line_key: Optional[str] = None,
           counterparty_name: str = "") -> dict:
    """Записать/обновить/снять старую отметку оплаты. Возвращает статус для ответа старого endpoint."""
    tx = claim_for(db, origin_type, origin_id)
    amt = dec(amount) if amount not in (None, "") else None
    if tx is not None and (tx.status in ("posted", "reversed")):
        audit(db, user_id, deal_id, "finance_transaction", tx.id, "legacy_ignored",
              after={"origin": f"{origin_type}:{origin_id}", "amount": s(amt), "on": on}, comment="отметка изменена после подтверждения в «Финансах» — не применена")
        return {"status": "confirmed_in_finance", "transaction_id": tx.id,
                "warning": "Платёж уже подтверждён в разделе «Финансы» банковской операцией — изменения старой отметки туда не переносятся."}
    if on is None or amt is None or amt <= 0:   # отметку сняли — непроведённая запись отзывается, история сохраняется
        if tx is None or tx.withdrawn_at:
            return {"status": "none"}
        _reverse_allocs(db, user_id, tx, "старая отметка оплаты снята")
        tx.withdrawn_at, tx.withdrawn_by, tx.withdrawn_reason = datetime.now(timezone.utc), user_id, "старая отметка оплаты снята"
        audit(db, user_id, deal_id, "finance_transaction", tx.id, "withdraw", after={"reason": tx.withdrawn_reason, "origin": f"{origin_type}:{origin_id}"})
        return {"status": "withdrawn", "transaction_id": tx.id}
    created = tx is None
    if created:
        tx = FinanceTransaction(account_id=None, direction=direction, amount=amt, currency=currency, booked_at=_booked(on), counterparty_name=counterparty_name[:255],
                                purpose=f"Старая отметка оплаты ({ORIGINS[origin_type]})", status="needs_reconciliation", origin_type=origin_type,
                                origin_id=origin_id[:64], idempotency_key=_key(origin_type, origin_id), note=note or "", created_by=user_id)
        db.add(tx)
        db.flush()
        audit(db, user_id, deal_id, "finance_transaction", tx.id, "legacy_create", after={"origin": f"{origin_type}:{origin_id}", "amount": s(amt), "currency": currency, "on": on})
    else:
        if tx.amount == amt and tx.currency == currency and tx.booked_at == _booked(on) and tx.direction == direction and not tx.withdrawn_at:
            return _result(db, tx)
        before = {"amount": s(tx.amount), "currency": tx.currency, "booked_at": tx.booked_at, "withdrawn": bool(tx.withdrawn_at)}
        _reverse_allocs(db, user_id, tx, "старая отметка изменена")
        tx.amount, tx.currency, tx.booked_at, tx.direction, tx.note = amt, currency, _booked(on), direction, note or tx.note
        tx.withdrawn_at, tx.withdrawn_by, tx.withdrawn_reason = None, None, ""
        if tx.account_id:
            from ...models import FinanceAccount
            a = db.get(FinanceAccount, tx.account_id)
            if a is None or a.currency != currency:   # счёт, выбранный ФД, больше не подходит по валюте
                tx.account_id = None
        audit(db, user_id, deal_id, "finance_transaction", tx.id, "legacy_update", before=before, after={"amount": s(amt), "currency": currency, "on": on})
    o = _unique_obligation(db, deal_id=deal_id, contract_id=contract_id, line_key=line_key, direction=direction, currency=currency, amount=amt)
    if o is not None:
        n = db.query(FinanceAllocation).filter(FinanceAllocation.transaction_id == tx.id).count()
        al = FinanceAllocation(transaction_id=tx.id, obligation_id=o.id, amount_tx=amt, amount_obligation=amt, status="provisional", allocated_on=date.today(),
                               idempotency_key=f"{tx.idempotency_key}:{n}", note="авто: однозначное соответствие старой отметке", created_by=user_id)
        db.add(al)
        db.flush()
        audit(db, user_id, o.deal_id, "finance_allocation", al.id, "create", after={"transaction_id": tx.id, "obligation_id": o.id, "amount": s(amt), "status": "provisional", "auto": "legacy"})
    return _result(db, tx)


def _result(db: Session, tx: FinanceTransaction) -> dict:
    linked = db.scalar(select(FinanceAllocation.obligation_id).where(FinanceAllocation.transaction_id == tx.id, FinanceAllocation.status == "provisional"))
    return {"status": "needs_reconciliation", "transaction_id": tx.id, "linked_obligation_id": linked,
            "warning": "Отметка сохранена как «требует сверки»: в подтверждённые остатки она не входит. Подтвердите платёж в «Финансах» банковской операцией."}


# ------------------------------------------------------------------ адаптеры точек ввода

def from_stage(db: Session, user_id: Optional[str], deal, stage, kind: str) -> dict:
    return record(db, user_id, origin_type="execution_legacy", origin_id=f"stage:{stage.id}", deal_id=deal.id, direction="in" if kind == "payment_in" else "out",
                  amount=stage.actual_amount, currency=(stage.currency or "RUB").upper(), on=stage.actual_on, note=stage.title)


def from_contract_pay(db: Session, user_id: Optional[str], c: Contract, key: str, data: dict) -> dict:
    from .obligations import DIRECTION, _line_keys
    if c.direction not in DIRECTION:
        return {"status": "none"}
    terms = list(c.payment_terms or [])
    keys = _line_keys(terms)
    idx = next((i for i, l in enumerate(terms) if str(l.get("no") or i + 1) == str(key)), None)
    line_key = keys[idx] if idx is not None else None
    amount = data.get("amount")
    if amount in (None, "") and idx is not None and not data.get("clear"):
        from ..legal_contracts import line_amount
        amount = line_amount(c, terms[idx])
    on = None if data.get("clear") else (date.fromisoformat(data["paid_on"]) if data.get("paid_on") else None)
    from ..legal_contracts import cp_display
    return record(db, user_id, origin_type="contract_legacy", origin_id=f"{c.id}:pay:{key}", deal_id=c.deal_id, direction=DIRECTION[c.direction],
                  amount=amount, currency=c.currency, on=on, note=data.get("comment") or "", contract_id=c.id, line_key=line_key,
                  counterparty_name=cp_display(db, c))


def from_planfact(db: Session, user_id: Optional[str], entry) -> dict:
    if entry.kind not in ("payment_in", "payment_out") or (entry.ref or "").startswith("stage:"):
        return {"status": "none"}   # платёж этапа уже учтён адаптером этапа — второй записи не создаём
    return record(db, user_id, origin_type="planfact_legacy", origin_id=entry.id, deal_id=entry.deal_id, direction="in" if entry.kind == "payment_in" else "out",
                  amount=entry.amount, currency=(entry.currency or "RUB").upper(), on=entry.on, note=entry.comment or "")


def stage_status(db: Session, stage_ids: list[str]) -> dict[str, dict]:
    """Статус старых отметок платёжных этапов для вкладки «Исполнение»."""
    if not stage_ids:
        return {}
    keys = {_key("execution_legacy", f"stage:{sid}"): sid for sid in stage_ids}
    out = {}
    for tx in db.scalars(select(FinanceTransaction).where(FinanceTransaction.idempotency_key.in_(list(keys)))).all():
        st = "withdrawn" if tx.withdrawn_at else tx.status
        out[keys[tx.idempotency_key]] = {"status": st, "transaction_id": tx.id, "url": f"/finance?tab=liquidity&tx={tx.id}"}
    return out
