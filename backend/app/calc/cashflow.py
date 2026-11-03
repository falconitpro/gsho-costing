"""Денежный поток, кассовый разрыв и стоимость денег (раздел 9 ТЗ).

F = Σ_t max(0, −B_t)·r_фин/365·Δt + Σ_j (k_конв·S_j + k_перевод) + гарантии

Стоимость денег интегрируется точно по дням (между соседними платежами остаток постоянен),
для графика поток агрегируется по неделям.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .types import CashGap, CashflowPoint, FinanceSettings


@dataclass
class Flow:
    on: date
    amount_rub: float          # + приток, − отток
    kind: str                  # client | factory | logistics | customs | vat_refund | service | other
    ref: str = ""
    fx_amount: float = 0.0     # сумма в валюте (для комиссии за конвертацию), 0 для RUB
    currency: str = "RUB"


@dataclass
class CashflowResult:
    points: list[CashflowPoint] = field(default_factory=list)
    gap: CashGap = field(default_factory=CashGap)
    finance_cost_rub: float = 0.0
    conversion_fees_rub: float = 0.0
    transfer_fees_rub: float = 0.0
    guarantee_cost_rub: float = 0.0
    deposit_income_rub: float = 0.0

    @property
    def total_finance_rub(self) -> float:
        return (
            self.finance_cost_rub + self.conversion_fees_rub + self.transfer_fees_rub
            + self.guarantee_cost_rub - self.deposit_income_rub
        )

    def breakdown(self) -> dict[str, float]:
        return {
            "money_cost": round(self.finance_cost_rub, 2),
            "conversion_fees": round(self.conversion_fees_rub, 2),
            "transfer_fees": round(self.transfer_fees_rub, 2),
            "guarantees": round(self.guarantee_cost_rub, 2),
            "deposit_income": round(-self.deposit_income_rub, 2),
        }


def build_cashflow(flows: list[Flow], fin: FinanceSettings, guarantee_cost_rub: float = 0.0) -> CashflowResult:
    res = CashflowResult(guarantee_cost_rub=guarantee_cost_rub)
    if not flows:
        return res
    flows = sorted(flows, key=lambda f: f.on)

    # комиссии за конвертацию и перевод
    def transfer_fee(amount: float) -> float:
        """Комиссия за перевод: фиксированная сумма, процент от суммы перевода или и то и другое."""
        fee = 0.0
        if fin.transfer_fee_mode in ("fixed", "both"):
            fee += fin.transfer_fee_rub
        if fin.transfer_fee_mode in ("pct", "both"):
            fee += abs(amount) * fin.transfer_fee_pct / 100.0
        return fee

    for f in flows:
        if f.amount_rub < 0 and f.currency != "RUB":
            res.conversion_fees_rub += abs(f.amount_rub) * fin.conversion_fee_pct / 100.0
            res.transfer_fees_rub += transfer_fee(f.amount_rub)
        elif f.amount_rub < 0 and f.kind == "factory":
            res.transfer_fees_rub += transfer_fee(f.amount_rub)

    # точная интеграция по датам платежей
    r_fin = fin.financing_rate_pct / 100.0
    r_dep = (fin.deposit_rate_pct or 0.0) / 100.0
    by_day: dict[date, float] = {}
    for f in flows:
        by_day[f.on] = by_day.get(f.on, 0.0) + f.amount_rub
    days = sorted(by_day)
    balance = 0.0
    gap_amount, gap_start, gap_days = 0.0, None, 0
    neg_start: date | None = None
    for i, d in enumerate(days):
        balance += by_day[d]
        span = (days[i + 1] - d).days if i + 1 < len(days) else 0
        if balance < -0.005:
            if neg_start is None:
                neg_start = d
            if balance < gap_amount:
                gap_amount, gap_start = balance, neg_start
            res.finance_cost_rub += -balance * r_fin / 365 * span
            run = (days[i + 1] - neg_start).days if i + 1 < len(days) else (d - neg_start).days
            gap_days = max(gap_days, run)
        else:
            neg_start = None
            if r_dep and balance > 0:
                res.deposit_income_rub += balance * r_dep / 365 * span

    # недельные корзины для графика
    bucket = max(1, fin.cashflow_bucket_days)
    cur, end = days[0], days[-1]
    i = 0
    bal = 0.0
    while cur <= end:
        nxt = cur + timedelta(days=bucket)
        inflow = outflow = 0.0
        while i < len(days) and days[i] < nxt:
            a = by_day[days[i]]
            if a >= 0:
                inflow += a
            else:
                outflow += -a
            i += 1
        bal += inflow - outflow
        res.points.append(CashflowPoint(on=cur, inflow=round(inflow, 2), outflow=round(outflow, 2), balance=round(bal, 2)))
        cur = nxt

    res.gap = CashGap(amount_rub=round(gap_amount, 2), starts_on=gap_start, days=gap_days if gap_amount < 0 else 0)
    return res
