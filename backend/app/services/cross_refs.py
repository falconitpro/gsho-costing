"""Кроссировки номеров (D-202): «номер завода-изготовителя машины ↔ OEM-номер производителя детали».

Карточка детали в каталоге объединяет все номера одной и той же детали: наш артикул, OEM, номер завода машины,
артикулы поставщиков, аналоги. Кроссировка = два номера на одной карточке. Номера сравниваются нормализованно
(без пробелов, дефисов, регистра — как в поиске J-a).

Импорт таблицы (Excel): по строке — производитель машины, номер завода, производитель детали, OEM, наименование.
- оба номера найдены на одной карточке — уже есть;
- найден один — второй добавляется к этой карточке («не подтверждено», источник — импорт, файл);
- номера найдены на разных карточках — конфликт, ничего не меняется (решает человек);
- не найден ни один — новая карточка (только с create=True), артикул — номер завода машины.
Пробный прогон по умолчанию. Тот же путь позже займёт ИИ (источник «ai») — утверждает всегда человек.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Part, PartCrossRef
from .catalog_search import norm, norm_sql

HEAD = {"производитель машины": "mm", "завод машины": "mm", "номер завода": "mm_no", "номер завода машины": "mm_no", "номер машины": "mm_no",
        "производитель детали": "oem_maker", "производитель": "oem_maker", "oem": "oem_no", "oem номер": "oem_no", "oem-номер": "oem_no",
        "наименование": "name", "название": "name", "комментарий": "comment"}


def find_parts(db: Session, number: str) -> set[str]:
    """Карточки, где есть этот номер: артикул, OEM или любой номер из кросс-номеров."""
    n = norm(number)
    if not n:
        return set()
    ids = set(db.scalars(select(Part.id).where(norm_sql(Part.article) == n)).all())
    ids |= set(db.scalars(select(Part.id).where(norm_sql(Part.oem_number) == n)).all())
    ids |= set(db.scalars(select(PartCrossRef.part_id).where(norm_sql(PartCrossRef.number) == n)).all())
    return ids


def has_number(p: Part, number: str) -> bool:
    n = norm(number)
    return n in {norm(p.article), norm(p.oem_number)} | {norm(c.number) for c in p.cross_refs}


def parse(rows: list[tuple]) -> tuple[list[dict], Optional[str]]:
    if not rows:
        return [], "Файл пустой"
    head = [HEAD.get(str(h or "").strip().lower()) for h in rows[0]]
    if "mm_no" not in head or "oem_no" not in head:
        return [], "Нужны колонки «Номер завода машины» и «OEM номер» (а также, по желанию: «Производитель машины», «Производитель детали», «Наименование»)"
    out = []
    for i, r in enumerate(rows[1:], start=2):
        d = {k: str(v).strip() if v is not None else "" for k, v in zip(head, r) if k}
        if d.get("mm_no") or d.get("oem_no"):
            d["row"] = i
            out.append(d)
    return out, None


def apply_rows(db: Session, rows: list[dict], user_id: str, source_ref: str, create: bool, dry_run: bool) -> dict:
    rep = {"rows": len(rows), "exists": 0, "added": 0, "created": 0, "conflicts": [], "skipped": [], "lines": []}
    for d in rows:
        mm_no, oem_no = d.get("mm_no", ""), d.get("oem_no", "")
        a, b = find_parts(db, mm_no), find_parts(db, oem_no)
        both = a | b
        line = {"row": d["row"], "machine_number": mm_no, "oem_number": oem_no}
        if len(both) > 1:
            rep["conflicts"].append({**line, "reason": "номера уже на разных карточках"})
            continue
        if not both:
            if not create:
                rep["skipped"].append({**line, "reason": "нет карточки ни с одним номером"})
                continue
            if not mm_no:
                rep["skipped"].append({**line, "reason": "нет номера завода машины для новой карточки"})
                continue
            line["action"] = "новая карточка"
            rep["created"] += 1
            if not dry_run:
                p = Part(article=mm_no[:64], name_ru=(d.get("name") or mm_no)[:255], manufacturer=d.get("mm", "")[:255], unit="шт", status="active")
                db.add(p)
                db.flush()
                _add(db, p, "machine_maker", mm_no, d.get("mm", ""), user_id, source_ref)
                if oem_no:
                    _add(db, p, "oem", oem_no, d.get("oem_maker", ""), user_id, source_ref)
            rep["lines"].append(line)
            continue
        p = db.get(Part, next(iter(both)))
        todo = [(k, n, mk) for k, n, mk in (("machine_maker", mm_no, d.get("mm", "")), ("oem", oem_no, d.get("oem_maker", ""))) if n and not has_number(p, n)]
        if not todo:
            rep["exists"] += 1
            continue
        line.update(action=f"добавить к {p.article}: " + ", ".join(n for _k, n, _m in todo), part_id=p.id)
        rep["added"] += len(todo)
        if not dry_run:
            for k, n, mk in todo:
                _add(db, p, k, n, mk, user_id, source_ref)
        rep["lines"].append(line)
    rep["lines"] = rep["lines"][:300]
    return rep


def _add(db: Session, p: Part, kind: str, number: str, maker: str, user_id: str, source_ref: str) -> None:
    db.add(PartCrossRef(part_id=p.id, kind=kind, number=number.strip()[:128], manufacturer=maker.strip()[:255], status="unverified",
                        source="import", source_ref=source_ref[:255]))
    db.flush()
