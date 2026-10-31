from openpyxl import load_workbook

from app.calc import calc_deal
from app.services.excel_export import MANAGER_HIDDEN_SHEETS, export_excel
from tests.fixtures import sample_deal

EXPECTED = ["Сводка", "Себестоимость", "Логистика и партии", "Сервис", "Платежи и денежный поток", "Курсы",
            "Исходные данные RFQ", "ЗИП на ТО", "История и справки", "Визы"]


def test_full_and_manager_versions(tmp_path):
    inp = sample_deal()
    res = calc_deal(inp)
    meta = {"version_no": 1, "approvals": [{"step": "Коммерческая", "role": "cd", "user": "Иванов", "at": "2026-11-01", "decision": "да", "comment": ""}],
            "spare_parts": {"P1": [{"system": "Двигатель", "name": "Масло", "interval_hours": 250, "qty_per_service": 40, "unit_price": 30, "currency": "CNY"}]}}
    full = export_excel(inp, res, str(tmp_path / "full.xlsx"), meta)
    wb = load_workbook(full)
    assert wb.sheetnames == EXPECTED
    # формулы живые
    ws = wb["Сводка"]
    formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any("Себестоимость" in f for f in formulas)
    cost_ws = wb["Себестоимость"]
    assert any(isinstance(c.value, str) and c.value.startswith("=SUM(") for row in cost_ws.iter_rows() for c in row)
    assert wb["Курсы"].max_row > 5

    mgr = export_excel(inp, res, str(tmp_path / "mgr.xlsx"), meta, manager_version=True)
    wbm = load_workbook(mgr)
    assert not (set(wbm.sheetnames) & MANAGER_HIDDEN_SHEETS)
    # в версии менеджера нигде нет слов «маржа» / «себестоимость»
    text = " ".join(str(c.value) for ws in wbm.worksheets for row in ws.iter_rows() for c in row if c.value is not None).lower()
    assert "маржа" not in text and "себестоимость" not in text
