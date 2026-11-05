"""Цена строки заявки на запчасти, порог маржи и минимум маржи КП (ТЗ запчастей, раздел 6). Чистые функции.

Цена клиенту подбирается в порядке: ручная цена менеджера → договорная цена клиента → цена продажи (прайс)
→ расчёт от себестоимости по порогу маржи. Себестоимость строки — стоимость для компании в РФ по маршруту
с наценками компаний группы (services/markup_chain.py), в рублях по курсу ЦБ.

Порог маржи зависит от себестоимости строки (за все единицы): до 10 000 ₽ — 40 %, до 100 000 ₽ — 30 %, дороже — 20 %.
Ниже порога не более чем на допуск (5 п. п.) — утверждает КД, ниже — ФД. Если маржа КП в рублях меньше минимума
(5 000 ₽), в КП добавляется строка «Обработка заказа» на недостающую сумму.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

DEFAULT_BANDS = [{"up_to_rub": 10_000, "pct": 40.0}, {"up_to_rub": 100_000, "pct": 30.0}, {"up_to_rub": None, "pct": 20.0}]

LINE_STATUS = {
    "new": "подбор", "rfq": "запрос поставщику", "quoted": "ответ получен", "in_kp": "в КП", "ordered": "заказана клиентом",
    "po": "заказана у поставщика", "transit": "в пути", "in_stock": "на складе", "shipped": "отгружена",
    "unavailable": "нет у поставщиков", "replaced": "замена аналогом", "cancelled": "отменена клиентом",
}
CLOSED_LINE = {"shipped", "unavailable", "cancelled"}


def threshold_pct(cost_rub: Optional[float], bands: Iterable[Mapping[str, Any]] = DEFAULT_BANDS) -> Optional[float]:
    if cost_rub is None:
        return None
    for b in bands:
        if b.get("up_to_rub") is None or cost_rub <= float(b["up_to_rub"]):
            return float(b["pct"])
    return None


def to_rub(amount: Optional[float], currency: str, fx: Mapping[str, float]) -> Optional[float]:
    if amount is None:
        return None
    if not currency or currency == "RUB":
        return float(amount)
    rate = fx.get(currency)
    return float(amount) * rate if rate else None


def price_line(*, qty: float, unit_cost_rub: Optional[float], fx: Mapping[str, float],
               manual: Optional[tuple[float, str]] = None, contract: Optional[tuple[float, str]] = None,
               pricelist: Optional[tuple[float, str]] = None, bands: Iterable[Mapping[str, Any]] = DEFAULT_BANDS,
               cd_tolerance_pp: float = 5.0) -> dict[str, Any]:
    """Цена за единицу, её источник и проверка маржи. Цены — (сумма, валюта)."""
    qty = float(qty or 0)
    cost_total = unit_cost_rub * qty if unit_cost_rub is not None else None
    thr = threshold_pct(cost_total, bands)
    price, cur, source = None, "RUB", ""
    for src, cand in (("manual", manual), ("contract", contract), ("pricelist", pricelist)):
        if cand and cand[0]:
            price, cur, source = float(cand[0]), cand[1] or "RUB", src
            break
    if price is None and unit_cost_rub is not None and thr is not None:
        price, cur, source = round(unit_cost_rub / (1 - thr / 100), 2), "RUB", "calc"
    out: dict[str, Any] = {"price": price, "currency": cur, "price_source": source, "threshold_pct": thr,
                           "cost": round(unit_cost_rub, 2) if unit_cost_rub is not None else None,
                           "cost_total_rub": round(cost_total, 2) if cost_total is not None else None}
    price_rub = to_rub(price, cur, fx)
    out["price_rub"] = round(price_rub, 2) if price_rub is not None else None
    out["sum_rub"] = round(price_rub * qty, 2) if price_rub is not None else None
    if price_rub and unit_cost_rub is not None:
        margin = (price_rub - unit_cost_rub) / price_rub * 100
        out["margin_pct"] = round(margin, 2)
        out["profit_rub"] = round((price_rub - unit_cost_rub) * qty, 2)
        gap = (thr - margin) if thr is not None else 0
        out["below_pp"] = round(gap, 2) if gap > 0 else 0.0
        out["approval"] = None if gap <= 0 else ("cd" if gap <= cd_tolerance_pp else "fd")
    else:
        out.update({"margin_pct": None, "profit_rub": None, "below_pp": 0.0, "approval": None})
    return out


def kp_summary(lines: Iterable[Mapping[str, Any]], min_margin_rub: float = 5000.0) -> dict[str, Any]:
    """Итоги КП по выбранным строкам: сумма, себестоимость, маржа, строка «Обработка заказа», нужный уровень визы."""
    lines = [l for l in lines if l.get("sum_rub") is not None]
    total = sum(l["sum_rub"] for l in lines)
    costed = [l for l in lines if l.get("profit_rub") is not None]
    profit = sum(l["profit_rub"] for l in costed)
    out: dict[str, Any] = {"lines": len(lines), "sum_rub": round(total, 2), "profit_rub": round(profit, 2) if costed else None,
                           "margin_pct": round(profit / total * 100, 2) if costed and total else None,
                           "uncosted_lines": len(lines) - len(costed)}
    fee = max(0.0, float(min_margin_rub) - profit) if costed else 0.0
    out["handling_fee_rub"] = round(fee, 2)
    levels = {l.get("approval") for l in lines}
    out["approval"] = "fd" if "fd" in levels else ("cd" if "cd" in levels else None)
    return out


def request_status(lines: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Сводный статус заявки из статусов строк: «КП: 28 из 40 строк, отгружено 12»."""
    lines = list(lines)
    total = len(lines)
    by: dict[str, int] = {}
    for l in lines:
        by[l.get("status") or "new"] = by.get(l.get("status") or "new", 0) + 1
    in_kp = sum(n for s, n in by.items() if s not in ("new", "rfq", "quoted", "unavailable", "cancelled"))
    shipped = by.get("shipped", 0)
    text = f"КП: {in_kp} из {total} строк" + (f", отгружено {shipped}" if shipped else "")
    return {"total": total, "by_status": by, "in_kp": in_kp, "shipped": shipped, "text": text}
