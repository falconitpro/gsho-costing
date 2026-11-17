"""Шаблон технических характеристик: Excel туда и обратно, блоки, порядок; свои строки в форме позиции (D-070)."""
import io
import os

import pytest
from openpyxl import Workbook
from openpyxl.styles import Font

from app.services import template_excel


def _xlsx(tmp_path, rows, bold=(), merge=()):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    for r_idx in bold:
        for c in ws[r_idx]:
            c.font = Font(bold=True)
    for rng in merge:
        ws.merge_cells(rng)
    p = tmp_path / f"t{len(os.listdir(tmp_path))}.xlsx"
    wb.save(p)
    return str(p)


def test_parse_with_block_column(tmp_path):
    p = _xlsx(tmp_path, [["Блок", "Параметр", "Ед. изм.", "Обязательный", "参数"], ["Двигатель", "Мощность", "кВт", "да", "功率"],
                         [None, "Модель двигателя", "", "", ""], ["Трансмиссия", "Тип КПП", "", "нет", ""]])
    r = template_excel.parse(p)
    assert [(x["section"], x["name_ru"], x["unit"], x["required"], x["name_zh"]) for x in r["rows"]] == [
        ("Двигатель", "Мощность", "кВт", True, "功率"), ("Двигатель", "Модель двигателя", "", False, ""), ("Трансмиссия", "Тип КПП", "", False, "")]
    assert r["blocks"] == 2 and not r["warnings"]


def test_parse_header_rows_bold_numbered_merged(tmp_path):
    p = _xlsx(tmp_path, [["Наименование характеристики", "Единица"], ["Двигатель", None], ["Мощность", "кВт"], ["Крутящий момент", "Н·м"],
                         ["2. Трансмиссия", None], ["Тип КПП", None], [None, None], ["Кабина", None], ["Кондиционер", "да/нет"]],
              bold=(2,), merge=("A8:B8",))
    r = template_excel.parse(p)
    assert [(x["section"], x["name_ru"]) for x in r["rows"]] == [("Двигатель", "Мощность"), ("Двигатель", "Крутящий момент"),
                                                                   ("2. Трансмиссия", "Тип КПП"), ("Кабина", "Кондиционер")]


