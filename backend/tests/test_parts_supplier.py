"""Запросы цен поставщикам: язык, файл запроса на двух языках и разбор заполненного ответа."""
from openpyxl import load_workbook

from app.services.parts_supplier import build_rfq_xlsx, parse_availability, parse_rfq_answer, rfq_status, supplier_language

LINES = [{"no": 1, "number": "SUP-1", "name_ru": "Ведущий мост", "name_foreign": "驱动桥", "qty": 2, "unit": "шт"},
         {"no": 2, "number": "522608", "name_ru": "Топливный фильтр", "name_foreign": "燃油滤清器", "qty": 12, "unit": "шт"},
         {"no": 3, "number": "X-3", "name_ru": "Ремень", "name_foreign": "", "qty": 1, "unit": "шт"}]


def test_language():
    assert supplier_language("CN") == "zh" and supplier_language("DE") == "de" and supplier_language("KG") == "ru"
    assert supplier_language("US") == "en" and supplier_language("CN", "ru") == "ru" and supplier_language("", "cn") == "zh"


def test_availability_words():
    assert parse_availability("Есть") == "available" and parse_availability("现货") == "available" and parse_availability("Lager") == "available"
    assert parse_availability("под заказ") == "on_order" and parse_availability("无") == "none" and parse_availability("nein") == "none"
    assert parse_availability("Alternative") == "analog" and parse_availability("") == ""


def test_build_and_parse_answer(tmp_path):
    path = build_rfq_xlsx(str(tmp_path / "rfq.xlsx"), "ЗП-2026-0001", "Shandong Shili", "zh", LINES, "12.11.2026")
    wb = load_workbook(path)
    ws = wb.active
    hdr = next(r for r in range(1, 10) if str(ws.cell(r, 1).value or "").startswith("№"))
    assert "序号" in ws.cell(hdr, 1).value and "币种" in ws.cell(hdr, 9).value
    # поставщик заполнил две строки из трёх (частичный ответ), одну — аналогом
    ws.cell(hdr + 1, 7, "现货"); ws.cell(hdr + 1, 8, "1 250,50"); ws.cell(hdr + 1, 9, "cny"); ws.cell(hdr + 1, 10, 30)
    ws.cell(hdr + 2, 8, 80); ws.cell(hdr + 2, 9, "CNY"); ws.cell(hdr + 2, 12, "FF-5052"); ws.cell(hdr + 2, 11, 20)
    wb.save(path)
    ans = parse_rfq_answer(list(load_workbook(path).active.iter_rows(values_only=True)))
    assert set(ans) == {1, 2}
    assert ans[1] == {"availability": "available", "price": 1250.5, "currency": "CNY", "lead_days": 30, "moq": None, "analog_number": "", "comment": ""}
    assert ans[2]["availability"] == "analog" and ans[2]["analog_number"] == "FF-5052" and ans[2]["moq"] == 20
    assert rfq_status([{"availability": "available"}, {"availability": ""}]) == "partial" and rfq_status([{"availability": ""}]) == ""


def test_russian_only_file(tmp_path):
    path = build_rfq_xlsx(str(tmp_path / "rfq.xlsx"), "ЗП-2026-0002", "Поставщик КГ", "ru", LINES)
    ws = load_workbook(path).active
    hdr = next(r for r in range(1, 10) if ws.cell(r, 1).value == "№")
    assert [ws.cell(hdr, c).value for c in (2, 3, 4)] == ["Артикул", "Наименование", "Кол-во"]
