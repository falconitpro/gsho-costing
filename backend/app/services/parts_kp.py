"""КП по заявке на запчасти: снимок строк в валюте КП, «что изменилось», документ Word и мягкий резерв (ТЗ запчастей, раздел 5).

КП можно отправлять частями: каждая редакция включает выбранные строки. Срок действия и мягкий резерв — 30 дней
(настройка parts_kp_validity_days). В документ не попадают себестоимость и маржа — проверка assert_no_cost_leak.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .parts_pricing import to_rub


def lead_text(source: str, lead_days: Optional[int]) -> str:
    if source == "stock":
        return "в наличии"
    if lead_days:
        return f"{lead_days} дн."
    return "по запросу"


def build_kp_lines(lines: Iterable[Mapping[str, Any]], currency: str, fx: Mapping[str, float]) -> list[dict]:
    """Строки заявки (payload) → снимок КП: цена и сумма в валюте КП по курсу ЦБ."""
    rate = 1.0 if currency == "RUB" else fx.get(currency)
    if not rate:
        raise ValueError(f"Нет курса {currency} к рублю")
    out = []
    for l in lines:
        price_rub = to_rub(l["price"], l["currency"], fx)
        if price_rub is None:
            raise ValueError(f"Строка {l['no']}: нет курса {l['currency']}")
        price = round(price_rub / rate, 2) if l["currency"] != currency else round(float(l["price"]), 2)
        part = l.get("part") or {}
        out.append({"line_id": l["id"], "no": l["no"], "article": part.get("article") or l["raw_article"], "name": part.get("name_ru") or l["raw_name"],
                    "manufacturer": part.get("manufacturer") or "", "qty": l["qty"], "unit": l["unit"], "price": price,
                    "sum": round(price * float(l["qty"]), 2), "lead": lead_text(l.get("source") or "", l.get("lead_days"))})
    return out


def diff_kp(prev: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Что изменилось к прошлой редакции: добавлены / убраны строки, цена, количество, срок."""
    p = {x["line_id"]: x for x in prev}
    n = {x["line_id"]: x for x in new}
    out: list[dict] = []
    for lid, x in n.items():
        if lid not in p:
            out.append({"article": x["article"], "change": "добавлена"})
            continue
        for k, title in (("price", "цена"), ("qty", "количество"), ("lead", "срок")):
            if p[lid][k] != x[k]:
                out.append({"article": x["article"], "change": title, "old": p[lid][k], "new": x[k]})
    for lid, x in p.items():
        if lid not in n:
            out.append({"article": x["article"], "change": "убрана"})
    return out


def money(v: float, cur: str) -> str:
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + f" {cur}"


