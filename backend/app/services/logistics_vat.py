"""D-122: рублёвые расходы логиста (брокер и СВХ, доставка по РФ, сертификация) — логист указывает «с НДС» или «без НДС».
В себестоимость и историю — сумма без НДС; НДС уплачивается вместе с оплатой и принимается к вычету в квартале оплаты."""
from __future__ import annotations

BATCH_ITEMS = ("broker_rub", "rf_delivery_rub")     # в партии: items["vat_included"][key]
POSITION_ITEMS = ("certification_rub",)               # в позиции: logistics["vat_included"][key]


def split(amount: float, included: bool, pct: float) -> tuple[float, float]:
    """(без НДС, НДС) для введённой суммы."""
    a = float(amount or 0.0)
    if not included or not a:
        return a, 0.0
    net = a / (1 + pct / 100.0)
    return net, a - net


def included(container: dict | None, key: str) -> bool:
    return bool(((container or {}).get("vat_included") or {}).get(key))
