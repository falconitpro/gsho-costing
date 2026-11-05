"""Запросы цен поставщикам запчастей (ТЗ запчастей, раздел 7): язык по поставщику, файл запроса, разбор ответа, выбор.

Китай — RU + CN, Германия — EN/DE, Киргизия — RU. Номер для поставщика: его артикул из кроссов → OEM → наш артикул.
Наименование на языке поставщика: из карточки (EN/CN/DE) → переводчик (помечается «машинный перевод, проверить»).
Ответ — по строкам: наличие, цена, валюта, срок, минимальная партия или аналог. Частичный ответ принимается.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

LANG_BY_COUNTRY = {"CN": "zh", "DE": "de", "KG": "ru", "RU": "ru", "AT": "de", "CH": "de"}
CARD_KEY = {"zh": "cn", "de": "de", "en": "en"}          # ключ наименования в карточке запчасти (names)
LANG_TITLE = {"ru": "русский", "zh": "китайский", "de": "немецкий", "en": "английский"}
AVAILABILITY = {"available": "в наличии", "on_order": "под заказ", "none": "нет", "analog": "предлагают аналог"}

# заголовки файла запроса: русский + язык поставщика
HEADERS = {
    "no": {"ru": "№", "zh": "序号", "de": "Nr.", "en": "No."},
    "number": {"ru": "Артикул", "zh": "零件号", "de": "Artikelnummer", "en": "Part number"},
    "name_ru": {"ru": "Наименование", "zh": "名称（俄文）", "de": "Bezeichnung (RU)", "en": "Description (RU)"},
    "name_foreign": {"ru": "Наименование (перевод)", "zh": "名称", "de": "Bezeichnung", "en": "Description"},
    "qty": {"ru": "Кол-во", "zh": "数量", "de": "Menge", "en": "Qty"},
    "unit": {"ru": "Ед.", "zh": "单位", "de": "Einheit", "en": "Unit"},
    "availability": {"ru": "Наличие (есть / под заказ / нет / аналог)", "zh": "库存（有/订货/无/替代）", "de": "Verfügbarkeit (Lager / Bestellung / nein / Alternative)",
                     "en": "Availability (stock / order / no / alternative)"},
    "price": {"ru": "Цена за ед.", "zh": "单价", "de": "Einzelpreis", "en": "Unit price"},
    "currency": {"ru": "Валюта", "zh": "币种", "de": "Währung", "en": "Currency"},
    "lead_days": {"ru": "Срок поставки, дн", "zh": "交货期（天）", "de": "Lieferzeit, Tage", "en": "Lead time, days"},
    "moq": {"ru": "Мин. партия", "zh": "最小起订量", "de": "Mindestmenge", "en": "MOQ"},
    "analog_number": {"ru": "Номер аналога", "zh": "替代件号", "de": "Alternativnummer", "en": "Alternative part no."},
    "comment": {"ru": "Комментарий", "zh": "备注", "de": "Kommentar", "en": "Comment"},
}
COLUMNS = list(HEADERS)
ANSWER_COLUMNS = ["availability", "price", "currency", "lead_days", "moq", "analog_number", "comment"]
INTRO = {"ru": "Просим указать наличие, цену, валюту, срок поставки и минимальную партию по каждой строке. Если позиции нет — предложите аналог.",
         "zh": "请逐项填写库存、单价、币种、交货期和最小起订量。如无此件，请提供替代件。",
         "de": "Bitte geben Sie für jede Position Verfügbarkeit, Preis, Währung, Lieferzeit und Mindestmenge an. Falls nicht lieferbar, bieten Sie bitte eine Alternative an.",
         "en": "Please state availability, unit price, currency, lead time and MOQ for each line. If an item is not available, please offer an alternative."}
AVAIL_WORDS = {"available": ("есть", "в наличии", "stock", "lager", "有", "现货", "yes", "ja"),
               "on_order": ("под заказ", "order", "bestellung", "订货", "期货"),
               "none": ("нет", "no", "nein", "无", "没有"),
               "analog": ("аналог", "alternative", "替代")}


def supplier_language(country: str, language: str = "") -> str:
    if language in ("ru", "zh", "de", "en"):
        return language
    if language == "cn":
        return "zh"
    return LANG_BY_COUNTRY.get((country or "").upper(), "en")


def parse_availability(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return ""
    for key, words in AVAIL_WORDS.items():
        if any(s.startswith(w) or s == w for w in words):
            return key
    return ""


def _num(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v)).replace(",", ".")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def build_rfq_xlsx(path: str, number: str, supplier_name: str, lang: str, lines: Iterable[dict], due: str = "") -> str:
    """Файл запроса: шапка, просьба, таблица на двух языках, столбцы ответа выделены цветом."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "RFQ"
    two = lang != "ru"
    ws.append([f"Запрос цены {number}" + (f" / RFQ {number}" if two else "")])
    ws.append([f"Поставщик: {supplier_name}"] + ([f"Ответ до: {due}"] if due else []))
    ws.append([INTRO["ru"]])
    if two:
        ws.append([INTRO[lang]])
    ws.append([])
    head = [HEADERS[c]["ru"] + (f"\n{HEADERS[c][lang]}" if two else "") for c in COLUMNS if two or c != "name_foreign"]
    ws.append(head)
    hdr_row = ws.max_row
    cols = [c for c in COLUMNS if two or c != "name_foreign"]
    fill = PatternFill("solid", fgColor="FFF2CC")
    for i, c in enumerate(cols, start=1):
        cell = ws.cell(row=hdr_row, column=i)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if c in ANSWER_COLUMNS:
            cell.fill = fill
    for l in lines:
        ws.append([l.get(c) if c not in ANSWER_COLUMNS else None for c in cols])
    for i, w in enumerate([6, 18, 36, 36, 8, 7, 22, 12, 9, 12, 11, 18, 30][:len(cols)], start=1):
        ws.column_dimensions[ws.cell(row=hdr_row, column=i).column_letter].width = w
    wb.save(path)
    return path


