"""Единое «сегодня» и «сейчас» по Москве (D-194, решение владельца 27.11.2026).

Сервер и контейнеры могут жить по UTC. Тогда date.today() и datetime.date() у времени из базы с 00:00 до 03:00 МСК
дают вчерашнюю дату, а сроки, «просрочено» и дни до события расходятся на день с тем, что видит сотрудник.
Поэтому в коде приложения календарная дата берётся только отсюда: today() — сегодня по Москве, msk_date(dt) — дата
момента времени по Москве. Время в базе по-прежнему хранится с часовым поясом (UTC), меняется только перевод в дату.
Проверка: tests/test_clock_moscow.py (в коде app/ нет date.today()).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

MSK = timezone(timedelta(hours=3), "MSK")   # без перехода на летнее время с 2014 года


def now() -> datetime:
    """Текущий момент в московском времени (с часовым поясом)."""
    return datetime.now(timezone.utc).astimezone(MSK)


def today() -> date:
    """Сегодня по Москве."""
    return now().date()


def msk_date(dt: Optional[datetime]) -> Optional[date]:
    """Календарная дата момента по Москве. Время без пояса считается UTC (так его пишет база и код)."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return dt                                   # уже дата
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).date()
