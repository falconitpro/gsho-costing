"""Шаблон технических характеристик ↔ Excel (D-070).

Выгрузка: лист «Шаблон» — колонки «Блок | Код | Параметр | 参数 (CN) | Ед. | Обязательный», строка на параметр.
Загрузка: тот же формат или любой свой — колонки ищутся по заголовкам (блок/раздел/группа, код, параметр/наименование/
характеристика, китайский, единица, обязательный). Блоки распознаются двумя способами: колонкой «Блок» или строками-
заголовками (в строке заполнено только название, и оно выделено жирным, или вида «2. Двигатель», или объединено на
несколько колонок). Если заголовков нет вовсе — первая колонка с текстом считается параметром, следующая — единицей.
"""
from __future__ import annotations

import re
from typing import Any, Optional

HEADERS = {
    "section": ("блок", "раздел", "группа", "section", "block", "group", "分组", "部分"),
    "code": ("код", "code", "编码", "代码"),
    "name_ru": ("параметр", "наименование", "характеристика", "показатель", "name", "parameter"),
    "name_zh": ("参数", "中文", "китай", "cn", "chinese", "名称"),
    "unit": ("ед", "единиц", "unit", "单位"),
    "required": ("обяз", "required", "必填", "必须"),
}
EXPORT_COLUMNS = [("section", "Блок"), ("code", "Код"), ("name_ru", "Параметр"), ("name_zh", "参数 (CN)"), ("unit", "Ед."), ("required", "Обязательный")]
YES = {"да", "yes", "y", "1", "true", "+", "x", "х", "✓", "✔", "v", "是", "обязательно", "обязательный"}
NUMBERED = re.compile(r"^\s*\d{1,2}\s*[.)]\s*\S")


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def _find_header(ws, max_scan: int = 15) -> tuple[Optional[int], dict[str, int]]:
    best: tuple[Optional[int], dict[str, int]] = (None, {})
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True), 1):
        cols: dict[str, int] = {}
        for c_idx, v in enumerate(row):
            t = _s(v).lower()
            if not t or len(t) > 40:
                continue
            for key, words in HEADERS.items():
                if key not in cols and any(t.startswith(w) or w in t for w in words):
                    # «параметр (CN)» / «参数» — китайское название, а не русское
                    if key == "name_ru" and any(w in t for w in HEADERS["name_zh"]):
                        continue
                    cols[key] = c_idx
                    break
        if "name_ru" in cols and len(cols) > len(best[1]):
            best = (r_idx, cols)
    return best


def parse(path: str) -> dict:
    """Строки шаблона из файла Excel: [{section, code, name_ru, name_zh, unit, required}], число блоков, замечания."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    ws = wb["Шаблон"] if "Шаблон" in wb.sheetnames else wb.worksheets[0]
    hdr, cols = _find_header(ws)
    warnings: list[str] = []
    if hdr is None:   # без заголовков: первая колонка с текстом — параметр, следующая — единица
        first = None
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 50), values_only=True):
            for c_idx, v in enumerate(row):
                if _s(v) and not isinstance(v, (int, float)):
                    first = c_idx if first is None else min(first, c_idx)
                    break
        cols = {"name_ru": first or 0, "unit": (first or 0) + 1}
        hdr = 0
        warnings.append("Заголовки колонок не найдены: первая колонка с текстом — параметр, следующая — единица измерения.")
    merged_rows = {rng.min_row for rng in ws.merged_cells.ranges if rng.max_col > rng.min_col}
    rows: list[dict] = []
    section = ""
    has_section_col = "section" in cols
    for r_idx in range(hdr + 1, ws.max_row + 1):
        cells = [ws.cell(row=r_idx, column=c + 1) for c in range(ws.max_column)]
        get = lambda k: _s(cells[cols[k]].value) if k in cols and cols[k] < len(cells) else ""   # noqa: E731
        name = get("name_ru")
        others = [get(k) for k in ("code", "name_zh", "unit", "required")]
        if has_section_col and get("section"):
            section = get("section")
        if not name:
            # строка-заголовок блока может стоять в первой колонке, а не в колонке параметра
            texts = [_s(c.value) for c in cells if _s(c.value)]
            if len(texts) == 1 and not has_section_col and (NUMBERED.match(texts[0]) or r_idx in merged_rows
                                                          or any(c.font and c.font.bold for c in cells if _s(c.value))):
                section = texts[0]
            continue
        cell = cells[cols["name_ru"]]
        is_header = not any(others) and not has_section_col and (
            bool(cell.font and cell.font.bold) or bool(NUMBERED.match(name)) or r_idx in merged_rows)
        if is_header:
            section = name
            continue
        rows.append({"section": section[:255], "code": get("code")[:16], "name_ru": name[:512], "name_zh": get("name_zh")[:512],
                     "unit": get("unit")[:64], "required": get("required").lower() in YES})
    blocks = len({r["section"] for r in rows if r["section"]})
    if rows and not blocks:
        warnings.append("Блоки не найдены — все параметры попадут в один блок; разбейте их на блоки в редакторе.")
    return {"rows": rows, "blocks": blocks, "warnings": warnings, "sheet": ws.title}


def export(rows: list[dict], path: str, title: str = "") -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Шаблон"
    ws.append([t for _k, t in EXPORT_COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="E3262B")
        c.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append([r.get("section") or "", r.get("code") or "", r.get("name_ru") or "", r.get("name_zh") or "", r.get("unit") or "",
                   "да" if r.get("required") else ""])
    for col, w in zip("ABCDEF", (28, 8, 48, 32, 10, 13)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{max(ws.max_row, 1)}"
    if title:
        ws.oddHeader.center.text = title
    wb.save(path)
