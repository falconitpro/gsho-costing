"""Запчасти: цена строки, порог маржи по себестоимости, визы КД/ФД, минимум маржи КП, подбор по артикулу."""
from app.services.parts_pricing import kp_summary, price_line, request_status, threshold_pct

FX = {"EUR": 100.0, "CNY": 12.0}


def test_threshold_bands():
    assert threshold_pct(5_000) == 40 and threshold_pct(10_000) == 40 and threshold_pct(50_000) == 30 and threshold_pct(500_000) == 20
    assert threshold_pct(None) is None


def test_price_source_priority_and_calc():
    # ручная > договорная > прайс > расчёт от себестоимости по порогу
    r = price_line(qty=2, unit_cost_rub=3000, fx=FX, manual=(90, "EUR"), contract=(80, "EUR"), pricelist=(70, "EUR"))
    assert r["price_source"] == "manual" and r["price_rub"] == 9000
    r = price_line(qty=2, unit_cost_rub=3000, fx=FX, contract=(80, "EUR"), pricelist=(70, "EUR"))
    assert r["price_source"] == "contract" and r["sum_rub"] == 16000
    r = price_line(qty=2, unit_cost_rub=3000, fx=FX)                      # себестоимость строки 6000 → порог 40 %
    assert r["price_source"] == "calc" and r["price"] == 5000 and r["margin_pct"] == 40 and r["approval"] is None
    r = price_line(qty=1, unit_cost_rub=None, fx=FX)
    assert r["price"] is None and r["margin_pct"] is None


def test_visa_levels():
    # себестоимость 6000 ₽ → порог 40 %; цена 9000 ₽ → маржа 33,3 % → ниже на 6,7 п. п. → ФД
    r = price_line(qty=1, unit_cost_rub=6000, fx=FX, manual=(90, "EUR"))
    assert round(r["margin_pct"], 1) == 33.3 and r["approval"] == "fd"
    r = price_line(qty=1, unit_cost_rub=6000, fx=FX, manual=(96, "EUR"))  # 37,5 % — в пределах допуска 5 п. п. → КД
    assert r["approval"] == "cd" and r["below_pp"] == 2.5


def test_kp_summary_handling_fee():
    a = price_line(qty=1, unit_cost_rub=1000, fx=FX)                      # маржа 40 % → прибыль ≈ 667 ₽
    b = price_line(qty=1, unit_cost_rub=6000, fx=FX, manual=(96, "EUR"))
    s = kp_summary([a, b], 5000)
    assert s["approval"] == "cd" and s["handling_fee_rub"] == round(5000 - s["profit_rub"], 2) and s["handling_fee_rub"] > 0
    big = price_line(qty=10, unit_cost_rub=6000, fx=FX)
    assert kp_summary([big], 5000)["handling_fee_rub"] == 0


def test_request_status_text():
    st = request_status([{"status": "quoted"}, {"status": "in_kp"}, {"status": "shipped"}, {"status": "new"}])
    assert st["text"] == "КП: 2 из 4 строк, отгружено 1"


def test_supplier_currency():
    from app.services.parts_pricing import supplier_currency as sc
    assert sc("GRP Group", "", "DE", "CNY") == "EUR"
    assert sc("ООО Деталь", "GRP", "RU", "") == "EUR"          # GRP — евро независимо от страны
    assert sc("Xingtai", "", "CN", "") == "CNY"
    assert sc("Поставщик РФ", "", "RU", "CNY") == "RUB"
    assert sc("Kyrgyz", "", "KG", "") == "KGS"
    assert sc("X", "", "TR", "USD") == "USD" and sc("X", "", "", "ZZZ") == "RUB"
