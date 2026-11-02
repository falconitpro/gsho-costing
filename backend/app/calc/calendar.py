"""Производственный календарь РФ: рабочие дни для сроков оплаты и поставки (задачи 1, 3) и SLA.

Выходные — суббота и воскресенье; праздники и переносы — по списку ниже (его дополняет администратор
через настройку `rf_holidays` / `rf_workdays`: списки дат ISO). Если год в списке отсутствует,
учитываются только нерабочие праздничные дни по ТК РФ (ст. 112) без переносов.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

# Нерабочие праздничные дни по ТК РФ (ежегодно)
FIXED_HOLIDAYS = [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8), (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)]

# Переносы по постановлениям Правительства РФ (дополняются по мере публикации)
EXTRA_HOLIDAYS: dict[int, list[tuple[int, int]]] = {
    2026: [(1, 9), (3, 9), (5, 11), (12, 31)],   # постановление Правительства РФ о переносах на 2026 г.
    # 2027 и далее — администратор задаёт переносы настройкой rf_holidays после публикации постановления
}
EXTRA_WORKDAYS: dict[int, list[tuple[int, int]]] = {}   # рабочие субботы при переносах


class WorkCalendar:
    def __init__(self, holidays: Optional[Iterable[date]] = None, workdays: Optional[Iterable[date]] = None):
        self.holidays: set[date] = set(holidays or [])
        self.workdays: set[date] = set(workdays or [])

    def _year_holidays(self, year: int) -> set[date]:
        out = {date(year, m, d) for m, d in FIXED_HOLIDAYS}
        out |= {date(year, m, d) for m, d in EXTRA_HOLIDAYS.get(year, [])}
        return out

    def is_workday(self, d: date) -> bool:
        if d in self.workdays or any(d == date(d.year, m, dd) for m, dd in EXTRA_WORKDAYS.get(d.year, [])):
            return True
        if d.weekday() >= 5:
            return False
        return d not in self.holidays and d not in self._year_holidays(d.year)

    def add_working_days(self, start: date, days: int) -> date:
        d = start
        step = 1 if days >= 0 else -1
        remaining = abs(days)
        while remaining > 0:
            d += timedelta(days=step)
            if self.is_workday(d):
                remaining -= 1
        return d

    def working_days_between(self, start: date, end: date) -> int:
        """Количество рабочих дней в интервале (start, end]."""
        if end < start:
            return -self.working_days_between(end, start)
        n, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_workday(d):
                n += 1
        return n

    def add(self, start: date, days: int, kind: str = "calendar") -> date:
        if kind == "working":
            return self.add_working_days(start, days)
        return start + timedelta(days=days)


DEFAULT_CALENDAR = WorkCalendar()


def add_business_days(start: date, days: int, calendar: Optional[WorkCalendar] = None) -> date:
    return (calendar or DEFAULT_CALENDAR).add_working_days(start, days)
