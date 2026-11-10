"""Деньги раздела «Финансы»: только Decimal, без двоичного float. Старые Float-поля читаются через Decimal(str(x))."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TOLERANCE = Decimal("0.01")   # допустимое расхождение пересчёта по курсу (копейка)


def dec(v: Any) -> Optional[Decimal]:
    """Значение → Decimal с точностью 2 знака. None/'' → None. float читается через str, чтобы не тащить двоичную погрешность."""
    if v is None or v == "":
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Некорректная сумма: {v!r}")
    if not d.is_finite():
        raise ValueError(f"Некорректная сумма: {v!r}")
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def rate(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Некорректный курс: {v!r}")
    if not d.is_finite() or d <= 0:
        raise ValueError("Курс должен быть положительным")
    return d.quantize(Decimal("0.00000001"))


def s(v: Optional[Decimal]) -> Optional[str]:
    """Сериализация суммы в JSON — строка с двумя знаками (точность не теряется)."""
    return None if v is None else f"{dec(v):.2f}"


def ccy(v: Any) -> str:
    c = str(v or "").strip().upper()
    if len(c) != 3 or not c.isalpha():
        raise ValueError(f"Валюта должна быть кодом ISO 4217 (3 буквы): {v!r}")
    return c


def ru(v, currency: str = "") -> str:
    """Сумма для текста (вопрос, проблема сверки): «77 000,00 RUB». Неразрывные пробелы не используются — текст уходит и в уведомления."""
    d = dec(v)
    if d is None:
        return "—"
    sign = "−" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{sign}{whole},{frac}" + (f" {currency}" if currency else "")


def ru_date(v) -> str:
    return v.strftime("%d.%m.%Y") if v else "—"
