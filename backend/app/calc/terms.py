"""Сроки производства и поставки (задача 3).

Срок производства: значение в календарных/рабочих днях, неделях или конкретная дата; отсчёт — от контракта
с заводом или от предоплаты заводу. Срок поставки клиенту: расчётное время от точки отсчёта (подписание,
аванс клиента, открытие аккредитива) до поставки клиенту × (1 + резерв) → округление вверх до целой недели.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from .calendar import WorkCalendar
from .types import DeliveryTerm, DeliveryTermResult, ProductionTerm

UNIT_TITLES = {"calendar_days": "календарных дней", "working_days": "рабочих дней", "weeks": "недель"}
_PLURAL = {"calendar_days": ("календарный день", "календарных дня", "календарных дней"),
           "working_days": ("рабочий день", "рабочих дня", "рабочих дней"), "weeks": ("неделя", "недели", "недель")}


def _plural(n: int, unit: str) -> str:
    one, few, many = _PLURAL.get(unit, (UNIT_TITLES.get(unit, unit),) * 3)
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
START_TITLES = {"client_contract": "с даты подписания контракта", "client_prepayment": "с даты поступления аванса",
                "lc_opened": "с даты открытия аккредитива"}


def production_end(term: Optional[ProductionTerm], fallback_days: int, start: date, cal: WorkCalendar, dt_days: int = 0) -> date:
    """Дата готовности на заводе от точки отсчёта (с учётом Δt сценария, как раньше)."""
    if term is None or (term.unit != "date" and not term.value):
        days = fallback_days + max(dt_days, -fallback_days // 2)
        return start + timedelta(days=days)
    if term.unit == "date" and term.fixed_date:
        return term.fixed_date + timedelta(days=max(dt_days, 0))
    v = float(term.value or 0)
    if term.unit == "working_days":
        end = cal.add_working_days(start, int(round(v)))
        return end + timedelta(days=dt_days) if dt_days > 0 else cal.add_working_days(end, int(dt_days))
    days = int(round(v * 7)) if term.unit == "weeks" else int(round(v))
    return start + timedelta(days=days + max(dt_days, -days // 2))


def production_calendar_days(term: Optional[ProductionTerm], fallback_days: int, start: date, cal: WorkCalendar) -> int:
    return (production_end(term, fallback_days, start, cal) - start).days


def _round_up_week(value: float, unit: str) -> int:
    """Округление вверх до целой недели: календарные дни кратно 7, рабочие — кратно 5, недели — целое."""
    if value <= 0:
        return 0
    if unit == "calendar_days":
        return int(math.ceil(value / 7.0 - 1e-9) * 7)
    if unit == "working_days":
        return int(math.ceil(value / 5.0 - 1e-9) * 5)
    return int(math.ceil(value - 1e-9))


def client_delivery_term(term: DeliveryTerm, start_on: date, delivery_on: date, reserve_pct: float, cal: WorkCalendar,
                         install_days: int = 0) -> DeliveryTermResult:
    cal_days = max((delivery_on - start_on).days, 0)
    if term.unit == "working_days":
        base = cal.working_days_between(start_on, delivery_on)
    elif term.unit == "weeks":
        base = cal_days / 7.0
    else:
        base = cal_days
    with_reserve = base * (1 + reserve_pct / 100.0)
    promised = _round_up_week(with_reserve, term.unit)
    text = f"{promised} {_plural(promised, term.unit)} {START_TITLES[term.start_event]}"
    return DeliveryTermResult(start_event=term.start_event, start_on=start_on, delivery_on=delivery_on,
                              calc_days=int(round(base)), unit=term.unit, reserve_pct=reserve_pct,
                              with_reserve=round(with_reserve, 2), promised=promised, text=text, install_days=install_days)
