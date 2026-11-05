"""Заказы поставщикам запчастей и внутригрупповые заказы по маршруту (ТЗ запчастей, раздел 7).

Заказ поставщику оформляет компания группы, которая покупает на первой ступени маршрута. На каждой следующей ступени
система формирует внутригрупповой заказ по цене цепочки наценок (markup_chain.simulate). Статус поставки ведётся
по строке внешнего заказа и переносится в строку заявки клиента вместе с ожидаемой датой на складе РФ.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional

from .parts_supplier import HEADERS as RFQ_HEADERS

PO_LINE_STATUS = {
    "ordered": "заказано", "confirmed": "подтверждено", "shipped": "отгружено поставщиком", "consolidation": "на консолидации",
    "transit": "в пути", "customs": "таможня", "received": "на складе", "cancelled": "отменено",
}
PO_LINE_FLOW = ["ordered", "confirmed", "shipped", "consolidation", "transit", "customs", "received"]
PO_STATUS = {"draft": "черновик", "sent": "отправлен поставщику", "confirmed": "подтверждён", "in_progress": "в поставке",
             "closed": "поставлен", "cancelled": "отменён"}
# статус строки заявки клиента по статусу строки заказа
REQUEST_STATUS = {"ordered": "po", "confirmed": "po", "shipped": "transit", "consolidation": "transit", "transit": "transit",
                  "customs": "transit", "received": "in_stock"}
ORDERABLE = {"quoted", "in_kp", "ordered", "replaced"}       # строки заявки, которые можно заказать у поставщика


def eta(start: date, lead_days: Optional[int], transit_days: int = 0) -> date:
    return start + timedelta(days=int(lead_days or 0) + int(transit_days or 0))


def po_status(current: str, line_statuses: Iterable[str]) -> str:
    """Статус заказа по строкам: все отменены → отменён; все получены (или отменены) → поставлен; что-то в пути → в поставке."""
    st = [s for s in line_statuses]
    if not st or current in ("draft", "cancelled"):
        return current
    live = [s for s in st if s != "cancelled"]
    if not live:
        return "cancelled"
    if all(s == "received" for s in live):
        return "closed"
    if any(PO_LINE_FLOW.index(s) >= PO_LINE_FLOW.index("shipped") for s in live):
        return "in_progress"
    if all(s == "confirmed" for s in live) or current == "confirmed":
        return "confirmed"
    return current


def can_move(old: str, new: str) -> bool:
    """Статус поставки идёт вперёд (можно пропустить шаги) или отменяется; полученную строку не отменить."""
    if new == old:
        return True
    if new == "cancelled":
        return old != "received"
    if old == "cancelled" or new not in PO_LINE_FLOW:
        return False
    return PO_LINE_FLOW.index(new) > PO_LINE_FLOW.index(old)


def intragroup_steps(sim: Mapping[str, Any]) -> list[dict]:
    """Ступени 2…N цепочки: продавец и покупатель — компании группы, цена единицы в валюте ступени."""
    return [{"no": s["no"], "seller_entity_id": s["seller_entity_id"], "buyer_entity_id": s["buyer_entity_id"], "currency": s["currency"],
             "incoterms": s.get("incoterms") or "", "price": s["price"]} for s in sim["steps"][1:]]


PO_HEADERS = {
    "no": RFQ_HEADERS["no"], "number": RFQ_HEADERS["number"], "name": RFQ_HEADERS["name_ru"], "name_foreign": RFQ_HEADERS["name_foreign"],
    "qty": RFQ_HEADERS["qty"], "unit": RFQ_HEADERS["unit"], "price": RFQ_HEADERS["price"],
    "sum": {"ru": "Сумма", "zh": "金额", "de": "Betrag", "en": "Amount"},
}
PO_TITLE = {"ru": "Заказ", "zh": "订单", "de": "Bestellung", "en": "Purchase order"}
PO_INTRO = {"ru": "Просим подтвердить заказ, дату отгрузки и выставить счёт.",
            "zh": "请确认订单和发货日期，并开具发票。",
            "de": "Bitte bestätigen Sie die Bestellung und den Liefertermin und senden Sie uns die Rechnung.",
            "en": "Please confirm the order and the shipment date and send us your invoice."}


def build_po_xlsx(path: str, *, number: str, seller: str, buyer: str, lang: str, currency: str, incoterms: str,
                  ship_date: str, lines: Iterable[Mapping[str, Any]]) -> str:
    """Заказ поставщику или внутригрупповой заказ: шапка, таблица на русском и языке поставщика, итог."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook()
    ws = wb.active
    ws.title = "PO"
    two = lang != "ru"
    ws.append([f"{PO_TITLE['ru']} {number}" + (f" / {PO_TITLE[lang]} {number}" if two else "")])
    ws.append([f"Продавец: {seller}"])
    ws.append([f"Покупатель: {buyer}"])
    ws.append([f"Валюта: {currency}" + (f" · Incoterms: {incoterms}" if incoterms else "") + (f" · Отгрузка до: {ship_date}" if ship_date else "")])
    ws.append([PO_INTRO["ru"]])
    if two:
        ws.append([PO_INTRO[lang]])
    ws.append([])
    cols = [c for c in PO_HEADERS if two or c != "name_foreign"]
    ws.append([PO_HEADERS[c]["ru"] + (f"\n{PO_HEADERS[c][lang]}" if two else "") for c in cols])
    hdr = ws.max_row
    for i in range(1, len(cols) + 1):
        ws.cell(row=hdr, column=i).font = Font(bold=True)
        ws.cell(row=hdr, column=i).alignment = Alignment(wrap_text=True, vertical="top")
    total = 0.0
    for l in lines:
        s = round(float(l["price"] or 0) * float(l["qty"] or 0), 2)
        total += s
        row = {**l, "sum": s}
        ws.append([row.get(c) for c in cols])
    ws.append([None] * (len(cols) - 2) + ["Итого" + (" / Total" if two else ""), round(total, 2)])
    ws.cell(row=ws.max_row, column=len(cols)).font = Font(bold=True)
    for i, w in enumerate([6, 18, 36, 36, 8, 7, 12, 14][:len(cols)], start=1):
        ws.column_dimensions[ws.cell(row=hdr, column=i).column_letter].width = w
    wb.save(path)
    return path
