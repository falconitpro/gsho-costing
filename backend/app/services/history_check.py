"""Проверка «по истории» (раздел 5): последние 5 значений по статье и сопоставимым условиям,
среднее, отклонение, нужен ли комментарий."""
from __future__ import annotations

from typing import Any


def history_check(value: float, history: list[dict[str, Any]], threshold_pct: float) -> dict[str, Any]:
    """history: [{amount, currency, on, route, machine_type, deal_id}], уже отфильтрована по статье/условиям."""
    last5 = sorted(history, key=lambda h: str(h["on"]), reverse=True)[:5]
    if not last5:
        return {"history": [], "history_avg": None, "deviation_pct": None, "needs_comment": False, "last_on": None}
    avg = sum(h["amount"] for h in last5) / len(last5)
    dev = (value - avg) / avg * 100 if avg else None
    return {
        "history": [{"amount": h["amount"], "on": str(h["on"]), "deal_id": h.get("deal_id")} for h in last5],
        "history_avg": round(avg, 2),
        "deviation_pct": None if dev is None else round(dev, 2),
        "needs_comment": dev is not None and abs(dev) > threshold_pct,
        "last_on": str(last5[0]["on"]),
    }


def hs_check(entered_duty: float | None, entered_vat: float | None, ref: dict[str, Any] | None) -> dict[str, Any]:
    """Сверка ручной ставки со справочником ТН ВЭД; расхождение помечается в расчёте."""
    if not ref:
        return {"found": False, "mismatch": False}
    mism = (entered_duty is not None and abs(entered_duty - ref["duty_pct"]) > 1e-9) or \
           (entered_vat is not None and abs(entered_vat - ref["vat_pct"]) > 1e-9)
    return {"found": True, "mismatch": mism, "ref_duty_pct": ref["duty_pct"], "ref_vat_pct": ref["vat_pct"],
            "util_fee": ref.get("util_fee"), "certification": ref.get("certification", []), "needs_comment": mism}