def parse_rfq_answer(rows: list[tuple]) -> dict[int, dict]:
    """Заполненный поставщиком файл запроса → ответы по номеру строки. Строки без ответа пропускаются."""
    hdr_i = next((i for i, r in enumerate(rows[:20]) if r and str(r[0] or "").split("\n")[0].strip() == "№"), None)
    if hdr_i is None:
        raise ValueError("Это не файл запроса цены: нет строки заголовка «№»")
    names = [str(h or "").split("\n")[0].strip() for h in rows[hdr_i]]
    by_title = {HEADERS[c]["ru"]: c for c in COLUMNS}
    idx = {by_title[n]: i for i, n in enumerate(names) if n in by_title}
    out: dict[int, dict] = {}
    for r in rows[hdr_i + 1:]:
        no = _num(r[idx["no"]]) if "no" in idx and idx["no"] < len(r) else None
        if not no:
            continue
        g = lambda c: r[idx[c]] if c in idx and idx[c] < len(r) else None   # noqa: E731
        ans = {"availability": parse_availability(g("availability")), "price": _num(g("price")),
               "currency": str(g("currency") or "").strip().upper()[:3], "lead_days": int(_num(g("lead_days")) or 0) or None,
               "moq": _num(g("moq")), "analog_number": str(g("analog_number") or "").strip(), "comment": str(g("comment") or "").strip()}
        if ans["price"] and not ans["availability"]:
            ans["availability"] = "analog" if ans["analog_number"] else "available"
        if any(v for k, v in ans.items() if k != "currency"):
            out[int(no)] = ans
    return out


def rfq_status(lines: Iterable[dict]) -> str:
    lines = list(lines)
    answered = sum(1 for l in lines if l.get("availability"))
    if not answered:
        return ""
    return "answered" if answered == len(lines) else "partial"
