import pytest
from docx import Document

from app.services.kp_generator import KpData, KpVariant, build_kp


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


def test_pdf_drawing_thumbnail_in_kp(tmp_path):
    """Задача 10: PDF-чертёж вставляется в документ КП миниатюрой первой страницы."""
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "drawing.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=420)
    page.draw_rect(fitz.Rect(40, 40, 555, 380), color=(0, 0, 0), width=2)
    page.insert_text((60, 80), "General arrangement", fontsize=18)
    doc.save(str(pdf))
    doc.close()
    from app.services.kp_generator import pdf_thumbnail
    thumb = pdf_thumbnail(str(pdf), max_px=600)
    assert thumb and thumb.endswith(".png")
    baseline = len(Document(build_kp(_data(), str(tmp_path / "kp_base.docx"))).inline_shapes)   # логотип в шапке
    d = _data()
    d.images = {"XDR80T": [(str(pdf), "Габаритный чертёж")]}
    kp_path = build_kp(d, str(tmp_path / "kp_pdf.docx"))
    assert kp_path.endswith("kp_pdf.docx") and pdf.read_bytes().startswith(b"%PDF")   # КП не перезаписал файл чертежа
    out = Document(kp_path)
    assert len(out.inline_shapes) == baseline + 1
    assert any("Габаритный чертёж (стр. 1)" in p.text for p in out.paragraphs)


def test_pdf_thumbnail_without_file_is_none(tmp_path):
    from app.services.kp_generator import pdf_thumbnail
    assert pdf_thumbnail(str(tmp_path / "missing.pdf")) is None


def test_kp_with_png_keeps_its_own_path(tmp_path):
    """Регрессия: при изображениях в документе КП сохранялся под именем картинки."""
    Image = pytest.importorskip("PIL.Image")
    png = tmp_path / "scheme.png"
    Image.new("RGB", (320, 200), (200, 30, 30)).save(png)
    d = _data()
    d.images = {"XDR80T": [(str(png), "Схема")]}
    out = build_kp(d, str(tmp_path / "kp.docx"))
    assert out.endswith("kp.docx")
    assert png.read_bytes()[:4] == b"\x89PNG"
