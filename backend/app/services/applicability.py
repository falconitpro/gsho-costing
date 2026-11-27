"""Применимость запчастей (J-b, D-196): статусы проверки, основание, диапазон серийных номеров, подбор к машине.

Статус говорит, насколько можно доверять связи «деталь подходит к модели»:
- confirmed — подтверждено документом (каталог завода, письмо поставщика, опыт сервиса); нужно основание;
- probable — вероятно (аналог, данные похожей модели), заказывать с проверкой;
- unverified — не подтверждено: так помечены все связи, заведённые до J-b (решение владельца 7);
- not_applicable — проверено, не подходит; в подбор к машине не попадает, но остаётся как знание;
- replaced — деталь заменена другой (номер замены — в кросс-номерах); в подбор попадает с пометкой.
"""
from __future__ import annotations

import re
from typing import Optional

STATUSES = {"confirmed": "Подтверждено", "probable": "Вероятно", "unverified": "Не подтверждено",
            "not_applicable": "Не подходит", "replaced": "Заменена"}
BASIS_KINDS = {"manual": "Каталог / руководство завода", "supplier": "Поставщик", "service": "Опыт сервиса",
               "analog": "По аналогии", "comment": "Комментарий"}
NEED_BASIS = {"confirmed", "not_applicable", "replaced"}      # без основания эти статусы не ставятся
ORDER = {"confirmed": 0, "replaced": 1, "probable": 2, "unverified": 3, "not_applicable": 9}

_TAIL = re.compile(r"^(.*?)(\d+)$")


def _key(serial: str) -> tuple:
    """Ключ сравнения серийного номера: префикс и число в конце сравниваются отдельно (BH-099 < BH-1000)."""
    s = (serial or "").strip().upper().replace(" ", "")
    m = _TAIL.match(s)
    return (m.group(1), int(m.group(2))) if m else (s, -1)


def serial_in_range(serial: str, frm: str, to: str) -> Optional[bool]:
    """True/False — входит ли номер в диапазон; None — проверить нельзя (номер машины не известен или диапазон пуст,
    или префиксы разные). None не исключает деталь из подбора, а помечается «проверьте номер»."""
    if not (frm or to):
        return True
    if not (serial or "").strip():
        return None
    k = _key(serial)
    lo, hi = (_key(frm) if frm else None), (_key(to) if to else None)
    for b in (lo, hi):
        if b is not None and b[0] != k[0]:
            return None
    if lo is not None and k < lo:
        return False
    if hi is not None and k > hi:
        return False
    return True


def parts_for_machine(db, machine, model_text: str, q: str = "", limit: int = 300) -> list[dict]:
    """Запчасти, применимые к машине: по модели каталога ГШО или модели другого производителя.
    «Не подходит» и номера вне диапазона исключаются; по каждой детали — лучший статус из её связей."""
    from sqlalchemy import or_, select

    from ..models import Part, PartApplicability
    conds = []
    if machine.equipment_model_id:
        conds.append(PartApplicability.equipment_model_id == machine.equipment_model_id)
    if model_text:
        conds.append(PartApplicability.other_model.ilike(model_text))
    if not conds:
        return []
    rows = db.execute(select(Part, PartApplicability).join(PartApplicability, PartApplicability.part_id == Part.id)
                      .where(or_(*conds), Part.status == "active")).all()
    best: dict[str, dict] = {}
    for p, a in rows:
        if a.status == "not_applicable":
            continue
        rng = serial_in_range(machine.serial, a.serial_from, a.serial_to)
        if rng is False:
            continue
        if q and q.lower() not in f"{p.article} {p.name_ru} {a.assembly}".lower():
            continue
        row = {"id": p.id, "article": p.article, "name_ru": p.name_ru, "assembly": a.assembly, "status": a.status,
               "status_title": STATUSES.get(a.status, a.status), "serial_check": rng is None and bool(a.serial_from or a.serial_to),
               "serial_range": " — ".join(x for x in (a.serial_from, a.serial_to) if x),
               "stock_total": sum((s.qty or 0) - (s.reserved or 0) for s in p.stock)}
        cur = best.get(p.id)
        if cur is None or ORDER.get(a.status, 5) < ORDER.get(cur["status"], 5):
            best[p.id] = row
    return sorted(best.values(), key=lambda x: (ORDER.get(x["status"], 5), x["assembly"], x["article"]))[:limit]
