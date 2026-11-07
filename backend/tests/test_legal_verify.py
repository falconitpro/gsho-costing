"""Юридический блок, этап 6: проверка сформированных документов по содержимому (без БД)."""
from __future__ import annotations

import os
import tempfile

from docx import Document

from app.services.legal_verify import verify_docx, verify_package_files


def _doc(paragraphs, table=None, header=""):
    d = Document()
    if header:
        d.sections[0].header.paragraphs[0].text = header
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for i, row in enumerate(table):
            for j, v in enumerate(row):
                t.cell(i, j).text = str(v)
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    d.save(path)
    return path


def test_verify_docx_ok_and_total():
    p = _doc(["Договор поставки " * 10], [["№", "Наименование", "Сумма"], ["1", "X3000", "3 000 000"], ["", "Итого", "5 200 000,00 CNY"]], header="Договор № 1")
    r = verify_docx(p, expect_tables=1, expect_total=5200000.0)
    assert r["ok"] and r["tables"] == 1 and r["headers"] and r["total_found"] == 5200000.0 and r["leftovers"] == []


def test_verify_docx_detects_problems():
    p = _doc(["Текст {{buyer.name_ru}} и {{#items}} " * 5], [["№", "Итого", "4 000 000"]])
    r = verify_docx(p, expect_tables=2, expect_total=5200000.0)
    assert not r["ok"]
    assert any("служебные теги" in x for x in r["problems"]) and any("таблиц 1" in x for x in r["problems"]) and any("не совпадает" in x for x in r["problems"])
    assert "{{buyer.name_ru}}" in r["leftovers"] and "{{#items}}" in r["leftovers"]


def test_verify_broken_file_and_package_report():
    fd, bad = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with open(bad, "wb") as f:
        f.write(b"not a docx" * 300)
    assert not verify_docx(bad)["ok"]
    good = _doc(["Приложение № 2 — ЗИП " * 10])
    rep = verify_package_files([{"kind": "spec_zip", "path": good}, {"kind": "purchase_contract", "path": bad}], {"zip_amount": 0, "equipment_amount": 1}, "purchase")
    assert rep["files"]["spec_zip"]["ok"] and not rep["files"]["purchase_contract"]["ok"] and not rep["ok"]