def render_docx(kp: Mapping[str, Any], client_name: str, path: str, company: str = "") -> str:
    """Документ КП по бренд-буку (общие элементы с КП ГШО)."""
    from docx import Document
    from docx.shared import Mm
    from docx.oxml.ns import qn
    from .kp_generator import BLACK, DEFAULT_LOGO, FONT, _heading, _run, _table, assert_no_cost_leak
    import os
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.font.color.rgb = BLACK
    if os.path.exists(DEFAULT_LOGO):
        try:
            doc.add_picture(DEFAULT_LOGO, width=Mm(45))
        except Exception:   # pragma: no cover
            pass
    _heading(doc, f"Коммерческое предложение {kp['number']}", 14)
    p = doc.add_paragraph()
    _run(p, f"Дата: {kp['date']}. Действительно до {kp['valid_until']}.")
    p = doc.add_paragraph()
    _run(p, f"Клиент: {client_name}")
    if kp.get("machine"):
        p = doc.add_paragraph()
        _run(p, f"Техника: {kp['machine']}")
    cur = kp["currency"]
    rows = [[str(i), l["article"], l["name"] + (f" ({l['manufacturer']})" if l.get("manufacturer") else ""), f"{l['qty']:g} {l['unit']}",
             money(l["price"], cur), money(l["sum"], cur), l["lead"]] for i, l in enumerate(kp["lines"], start=1)]
    if kp.get("handling_fee"):
        rows.append([str(len(rows) + 1), "", "Обработка заказа", "1", money(kp["handling_fee"], cur), money(kp["handling_fee"], cur), ""])
    rows.append(["", "", "Итого", "", "", money(kp["total"], cur), ""])
    _table(doc, ["№", "Артикул", "Наименование", "Кол-во", "Цена", "Сумма", "Срок поставки"], rows, accent_last_row=True)
    doc.add_paragraph()
    notes = ["Цены указаны с учётом условий договора поставки."]
    if cur != "RUB" and kp.get("fx_rate"):
        rate = f"{kp['fx_rate']:.4f}".replace(".", ",")
        notes.append(f"Цены в {cur}; оплата в рублях по курсу ЦБ РФ на дату оплаты. Курс на дату КП: {rate} ₽.")
    notes.append("Срок «в наличии» — товар на складе, резерв на срок действия предложения. Остальные сроки — с даты оплаты.")
    for n in notes:
        p = doc.add_paragraph()
        _run(p, n, size=9)
    if company:
        p = doc.add_paragraph()
        _run(p, company, size=9)
    doc.save(path)
    assert_no_cost_leak(path)
    return path


# ------------------------------------------------------------------ мягкий резерв (БД)

def release_expired(db, today: Optional[date] = None) -> int:
    """Снимает мягкие резервы с истёкшим сроком КП и пересчитывает зарезервированное по складам."""
    from sqlalchemy import select
    from ..models import PartReservation
    today = today or date.today()
    rows = db.scalars(select(PartReservation).where(PartReservation.released_at.is_(None), PartReservation.kind == "soft",
                                                   PartReservation.expires_at < today)).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.released_at = now
    if rows:
        db.flush()
        recompute_reserved(db, {r.part_id for r in rows})
    return len(rows)


def recompute_reserved(db, part_ids: Iterable[str]) -> None:
    from sqlalchemy import func, select
    from ..models import PartReservation, PartStock
    for pid in set(part_ids):
        sums = dict(db.execute(select(PartReservation.warehouse_id, func.sum(PartReservation.qty))
                               .where(PartReservation.part_id == pid, PartReservation.released_at.is_(None))
                               .group_by(PartReservation.warehouse_id)).all())
        for st in db.scalars(select(PartStock).where(PartStock.part_id == pid)).all():
            st.reserved = float(sums.get(st.warehouse_id) or 0)
    db.flush()


def reserve_soft(db, line, kp_id: str, until: date) -> float:
    """Мягкий резерв под строку «со склада»: прежний резерв строки снимается, новый ставится со складов с наибольшим свободным остатком.
    Возвращает зарезервированное количество (может быть меньше заказанного, если свободного остатка не хватает)."""
    from sqlalchemy import select
    from ..models import PartReservation, PartStock
    now = datetime.now(timezone.utc)
    for r in db.scalars(select(PartReservation).where(PartReservation.line_id == line.id, PartReservation.released_at.is_(None),
                                                      PartReservation.kind == "soft")).all():
        r.released_at = now
    db.flush()
    recompute_reserved(db, [line.part_id])
    need = float(line.qty or 0)
    got = 0.0
    stock = sorted(db.scalars(select(PartStock).where(PartStock.part_id == line.part_id)).all(),
                   key=lambda s: (s.qty or 0) - (s.reserved or 0), reverse=True)
    for s in stock:
        free = (s.qty or 0) - (s.reserved or 0)
        if free <= 0 or got >= need:
            continue
        q = min(free, need - got)
        db.add(PartReservation(part_id=line.part_id, warehouse_id=s.warehouse_id, line_id=line.id, kp_id=kp_id, qty=q, kind="soft", expires_at=until))
        got += q
    db.flush()
    recompute_reserved(db, [line.part_id])
    return got
