"""КП по заявке на запчасти: снимок строк в валюте КП, «что изменилось», документ без себестоимости."""
import pytest
from docx import Document

from app.services.parts_kp import build_kp_lines, diff_kp, lead_text, render_docx

FX = {"EUR": 100.0, "CNY": 12.0}
L1 = {"id": "a", "no": 1, "raw_article": "554831", "raw_name": "мост", "qty": 2, "unit": "шт", "price": 50, "currency": "EUR", "source": "stock",
      "lead_days": None, "part": {"article": "554831", "name_ru": "Ведущий мост", "manufacturer": "Sandvik"}}
L2 = {"id": "b", "no": 2, "raw_article": "X1", "raw_name": "Фильтр", "qty": 1, "unit": "шт", "price": 3000, "currency": "RUB", "source": "order",
      "lead_days": 45, "part": None}


def test_lead_text():
    assert lead_text("stock", 10) == "в наличии" and lead_text("order", 45) == "45 дн." and lead_text("order", None) == "по запросу"


def test_build_lines_in_kp_currency():
    rub = build_kp_lines([L1, L2], "RUB", FX)
    assert rub[0]["price"] == 5000 and rub[0]["sum"] == 10000 and rub[0]["name"] == "Ведущий мост" and rub[1]["lead"] == "45 дн."
    eur = build_kp_lines([L1, L2], "EUR", FX)
    assert eur[0]["price"] == 50 and eur[1]["price"] == 30 and eur[1]["article"] == "X1"
    with pytest.raises(ValueError, match="Нет курса USD"):
        build_kp_lines([L1], "USD", FX)


def test_diff():
    a = build_kp_lines([L1], "RUB", FX)
    b = build_kp_lines([{**L1, "price": 55}, L2], "RUB", FX)
    ch = diff_kp(a, b)
    assert {"article": "554831", "change": "цена", "old": 5000, "new": 5500} in ch and {"article": "X1", "change": "добавлена"} in ch
    assert diff_kp(b, a)[-1] == {"article": "X1", "change": "убрана"}


def test_render_docx_without_cost(tmp_path):
    lines = build_kp_lines([L1, L2], "EUR", FX)
    kp = {"number": "КП-ЗЧ-2026-0001/1", "date": "05.11.2026", "valid_until": "05.12.2026", "currency": "EUR", "fx_rate": 100.0,
          "lines": lines, "handling_fee": 12.5, "total": round(sum(l["sum"] for l in lines) + 12.5, 2), "machine": "Sandvik LH410"}
    path = render_docx(kp, 'ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"', str(tmp_path / "kp.docx"))
    doc = Document(path)
    text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
    assert "КП-ЗЧ-2026-0001/1" in text and "Обработка заказа" in text and "в наличии" in text and "142,50 EUR" in text
    assert "100,0000 ₽" in text