def test_parse_without_headers_and_roundtrip(tmp_path):
    p = _xlsx(tmp_path, [["Мощность", "кВт"], ["Масса", "кг"]])
    r = template_excel.parse(p)
    assert [x["name_ru"] for x in r["rows"]] == ["Мощность", "Масса"] and r["warnings"]
    rows = [{"section": "Двигатель", "code": "2.1", "name_ru": "Модель", "name_zh": "型号", "unit": "", "required": True},
            {"section": "Кабина", "code": "", "name_ru": "Кондиционер", "name_zh": "", "unit": "да/нет", "required": False}]
    out = str(tmp_path / "exp.xlsx")
    template_excel.export(rows, out, "тест")
    back = template_excel.parse(out)["rows"]
    assert back == rows


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="нужна БД")
def test_template_api_and_position_custom_rows():
    from fastapi.testclient import TestClient
    from app.main import app
    from scripts.seed import run as seed
    from tests.test_api_flow import _ok, H, MGR, CD
    seed()
    with TestClient(app) as c:
        r = c.post("/api/refs/machine-types", headers=H(CD), json={"name": "Буровая установка (тест)"})
        assert r.status_code in (200, 201), r.text
        mt = next(t for t in _ok(c.get("/api/refs/machine-types", headers=H(CD))) if t["name"] == "Буровая установка (тест)")
        url = f"/api/refs/tech-param-templates?machine_type_id={mt['id']}"
        # Excel → разбор (только КД / ЗГД) → сохранение в порядке экрана, пустые строки отбрасываются
        buf = io.BytesIO()
        wb = Workbook(); ws = wb.active
        for r in [["Блок", "Параметр", "Ед.", "Обязательный"], ["Двигатель", "Мощность", "кВт", "да"], ["Двигатель", "Объём", "л", ""], ["Кабина", "Кондиционер", "", ""]]:
            ws.append(r)
        wb.save(buf)
        files = {"file": ("шаблон.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        assert c.post("/api/refs/tech-param-templates/parse", headers=H(MGR), files=files).status_code == 403
        parsed = _ok(c.post("/api/refs/tech-param-templates/parse", headers=H(CD), files=files))
        assert parsed["blocks"] == 2 and len(parsed["rows"]) == 3
        rows = [{**r, "no": 0} for r in parsed["rows"]]
        rows = [rows[2], rows[0], {"no": 0, "name_ru": "  ", "section": "Кабина"}, rows[1]]          # переставили блоки, пустая строка
        assert _ok(c.put(url, headers=H(CD), json=rows))["rows"] == 3
        got = _ok(c.get(url, headers=H(MGR)))
        assert not got["inherited"] and [(r["no"], r["section"], r["name_ru"]) for r in got["rows"]] == [
            (1, "Кабина", "Кондиционер"), (2, "Двигатель", "Мощность"), (3, "Двигатель", "Объём")]
        x = c.get(f"/api/refs/tech-param-templates/file?machine_type_id={mt['id']}", headers=H(MGR))
        assert x.status_code == 200 and x.content[:2] == b"PK"
        # позиция этого типа получает строки шаблона с блоками
        clients = _ok(c.get("/api/refs/clients", headers=H(MGR)))
        did = _ok(c.post("/api/deals", headers=H(MGR), json={"client_id": clients[0]["id"], "site": "Шаблон"}), 201)["id"]
        pos = _ok(c.post(f"/api/deals/{did}/positions", headers=H(MGR), json={"machine_type": mt["name"], "qty": 1,
                  "custom_rows": [{"name": "Глубина бурения", "unit": "м", "requirement": "≥ 30", "section_no": 99}], "row_order": ["x:0"]}), 201)
        tr = _ok(c.get(f"/api/deals/{did}/positions/{pos['id']}/tech-params", headers=H(MGR)))["rows"]
        names = [r["name_ru"] for r in tr]
        assert names[:3] == ["Кондиционер", "Мощность", "Объём"] and "Глубина бурения" in names
        assert {r["section"] for r in tr if r["name_ru"] in ("Мощность", "Объём")} == {"Двигатель"}
        gb = next(r for r in tr if r["name_ru"] == "Глубина бурения")
        assert gb["customer_requirement_ru"] == "≥ 30" and gb["section"] == "Дополнительные параметры"
        # форма: своя строка переставлена в начало — порядок таблицы меняется, строка не дублируется
        others = [r for r in tr if not r["code"] and r["id"] != gb["id"]]
        custom = [{"id": gb["id"], "name": "Глубина бурения", "unit": "м", "requirement": "≥ 35", "section_no": 99}] + [
            {"id": r["id"], "name": r["name_ru"], "unit": r["unit"], "requirement": "", "value": "", "comment": "", "section_no": 100, "section": r["section"]} for r in others]
        _ok(c.patch(f"/api/deals/{did}/positions/{pos['id']}", headers=H(MGR), json={"custom_rows": custom, "row_order": [f"x:{i}" for i in range(len(custom))]}))
        tr = _ok(c.get(f"/api/deals/{did}/positions/{pos['id']}/tech-params", headers=H(MGR)))["rows"]
        assert tr[0]["name_ru"] == "Глубина бурения" and tr[0]["customer_requirement_ru"] == "≥ 35"
        assert [r["name_ru"] for r in tr].count("Глубина бурения") == 1 and [r["no"] for r in tr] == list(range(1, len(tr) + 1))
        assert {r["section"] for r in tr if r["name_ru"] in ("Мощность", "Объём")} == {"Двигатель"}   # блок шаблона сохранился
        _ok(c.put(url, headers=H(CD), json=[{"no": 1, "name_ru": "Мощность", "section": "Двигатель"}]))
