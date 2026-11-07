"""Полный перечень незаполненного перед отправкой на расчёт — по группам, без обрезки."""
from app.core.workflow import manager_check_items, manager_checks


def test_check_items_full_list():
    rows = [{"name_ru": f"Параметр {k}", "required": True} for k in range(8)] + [{"name_ru": "Заполнен", "required": True, "value_ru": "1"},
                                                                                    {"name_ru": "Опция РФ", "required": True, "local_ru": True}]
    deal = {"client_id": "c", "service_flag": True, "positions": [
        {"id": "p1", "factory": "f", "model": "M1", "machine_type": "Самосвал", "qty": 1, "unit_price": 0, "currency": "CNY", "tech_param_rows": rows},
        {"id": "p2", "factory": "f", "model": "M2", "qty": 1, "unit_price": 5, "currency": "CNY", "tech_param_rows": []}]}
    items = manager_check_items(deal)
    assert [x["group"] for x in items] == ["Сделка", "Позиция 1 · Самосвал M1"]
    assert items[0]["fields"] == ["Целевая наработка (активен флаг сервиса)"]
    assert items[1]["fields"] == ["Цена"] and len(items[1]["tech"]) == 8 and items[1]["position_id"] == "p1"
    assert "…" in " ".join(manager_checks(deal))          # строковые проверки по-прежнему обрезаны, перечень — нет
