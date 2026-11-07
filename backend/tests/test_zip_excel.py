"""ЗИП из Excel: разбор файла (шаблон и «свободные» заголовки) и расчёт перечня на заданную наработку."""
from __future__ import annotations

import os
import tempfile

from openpyxl import Workbook

from app.api.model_docs import parse_zip_xlsx, zip_for_hours


def _xlsx(rows: list[list]) -> str:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def test_parse_template_like():
    path = _xlsx([
        ["ЗИП — SHL-4000"],
        ["Вид (поставка / ТО)", "Узел / система", "Артикул", "Наименование", "Ед.", "Кол-во на 1 замену", "Цена", "Валюта", "Периодичность замены, м·ч", "Срок поставки, дн", "Комментарий"],
        ["в комплекте поставки", "Гидросистема", "HF-1001", "Фильтр гидравлический", "шт", 1, "350,5", "cny", None, None, ""],
        ["ЗИП на ТО", "Двигатель", "OF-2201", "Фильтр масляный", "шт", 2, 120, "CNY", 500, 30, "каждые 500"],
        ["ТО", "", "", "Ремень", "шт", 1, None, "", None, None, ""],     # без периодичности — ошибка
        [None] * 11,
        ["мусор после пустой строки"],
    ])
    items, errors = parse_zip_xlsx(path)
    os.unlink(path)
    assert [i["article"] or i["name"] for i in items] == ["HF-1001", "OF-2201", "Ремень"]
    assert items[0]["kind"] == "supply" and items[0]["price"] == 350.5 and items[0]["currency"] == "CNY"
    assert items[1]["kind"] == "to" and items[1]["interval_hours"] == 500 and items[1]["lead_days"] == 30
    assert len(errors) == 1 and "Ремень" in errors[0]


def test_parse_no_header():
    path = _xlsx([["a", "b"], [1, 2]])
    items, errors = parse_zip_xlsx(path)
    os.unlink(path)
    assert items == [] and "заголовков" in errors[0]


def test_zip_for_hours():
    items = [
        {"kind": "supply", "article": "A", "qty": 1, "price": 100, "currency": "CNY"},
        {"kind": "to", "article": "B", "qty": 2, "price": 10, "currency": "CNY", "interval_hours": 500},
        {"kind": "to", "article": "C", "qty": 1, "price": 5, "currency": "USD", "interval_hours": 3000},   # не попадает в 2000 ч
        {"kind": "to", "article": "D", "qty": 1, "price": None, "interval_hours": 1000},
    ]
    rows, total = zip_for_hours(items, 2000)
    by = {r["article"]: r for r in rows}
    assert set(by) == {"A", "B", "D"}
    assert by["B"]["replacements"] == 4 and by["B"]["qty_period"] == 8 and by["B"]["amount"] == 80
    assert by["A"]["replacements"] == 1 and by["D"]["amount"] is None
    assert total == {"CNY": 180}
