from datetime import date

from app.services.rfq_import import parse_rfq, price_reference
from app.services.rfq_template import build_rfq_template

FILLED = {
    "0.1": 10, "0.4": -40, "0.5": 35, "0.7": "Xuzhou XCMG Mining Machinery Co., Ltd", "0.8": "Xuzhou, Jiangsu",
    "1.1": "Шахтный самосвал", "1.2": "XDR80T", "1.5": "9 850", "1.6": 4200, "1.7": 3950, "1.14": "38000", "1.15": 36500,
    "2.1": "Weichai WP13", "2.2": 405, "2.3": "China III", "2.4": "дизель",
    "12.1": 25000, "12.2": "N/A", "12.3": 18000,
    "13.2": "FCA", "13.3": "Маньчжурия", "13.4": 2, "13.5": "2 300 000", "13.6": "RMB", "13.8": 410000,
    "14.1": 120, "14.2": 7, "14.4": 12, "14.5": 2000, "14.9": "включено",
}
ZIP = [
    {"system": "Двигатель", "part_no": "1000423", "name": "Масло моторное 15W-40", "interval_hours": 250, "qty_per_service": 40, "unit_price": 30},
    {"system": "Двигатель", "part_no": "1000800", "name": "Фильтр масляный", "interval_hours": 250, "qty_per_service": 2, "unit_price": 180},
    {"system": "Гидравлика", "part_no": "2001100", "name": "Фильтр гидравлический", "interval_hours": 500, "qty_per_service": 1, "unit_price": None},
]


def test_parse_filled_rfq(tmp_path):
    p = build_rfq_template(str(tmp_path / "rfq.xlsx"), FILLED, ZIP)
    r = parse_rfq(p)
    assert r.template_version == "v1"
    assert r.ok, [e.message for e in r.errors if e.severity == "error"]
    assert r.header["factory_name"].startswith("Xuzhou")
    assert r.position["model"] == "XDR80T" and r.position["qty"] == 2
    assert r.position["unit_price"] == 2_300_000 and r.position["currency"] == "CNY"
    assert r.position["options_price"] == 43000 and len(r.options) == 2        # N/A не считается опцией
    assert r.logistics_params["1.5"]["value"] == 9850 and "14.9" in r.logistics_params
    assert "1.3" in r.tech_params and "1.5" not in r.tech_params
    assert len(r.spare_parts) == 3 and r.spare_parts[0]["unit_price"] == 30
    assert any("пустая цена" in e.message for e in r.errors)                 # warning по ЗИП


def test_parse_missing_required_and_bad_number(tmp_path):
    bad = dict(FILLED, **{"13.5": "N/A", "14.1": "сто двадцать", "1.2": ""})
    p = build_rfq_template(str(tmp_path / "bad.xlsx"), bad, [])
    r = parse_rfq(p)
    assert not r.ok
    assert set(r.missing_required) == {"13.5", "1.2"}
    assert any(e.code == "14.1" and "число" in e.message for e in r.errors)


def test_price_reference():
    hist = [{"unit_price": 2_100_000, "currency": "CNY", "on": date(2026, 3, 1)},
            {"unit_price": 2_000_000, "currency": "CNY", "on": date(2025, 9, 1)}]
    ref = price_reference(2_300_000, "CNY", hist, 10.0)
    assert ref["last_price"] == 2_100_000 and ref["deviation_pct"] == 9.52 and not ref["needs_comment"]
    assert price_reference(2_400_000, "CNY", hist, 10.0)["needs_comment"]
    assert price_reference(1, "USD", hist, 10.0)["last_price"] is None
