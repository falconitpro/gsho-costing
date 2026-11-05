"""Парк машин клиентов: средняя наработка, прогноз следующего ТО и напоминания менеджеру (ТЗ запчастей, раздел 9).

Средняя наработка в день — по двум последним замерам (если замер один — от ввода в эксплуатацию с нуля моточасов),
иначе — вручную заданная. ТО: для каждого комплекта следующее кратное его периодичности выше текущей наработки;
ближайшее из них — следующее ТО, в него входят все комплекты, чья периодичность его делит (ТО-1000 включает ТО-500 и ТО-250).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional, Sequence


def avg_per_day(readings: Sequence[tuple[date, float]], commissioned_on: Optional[date] = None, manual: Optional[float] = None) -> Optional[float]:
    pts = sorted(readings)
    if len(pts) >= 2:
        (d1, h1), (d2, h2) = pts[-2], pts[-1]
        if (d2 - d1).days > 0 and h2 >= h1:
            return round((h2 - h1) / (d2 - d1).days, 2)
    if len(pts) == 1 and commissioned_on and (pts[0][0] - commissioned_on).days > 0:
        return round(pts[0][1] / (pts[0][0] - commissioned_on).days, 2)
    return manual


def hours_now(readings: Sequence[tuple[date, float]], per_day: Optional[float], today: date) -> Optional[float]:
    pts = sorted(readings)
    if not pts:
        return None
    d, h = pts[-1]
    return round(h + (per_day or 0) * max((today - d).days, 0), 1)


def next_to(current: float, kits: Iterable[Mapping]) -> Optional[tuple[float, list[Mapping]]]:
    """Ближайшее ТО выше текущей наработки и комплекты, которые в него входят."""
    ks = [k for k in kits if float(k.get("interval_hours") or 0) > 0]
    if not ks:
        return None
    nxt = min(math.floor(current / float(k["interval_hours"]) + 1e-9) * float(k["interval_hours"]) + float(k["interval_hours"]) for k in ks)
    due = [k for k in ks if abs(nxt / float(k["interval_hours"]) - round(nxt / float(k["interval_hours"]))) < 1e-6]
    return nxt, sorted(due, key=lambda k: -float(k["interval_hours"]))


def due_date(current: float, target: float, per_day: Optional[float], today: date) -> Optional[date]:
    if not per_day or per_day <= 0:
        return None
    return today + timedelta(days=math.ceil(max(target - current, 0) / per_day))


def kit_lines(kits: Iterable[Mapping]) -> list[dict]:
    """Строки заявки по комплектам ТО: одинаковые позиции складываются."""
    out: dict[str, dict] = {}
    for k in kits:
        for l in k.get("lines") or []:
            key = l.get("part_id") or l.get("article")
            if not key:
                continue
            row = out.setdefault(key, {"part_id": l.get("part_id"), "article": l.get("article") or "", "name": l.get("name") or "", "qty": 0.0})
            row["qty"] += float(l.get("qty") or 1)
    return list(out.values())
