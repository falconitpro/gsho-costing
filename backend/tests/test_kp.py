import pytest
from docx import Document

from app.services.kp_generator import KpData, KpVariant, assert_no_cost_leak, build_kp


def _data():
    return KpData(
        kp_no="КП-2026-0042", kp_date="01.11.2026", valid_until="01.12.2026", client="ООО «Рудник»", site="Месторождение Северное",
        manager="Иванов И.И.", manager_contact="+7 900 000-00-00",
        intro_text="Уважаемые коллеги! Направляем предложение на поставку горношахтного оборудования.",
        variants=[KpVariant(name="Вариант 1", model_factory="XDR80T / XCMG", qty=2, configuration="кондиционер, пожаротушение",
                            basis="DDP объект", delivery_days=159, unit_price_rub=60_000_000, total_rub=120_000_000, vat_pct=20,
                            payment_terms="30 % аванс, 70 % по поставке", warranty="12 мес. / 2000 м·ч", service_line="включено в цену")],
        tech_specs={"XDR80T": [("Грузоподъёмность", "80", "т"), ("Мощность двигателя", "405", "кВт")]},
        service_block={"regulations": ["ТО-1", "ТО-2"], "spare_parts": "по листу ЗИП", "visits": "20 выездов", "price": "включено в цену"},
        delivery_terms=[("Документация на русском", "да"), ("ТР ТС 010/2011", "включено")],
        payment_and_warranty="Оплата по графику. Гарантия 12 месяцев.", signatures="Генеральный директор", requisites="ИНН 0000000000",
    )


def test_kp_builds_and_has_no_cost_leak(tmp_path):
    p = build_kp(_data(), str(tmp_path / "kp.docx"))
    doc = Document(p)
    text = "\n".join(x.text for x in doc.paragraphs) + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ" in text and "±5 %" in text
    assert any("120 000 000" in c.text for t in doc.tables for r in t.rows for c in r.cells)


def test_leak_detector(tmp_path):
    d = _data()
    d.intro_text = "Себестоимость сделки составляет 100 млн"
    with pytest.raises(RuntimeError):
        build_kp(d, str(tmp_path / "leak.docx"))
