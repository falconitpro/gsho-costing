"""Юридический блок, этап 6: проверка сформированных документов «по факту» — не по коду ответа API, а по содержимому файла.

Каждый DOCX пакета открывается заново: файл не повреждён, нет остатков переменных {{…}} и служебных тегов {{#…}}, таблицы на месте,
текст не пуст, колонтитулы присутствуют; для спецификаций — итог таблицы совпадает с суммой договора. Отчёт пишется в пакет (checks.documents)
и показывается пользователю; расхождение итогов или остатки тегов — блокирующая ошибка формирования.
"""
from __future__ import annotations

import os
import re
from typing import Any

from docx import Document

from .docx_fill import VAR_RE, _iter_paragraphs

NUM_RE = re.compile(r"\d[\d\s ]*(?:[,.]\d+)?")


def _to_float(s: str) -> float | None:
    s = s.replace(" ", " ").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def verify_docx(path: str, expect_tables: int = 0, expect_total: float | None = None, currency: str = "") -> dict[str, Any]:
    """Проверка одного файла. Возвращает {ok, size, paragraphs, tables, leftovers, headers, total_found, problems}."""
    problems: list[str] = []
    if not os.path.isfile(path) or os.path.getsize(path) < 2000:
        return {"ok": False, "problems": ["файл отсутствует или повреждён (слишком мал)"], "size": os.path.getsize(path) if os.path.isfile(path) else 0}
    try:
        doc = Document(path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "problems": [f"файл не открывается как DOCX: {e}"], "size": os.path.getsize(path)}
    paras = [p.text for p in _iter_paragraphs(doc)]
    text = "\n".join(paras)
    leftovers = sorted({m.group(0) for m in VAR_RE.finditer(text)})
    if leftovers:
        problems.append(f"остались служебные теги: {', '.join(leftovers[:6])}")
    if len(text.strip()) < 100:
        problems.append("документ почти пуст")
    tables = len(doc.tables)
    if expect_tables and tables < expect_tables:
        problems.append(f"таблиц {tables}, ожидалось не менее {expect_tables}")
    for ti, t in enumerate(doc.tables):
        widths = {len(r.cells) for r in t.rows}
        if len(widths) > 1 and max(widths) - min(widths) > 1:
            problems.append(f"таблица {ti + 1}: строки разной ширины — возможно, разрушена вёрстка")
    headers = any((s.header and any(p.text.strip() for p in s.header.paragraphs)) for s in doc.sections)
    total_found = None
    if expect_total is not None:
        # ищем строку «Итого» в таблицах и число в ней
        cand = []
        for t in doc.tables:
            for r in t.rows:
                row_text = " | ".join(c.text for c in r.cells)
                if re.search(r"итого|total|сумма договора", row_text, re.I):
                    for m in NUM_RE.finditer(row_text):
                        v = _to_float(m.group(0))
                        if v is not None and v > 0:
                            cand.append(v)
        total_found = next((v for v in cand if abs(v - expect_total) < 0.5), None)
        if total_found is None and cand:
            problems.append(f"итог в таблице ({max(cand):,.2f}) не совпадает с суммой договора ({expect_total:,.2f})".replace(",", " "))
        elif total_found is None:
            problems.append("строка «Итого» с суммой в таблицах не найдена")
    return {"ok": not problems, "size": os.path.getsize(path), "paragraphs": len([p for p in paras if p.strip()]), "tables": tables, "leftovers": leftovers, "headers": headers,
            "total_found": total_found, "problems": problems}


def verify_package_files(docs: list[dict], totals: dict | None = None, kind: str = "purchase") -> dict[str, Any]:
    """Отчёт по всем документам пакета; ok — только если каждый файл прошёл."""
    report: dict[str, Any] = {"files": {}, "ok": True}
    amount = (totals or {}).get("equipment_amount") if kind == "purchase" else None
    for x in docs:
        k = x["kind"]
        exp_tables = 1 if k in ("spec_equipment", "spec_zip", "disagreements_protocol", "tech_spec_client") else 0
        exp_total = amount if k == "spec_equipment" and amount else None
        r = verify_docx(x["path"], expect_tables=exp_tables, expect_total=exp_total, currency=(totals or {}).get("currency", ""))
        if k == "spec_zip" and not (totals or {}).get("zip_amount") and r.get("problems") == ["таблиц 0, ожидалось не менее 1"]:
            r["problems"], r["ok"] = [], True        # ЗИП пустой — таблицы нет по праву
        report["files"][k] = r
        report["ok"] = report["ok"] and r["ok"]
    return report


def pdf_available() -> bool:
    import shutil
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))
