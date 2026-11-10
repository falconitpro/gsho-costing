"""Права раздела «Финансы» (docs/finance/04_API_и_права.md): доступ к функции по эффективной матрице + видимость объекта.

- чтение раздела: finance_section ≥ READ (ФД, ЗГД; КД — только если выдано в настройках доступа, D-116);
- банковский факт, счета, остатки, разнесения: дополнительно execution_payments ≥ EDIT;
- обязательство видно, если видна его сделка и договор; исходящие платежи заводам — ещё и при payment_terms ≥ READ
  (закупочные условия не расширяются через «Финансы»).
Скрытый объект → 404, а не 403, чтобы не раскрывать его существование.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...core.roles import EDIT, READ
from ...models import Contract, Deal, FinanceObligation


def can_read(user) -> bool:
    return user.can("finance_section", READ)


def can_edit(user) -> bool:
    return can_read(user) and user.can("execution_payments", EDIT)


def require_read(user) -> None:
    if not can_read(user):
        raise HTTPException(403, "Нет доступа к разделу «Финансы» (право «Раздел «Финансы»» в настройках доступа)")


def require_edit(user) -> None:
    require_read(user)
    if not user.can("execution_payments", EDIT):
        raise HTTPException(403, "Вводить и проводить банковский факт могут ФД и ЗГД (право «Фактические платежи»)")


class Visibility:
    """Кэш видимости сделок и договоров в рамках одного запроса (без N+1 на каждый ряд)."""

    def __init__(self, db: Session, user):
        self.db, self.user = db, user
        self._deals: dict[str, bool] = {}
        self._contracts: dict[str, bool] = {}

    def deal(self, deal_id: Optional[str]) -> bool:
        if not deal_id:
            return True
        if deal_id not in self._deals:
            from ...api.common import can_see_deal
            d = self.db.get(Deal, deal_id)
            self._deals[deal_id] = bool(d and can_see_deal(self.user, d))
        return self._deals[deal_id]

    def contract(self, contract_id: Optional[str]) -> bool:
        if not contract_id:
            return True
        if contract_id not in self._contracts:
            from ..legal_contracts import can_see
            c = self.db.get(Contract, contract_id)
            self._contracts[contract_id] = bool(c and can_see(self.db, self.user, c))
        return self._contracts[contract_id]

    def obligation(self, o: FinanceObligation) -> bool:
        if o.direction == "out" and o.counterparty_kind == "factory" and not self.user.can("payment_terms", READ):
            return False
        if o.source_type == "calculation" and o.direction == "out" and not self.user.can("cost", READ):
            return False   # прогноз оплат заводу из расчёта раскрывает закупочную цену — только с правом на себестоимость
        if o.source_type == "purchase_order" and not self.user.can("factory_price_history", READ):
            return False   # график оплат заказа поставщику видят те, кому открыты цены завода (как в «Снабжении»)
        return self.deal(o.deal_id) and self.contract(o.contract_id)
