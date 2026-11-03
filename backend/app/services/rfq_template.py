"""Работа с файлом шаблона RFQ компании (backend/app/templates/rfq_template.xlsx).

`fill_rfq_template` заполняет копию шаблона значениями «от завода» (колонка «Значение (рус.)», лист ЗИП) —
используется в тестах и для демо-файлов. `template_path` отдаёт актуальный шаблон (загруженный администратором
через настройки или встроенный).
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional

from openpyxl import load_workbook

from .rfq_import import _find_main_header, _norm

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
DEFAULT_TEMPLATE = os.path.join(TEMPLATES_DIR, "rfq_template.xlsx")


def template_path(custom: Optional[str] = None) -> str:
    return custom if custom and os.path.exists(custom) else DEFAULT_TEMPLATE


def fill_rfq_template(dst: str, values: dict[str, Any], spare_parts: Optional[list[dict]] = None,
                      comments: Optional[dict[str, str]] = None, option_names: Optional[dict[str, str]] = None,
                      src: Optional[str] = None) -> str:
    """values: код → значение (пишется в «Значение (рус.)»); option_names: код 12.x → наименование опции;
    spare_parts: список {name|row, qty_per_service, interval_hours, unit_price, part_no, brand} по листу ЗИП."""
    shutil.copyfile(template_path(src), dst)
    wb = load_workbook(dst)
    ws = next((w for w in wb.worksheets if "тех" in w.title.lower()), wb.worksheets[0])
    hdr, cols, _ = _find_main_header(ws)
    rows_by_code: dict[str, int] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        c = ws.cell(r, cols["code"]).value
        if c is not None:
            rows_by_code[str(c).strip()] = r
    def put(r: int, c: int, val: Any) -> None:
        """Запись с учётом объединённых ячеек (у строк 12.x «Значение» объединено по CN/RU)."""
        for rng in ws.merged_cells.ranges:
            if rng.min_row <= r <= rng.max_row and rng.min_col <= c <= rng.max_col:
                ws.cell(rng.min_row, rng.min_col).value = val
                return
        ws.cell(r, c).value = val

    for code, val in values.items():
        r = rows_by_code.get(code)
        if r:
            put(r, cols["value_ru"], val)
    for code, name in (option_names or {}).items():
        r = rows_by_code.get(code)
        if r:
            put(r, cols["name_ru"], name)
    for code, text in (comments or {}).items():
        r = rows_by_code.get(code)
        if r and "comment" in cols:
            put(r, cols["comment"], text)

    if spare_parts:
        z = next((w for w in wb.worksheets if "зип" in w.title.lower()), None)
        if z is not None:
            # заголовки
            hrow, zc = None, {}
            for r in range(1, 20):
                cells = {c: _norm(z.cell(r, c).value).replace("\n", " ") for c in range(1, 16)}
                if any(v.startswith("наименование") for v in cells.values()) and any(v.startswith("цена") for v in cells.values()):
                    hrow = r
                    for c, v in cells.items():
                        if v.startswith("наименование"): zc["name"] = c
                        elif v.startswith("каталожный"): zc["part_no"] = c
                        elif v.startswith("производитель"): zc["brand"] = c
                        elif v.startswith("кол-во на"): zc["qty"] = c
                        elif v.startswith("периодичность"): zc["interval"] = c
                        elif v.startswith("цена"): zc["price"] = c
                        elif v.startswith("наличие"): zc["lead"] = c
                    break
            if hrow:
                name_rows = {_norm(z.cell(r, zc["name"]).value).split(" / ")[0]: r for r in range(hrow + 1, z.max_row + 1) if z.cell(r, zc["name"]).value}
                for sp in spare_parts:
                    r = sp.get("row") or name_rows.get(_norm(sp.get("name", "")).split(" / ")[0])
                    if not r:
                        continue
                    for key, col in (("qty_per_service", "qty"), ("interval_hours", "interval"), ("unit_price", "price"),
                                     ("part_no", "part_no"), ("brand", "brand"), ("lead_days", "lead")):
                        if key in sp and col in zc:
                            z.cell(r, zc[col]).value = sp[key]
    wb.save(dst)
    return dst


LOCAL_RU_NOTE = "Реализуется в России — не заполнять / 在俄罗斯实现，无需填写"


def fill_rfq_requirements(dst: str, rows: list[dict], src: Optional[str] = None) -> str:
    """Задача 4: RFQ на основе таблицы позиции — требования заказчика (RU/CN) по кодам; строки «в РФ» помечены и не заполняются заводом."""
    shutil.copyfile(template_path(src), dst)
    wb = load_workbook(dst)
    ws = next((w for w in wb.worksheets if "тех" in w.title.lower()), wb.worksheets[0])
    hdr, cols, _ = _find_main_header(ws)
    rows_by_code: dict[str, int] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        c = ws.cell(r, cols["code"]).value
        if c is not None:
            rows_by_code[str(c).strip()] = r

    def put(r: int, c: Optional[int], val: Any) -> None:
        if not c or val in (None, ""):
            return
        for rng in ws.merged_cells.ranges:
            if rng.min_row <= r <= rng.max_row and rng.min_col <= c <= rng.max_col:
                ws.cell(rng.min_row, rng.min_col).value = val
                return
        ws.cell(r, c).value = val

    for row in rows:
        r = rows_by_code.get(str(row.get("code") or "").strip())
        if not r:
            continue
        if row.get("local_ru"):
            put(r, cols.get("req_ru"), LOCAL_RU_NOTE)
            put(r, cols.get("comment"), LOCAL_RU_NOTE)
            continue
        put(r, cols.get("req_ru"), row.get("customer_requirement_ru"))
        put(r, cols.get("req_zh"), row.get("customer_requirement_zh"))
    wb.save(dst)
    return dst
