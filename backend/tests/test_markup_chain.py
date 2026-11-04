"""Запчасти, шаг 1: наценки между компаниями группы и маржа по ступеням маршрута."""
import pytest

from app.services.markup_chain import ChainError, markup_pct_for, simulate, validate_steps

FX = {"CNY": 12.0, "EUR": 100.0, "USD": 90.0}
RULES = {"default": {"cn": 5, "kg": 3}, "by_group": [{"entity_id": "kg", "part_group": "Фильтры", "pct": 8}],
         "by_supplier": [{"entity_id": "cn", "supplier_id": "f1", "pct": 2}], "min_margin_pct": 2.5}
# вариант В: Китай → FCA склад экспедитора в CNY; Киргизия → DAP склад РФ в валюте закупки
ROUTE_CN_KG_RU = [
    {"seller_entity_id": None, "buyer_entity_id": "cn", "currency": "CNY", "incoterms": "EXW"},
    {"seller_entity_id": "cn", "buyer_entity_id": "kg", "currency": "CNY", "incoterms": "FCA"},
    {"seller_entity_id": "kg", "buyer_entity_id": "ru", "currency": "CNY", "incoterms": "DAP"},
]
NAMES = {"cn": "Компания в Китае", "kg": "Компания в Киргизии", "ru": "Компания в РФ"}


def test_markup_priority():
    assert markup_pct_for(RULES, "cn") == 5
    assert markup_pct_for(RULES, "cn", supplier_id="f1") == 2              # поставщик важнее ставки по умолчанию
    assert markup_pct_for(RULES, "kg", part_group="фильтры") == 8          # группа товара, без учёта регистра
    assert markup_pct_for(RULES, "kg", part_group="Фильтры", supplier_id="f1") == 8   # исключение поставщика — только для его компании
    assert markup_pct_for(RULES, "ru") == 0


def test_validate_steps():
    assert validate_steps(ROUTE_CN_KG_RU) == []
    assert validate_steps([]) == ["В маршруте нет ни одной ступени"]
    bad = [dict(ROUTE_CN_KG_RU[0]), {**ROUTE_CN_KG_RU[1], "seller_entity_id": "kg"}]
    assert any("продавец должен быть покупателем" in e for e in validate_steps(bad))
    assert any("от поставщика" in e for e in validate_steps([{**ROUTE_CN_KG_RU[1]}]))


def test_simulate_chain_and_group_margin():
    r = simulate(1000, ROUTE_CN_KG_RU, RULES, fx=FX, client_price_rub=20000, extra_costs_rub=2000, entity_names=NAMES)
    p = [s["price"] for s in r["steps"]]
    assert p == [1000, 1050, 1081.5]                                       # +5 % Китай, +3 % Киргизия
    assert [c["margin"] for c in r["companies"]] == [50, 31.5]
    assert r["purchase_rub"] == 12000 and r["landed_rub"] == round(1081.5 * 12 + 2000, 2)
    assert r["group_margin_rub"] == 20000 - 12000 - 2000
    # сумма долей компаний = маржа группы
    assert round(sum(x["margin_rub"] for x in r["group_split"]), 2) == r["group_margin_rub"]
    assert r["warnings"] == []


def test_simulate_currency_change_transfer_price_and_warnings():
    route = [{"seller_entity_id": None, "buyer_entity_id": "kg", "currency": "EUR"},
             {"seller_entity_id": "kg", "buyer_entity_id": "ru", "currency": "RUB", "transfer_price": 10100}]
    r = simulate(100, route, RULES, fx=FX, entity_names=NAMES)
    assert r["steps"][1]["price"] == 10100 and r["steps"][1]["transfer_price"]
    assert r["companies"][0]["margin"] == 100                                # 10100 − 100 € × 100
    assert any("ниже минимума" in w for w in r["warnings"])                  # ≈ 0,99 % < 2,5 %
    with pytest.raises(ChainError, match="Нет курса KGS"):
        simulate(100, [{"seller_entity_id": None, "buyer_entity_id": "kg", "currency": "KGS"},
                       {"seller_entity_id": "kg", "buyer_entity_id": "ru", "currency": "RUB"}], RULES, fx=FX)
    with pytest.raises(ChainError, match="больше нуля"):
        simulate(0, ROUTE_CN_KG_RU, RULES, fx=FX)
