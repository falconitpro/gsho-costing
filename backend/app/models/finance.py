"""Раздел «Финансы», вариант Б (docs/finance/02_Архитектура_и_модель_данных.md).

Финансовый домен внутри монолита:
- FinanceObligation — проекция строки обязательства из канонического источника (договор и др.). Не редактор графика:
  сумма и срок меняются только в источнике, проекция пересобирается адаптером.
- FinanceAccount / FinanceBalanceSnapshot — счёт компании группы на существующем BankProfile и подтверждённые остатки.
- FinanceTransaction — банковская операция (draft → posted → reversed; исправление — сторно).
- FinanceAllocation — разнесение операции на обязательство (many-to-many, частичное).

Все деньги — Numeric (Decimal), не Float. Схема аддитивная: только новые таблицы.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from . import JSONType

MONEY = Numeric(18, 2)
RATE = Numeric(18, 8)


def _uuid() -> str:
    return str(uuid.uuid4())


class _Stamp:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FinanceObligation(_Stamp, Base):
    """Проекция строки обязательства. paid/status не хранятся — вычисляются по подтверждённым разнесениям."""
    __tablename__ = "finance_obligations"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "source_line_key", name="uq_fin_obligation_source"),
        CheckConstraint("amount >= 0", name="ck_fin_obligation_amount"),
        CheckConstraint("direction in ('in','out')", name="ck_fin_obligation_direction"),
        Index("ix_fin_obl_deal_forecast", "deal_id", "forecast_on"),
        Index("ix_fin_obl_entity_dir", "legal_entity_id", "direction"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(32))                 # contract | calculation | purchase_order | batch | part_request | penalty
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    source_line_key: Mapped[str] = mapped_column(String(128))            # payment:{line_uid|legacy_no}
    source_revision: Mapped[str] = mapped_column(String(64), default="")  # sha256 значимых полей строки источника
    source_line_no: Mapped[Optional[int]] = mapped_column()
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    contract_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    legal_entity_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    counterparty_kind: Mapped[str] = mapped_column(String(16), default="other")   # client | factory | other
    counterparty_id: Mapped[Optional[str]] = mapped_column(String(36))
    counterparty_name_snapshot: Mapped[str] = mapped_column(String(255), default="")
    superseded_by_id: Mapped[Optional[str]] = mapped_column(ForeignKey("finance_obligations.id"))
    direction: Mapped[str] = mapped_column(String(3))                    # in | out
    nature: Mapped[str] = mapped_column(String(16), default="contractual")   # forecast | contractual | operational | penalty
    title: Mapped[str] = mapped_column(String(255), default="")
    event_code: Mapped[str] = mapped_column(String(32), default="")
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    legal_due_on: Mapped[Optional[date]] = mapped_column(Date, index=True)
    legal_due_text: Mapped[str] = mapped_column(Text, default="")
    event_on: Mapped[Optional[date]] = mapped_column(Date)
    event_is_fact: Mapped[bool] = mapped_column(Boolean, default=False)
    forecast_on: Mapped[Optional[date]] = mapped_column(Date, index=True)
    basis_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    excluded_reason: Mapped[str] = mapped_column(String(255), default="")
    sync_status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | conflict | orphaned
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FinanceAccount(_Stamp, Base):
    """Финансовый счёт компании группы. Реквизиты — только в BankProfile, здесь их нет."""
    __tablename__ = "finance_accounts"
    __table_args__ = (
        # один активный финансовый счёт на банковский профиль
        Index("uq_fin_account_active_bank", "bank_profile_id", unique=True, postgresql_where=text("status = 'active'")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    legal_entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    bank_profile_id: Mapped[str] = mapped_column(ForeignKey("bank_profiles.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="active")    # active | archived
    opened_on: Mapped[Optional[date]] = mapped_column(Date)
    archived_on: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


class FinanceBalanceSnapshot(Base):
    """Подтверждённый остаток на момент as_of. Не перезаписывается: исправление — новый снимок."""
    __tablename__ = "finance_balance_snapshots"
    __table_args__ = (Index("ix_fin_snapshot_account_asof", "account_id", "as_of"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("finance_accounts.id"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    source: Mapped[str] = mapped_column(String(32), default="manual")     # manual | statement_import | migration
    external_ref: Mapped[str] = mapped_column(String(255), default="")
    attachment_id: Mapped[Optional[str]] = mapped_column(String(36))
    note: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(36))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FinanceTransaction(_Stamp, Base):
    """Банковская операция. posted не меняется и не удаляется; исправление — сторно (новая операция обратного направления)."""
    __tablename__ = "finance_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_fin_tx_amount"),
        CheckConstraint("direction in ('in','out')", name="ck_fin_tx_direction"),
        CheckConstraint("status in ('draft','needs_reconciliation','posted','reversed')", name="ck_fin_tx_status"),
        Index("ix_fin_tx_account_booked", "account_id", "booked_at"),
        # дубль банковской операции по счёту — защита на уровне БД
        Index("uq_fin_tx_account_extref", "account_id", "external_ref", unique=True,
              postgresql_where=text("external_ref <> '' AND status IN ('posted','reversed') AND origin_type <> 'reversal'")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("finance_accounts.id"))
    direction: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value_date: Mapped[Optional[date]] = mapped_column(Date)
    counterparty_name: Mapped[str] = mapped_column(String(255), default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    external_ref: Mapped[str] = mapped_column(String(128), default="", index=True)
    import_batch_id: Mapped[Optional[str]] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    reversal_of_id: Mapped[Optional[str]] = mapped_column(ForeignKey("finance_transactions.id"))
    reversed_by_id: Mapped[Optional[str]] = mapped_column(String(36))
    origin_type: Mapped[str] = mapped_column(String(24), default="manual")   # manual | bank_import | *_legacy | migration | fx | reversal
    origin_id: Mapped[Optional[str]] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    attachment_id: Mapped[Optional[str]] = mapped_column(String(36))
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    posted_by: Mapped[Optional[str]] = mapped_column(String(36))
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # отзыв непроведённой записи (старая отметка снята, дубль, не платёж) — запись остаётся для истории, из сверки уходит
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    withdrawn_by: Mapped[Optional[str]] = mapped_column(String(36))
    withdrawn_reason: Mapped[str] = mapped_column(Text, default="")


class FinanceAllocation(Base):
    """Разнесение операции на обязательство. confirmed — только для проведённой операции."""
    __tablename__ = "finance_allocations"
    __table_args__ = (
        CheckConstraint("amount_tx > 0 AND amount_obligation > 0", name="ck_fin_alloc_amount"),
        CheckConstraint("status in ('provisional','confirmed','reversed')", name="ck_fin_alloc_status"),
        Index("ix_fin_alloc_obligation_status", "obligation_id", "status"),
        Index("ix_fin_alloc_tx_status", "transaction_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("finance_transactions.id"))
    obligation_id: Mapped[str] = mapped_column(ForeignKey("finance_obligations.id"))
    amount_tx: Mapped[Decimal] = mapped_column(MONEY)
    amount_obligation: Mapped[Decimal] = mapped_column(MONEY)
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(RATE)
    fx_on: Mapped[Optional[date]] = mapped_column(Date)
    fx_source: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="provisional")
    allocated_on: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_by: Mapped[Optional[str]] = mapped_column(String(36))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[Optional[str]] = mapped_column(String(36))
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reverse_reason: Mapped[str] = mapped_column(Text, default="")


class FinanceImportBatch(_Stamp, Base):
    """Пакет импорта банковской выписки: preview ничего не проводит; confirm создаёт проведённые операции, повтор не дублирует."""
    __tablename__ = "finance_import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("finance_accounts.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mapping: Mapped[dict] = mapped_column(JSONType, default=dict)
    rows: Mapped[list] = mapped_column(JSONType, default=list)        # разобранные строки: {n, status, booked_on, direction, amount, ..., errors}
    rows_total: Mapped[int] = mapped_column(default=0)
    rows_valid: Mapped[int] = mapped_column(default=0)
    rows_duplicates: Mapped[int] = mapped_column(default=0)
    rows_errors: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="preview")   # preview | confirmed | cancelled
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FinanceFxOperation(_Stamp, Base):
    """Конвертация: две реальные проведённые операции — списание проданной валюты и зачисление купленной (D-025).
    Не выручка и не расход; ноги не разносятся на обязательства; одна нога — не более чем в одной конвертации."""
    __tablename__ = "finance_fx_operations"
    __table_args__ = (
        CheckConstraint("sell_amount > 0 AND buy_amount > 0 AND rate > 0", name="ck_fin_fx_amounts"),
        CheckConstraint("sell_currency <> buy_currency", name="ck_fin_fx_currencies"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sell_transaction_id: Mapped[str] = mapped_column(ForeignKey("finance_transactions.id"), unique=True)
    buy_transaction_id: Mapped[str] = mapped_column(ForeignKey("finance_transactions.id"), unique=True)
    fee_transaction_id: Mapped[Optional[str]] = mapped_column(ForeignKey("finance_transactions.id"), unique=True)
    sell_account_id: Mapped[str] = mapped_column(ForeignKey("finance_accounts.id"), index=True)
    buy_account_id: Mapped[str] = mapped_column(ForeignKey("finance_accounts.id"), index=True)
    sell_amount: Mapped[Decimal] = mapped_column(MONEY)
    sell_currency: Mapped[str] = mapped_column(String(3))
    buy_amount: Mapped[Decimal] = mapped_column(MONEY)
    buy_currency: Mapped[str] = mapped_column(String(3))
    rate: Mapped[Decimal] = mapped_column(RATE)                       # единиц проданной валюты за 1 единицу купленной
    rate_source: Mapped[str] = mapped_column(String(64), default="")
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    external_ref: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted | reversed
    reverse_reason: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
