"""D-145: десятичные суммы, этап 2 — суммы внутри JSON-полей (логистика партий, ЗИП, условия договоров и претензии, запчасти).

JSON хранит числа двоичной дробью; при пересчётах появлялись «хвосты» вида 1234.5600000000002. При записи такие поля
нормализуются: дробные числа округляются до 6 знаков (копейки и курсы не меняются), целые и строки не трогаются.
Срабатывает перед каждой записью в базу (событие сессии before_flush) только для перечисленных полей.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

PLACES = 6
FIELDS: dict[str, tuple[str, ...]] = {
    "PaymentScheme": ("lines",),
    "Position": ("zip_items",),
    "Batch": ("items",),
    "LegalZipSet": ("lines",),
    "Contract": ("payment_terms", "penalties", "security", "facts"),
    "ContractClaim": ("lines",),
    "PartKp": ("lines",),
    "PurchaseOrder": ("payments",),
    "WarrantyClaim": ("lines", "compensations"),
    "PartBatch": ("costs",),
}


def tidy(v: Any) -> Any:
    """Дробные числа — до 6 знаков, рекурсивно по словарям и спискам. bool — не число."""
    if isinstance(v, float):
        r = round(v, PLACES)
        return 0.0 if r == 0 else r
    if isinstance(v, dict):
        return {k: tidy(x) for k, x in v.items()}
    if isinstance(v, list):
        return [tidy(x) for x in v]
    return v


@event.listens_for(Session, "before_flush")
def _tidy_money_json(session: Session, _ctx, _instances) -> None:
    for obj in (*session.new, *session.dirty):
        names = FIELDS.get(type(obj).__name__)
        if not names:
            continue
        for n in names:
            cur = getattr(obj, n, None)
            if cur is None:
                continue
            new = tidy(cur)
            if new != cur:
                setattr(obj, n, new)
