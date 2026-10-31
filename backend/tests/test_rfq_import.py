"""Импорт реального двуязычного шаблона RFQ (backend/app/templates/rfq_template.xlsx)."""
from datetime import date

from app.services.rfq_import import parse_rfq, price_reference, template_fields
from app.services.rfq_template import DEFAULT_TEMPLATE, fill_rfq_template

# «Ответ завода»: заполнены не все поля — так и должно быть, обязательных немного
FILLED = {
    "0.1": "2026-10-28", "0.2": "2026-11-05", "0.4": -40, "0.5": 35, "0.6": "подземные работы, запылённость",
    "0.7": "Xuzhou XCMG Mining Machinery Co., Ltd", "0.8": "Xuzhou, Jiangsu, China",
    "1.1": "Шахтный самосвал", "1.2": "XDR80T", "1.3": "КНР", "1.4": 2026, "1.5": "9 850", "1.6": 4200, "1.7": 3950,
    "1.8": 4300, "1.14": "38000", "1.15": 72000, "1.16": 34000, "1.17": 22, "1.19": 35, "1.20": 25, "1.22": "26.5R25",
    "2.1": "Weichai", "2.2": "WP13", "2.3": "China III", "2.4": 6, "2.5": 12.5, "2.6": 405, "2.7": "2 × 280 кВт",
    "6.1": "мокрые диски", "9.1": "закрытая", "9.6": "Да", "9.18": "Ansul",
    "12.1": 25000, "12.3": 18000,
    "13.1": "CNY / 人民币", "13.2": "FCA", "13.3": "Маньчжурия", "13.4": 2, "13.5": "2 300 000", "13.6": 4600000, "13.8": 410000,
    "14.1": 120, "14.2": 7, "14.4": 12, "14.5": 2000, "14.7": 30, "14.9": "Да", "14.18": "включено", "14.20": "ящичная, плёнка",
}
OPTION_NAMES = {"12.1": "Кондиционер кабины", "12.2": "Система пожаротушения", "12.3": "Централизованная смазка"}
ZIP = [
    {"name": "Моторное масло", "qty_per_service": 40, "interval_hours": 250, "unit_price": 30, "part_no": "1000423"},
    {"name": "Фильтр масляный", "qty_per_service": 2, "interval_hours": 250, "unit_price": 180, "part_no": "1000800"},
    {"name": "Фильтр гидравлический сливной", "qty_per_service": 1, "interval_hours": 500},   # без цены → предупреждение
]


def test_real_template_schema():
    r = parse_rfq(DEFAULT_TEMPLATE)
    assert r.template_version == "v2" and len(r.fields) >= 170 and len(r.sections) == 15
    l_codes = {c for c, f in r.fields.items() if f.is_l}
    assert {"0.7", "1.1", "1.5", "1.14", "2.6", "13.5", "14.1", "14.18", "14.21"} <= l_codes and "1.8" not in l_codes
    assert r.zip_target_hours == 10000 and len(r.spare_parts) >= 45
    assert not r.ok and set(r.missing_required) >= {"0.7", "1.1", "1.2", "13.4", "13.5"}   # пустой шаблон — обязательные не заполнены
    tf = template_fields(DEFAULT_TEMPLATE)
    assert any(f["code"] == "8.5.1" for f in tf["fields"]) and "13.5" in tf["required"]


def test_parse_filled_rfq(tmp_path):
    p = fill_rfq_template(str(tmp_path / "rfq.xlsx"), FILLED, ZIP, option_names=OPTION_NAMES)
    r = parse_rfq(p)
    assert r.ok, [e.message for e in r.errors if e.severity == "error"]
    assert r.header["requested_at"] == "2026-10-28" and r.header["factory_name"].startswith("Xuzhou")
    assert r.position["model"] == "XDR80T" and r.position["qty"] == 2 and r.position["currency"] == "CNY"
    assert r.position["unit_price"] == 2_300_000 and r.position["production_days"] == 120 and r.position["zip_price"] == 410000
    # опции: цена на 1 машину × количество
    assert r.position["options_price_per_unit"] == 43000 and r.position["options_price"] == 86000
    assert [o["name"] for o in r.options] == ["Кондиционер кабины", "Система пожаротушения", "Централизованная смазка"]
    assert r.options[1]["value"] is None          # названа заводом, цена не указана — остаётся в списке для уточнения
    # L-поля заполненные → логисту; остальные заполненные → техданные; незаполненные — в fields, но не в params
    assert r.logistics_params["1.5"]["value"] == 9850 and r.logistics_params["14.18"]["value"] == "включено"
    assert "1.8" in r.tech_params and "1.8" not in r.logistics_params
    assert "1.9" in r.fields and "1.9" not in r.tech_params
    assert r.fields["2.7"].value == "2 × 280 кВт"       # текст в числовом поле сохраняется с предупреждением
    assert r.filled_count >= 50
    # ЗИП
    filled = [s for s in r.spare_parts if s["filled"]]
    assert len(filled) == 3 and filled[0]["unit_price"] == 30 and filled[0]["part_no"] == "1000423"
    assert any("пустая цена" in e.message for e in r.errors)


def test_parse_missing_required(tmp_path):
    bad = dict(FILLED, **{"13.5": "N/A", "14.1": "сто двадцать", "1.2": ""})
    p = fill_rfq_template(str(tmp_path / "bad.xlsx"), bad, [])
    r = parse_rfq(p)
    assert not r.ok and set(r.missing_required) == {"13.5", "1.2"}
    assert any(e.code == "14.1" and "число" in e.message for e in r.errors)


def test_price_reference():
    hist = [{"unit_price": 2_100_000, "currency": "CNY", "on": date(2026, 3, 1)},
            {"unit_price": 2_000_000, "currency": "CNY", "on": date(2025, 9, 1)}]
    ref = price_reference(2_300_000, "CNY", hist, 10.0)
    assert ref["last_price"] == 2_100_000 and ref["deviation_pct"] == 9.52 and not ref["needs_comment"]
    assert price_reference(2_400_000, "CNY", hist, 10.0)["needs_comment"]
