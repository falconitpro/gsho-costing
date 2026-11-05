"""Перенос из МойСклад: разбор выгрузки «Товары (Excel)» и отчёта «Остатки по складам»."""
from openpyxl import Workbook

from app.services.moysklad_import import norm_name, num, parse_products, parse_stock, read_rows

HEADER = ["Группы", "UUID", "Тип", "Код", "Наименование", "Внешний код", "Артикул", "Единица измерения", "Цена: Цена продажи",
          "Валюта (Цена продажи)", 'Цена: ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"', 'Валюта (ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ")', "Закупочная цена", "Валюта (Закупочная цена)",
          "Неснижаемый остаток", "Неснижаемый остаток для склада Бишкек", "Неснижаемый остаток для склада Котельниково", "Описание",
          "Минимальная цена", "Валюта (Минимальная цена)", "Страна", "Архивный", "Вес", "Объем", "Код ТН ВЭД", "Изображение",
          "Доп. поле: Применимо на оборудовании", "Доп. поле: Срок поставки, дней", "Доп. поле: Спецификация, №", "Доп. поле: Доп. соглашение, №"]


def row(**kw):
    base = {h: None for h in HEADER}
    base.update({"UUID": "u1", "Тип": "Товар", "Код": "00001", "Наименование": "Топливный фильтр", "Артикул": "522608",
                 "Цена: Цена продажи": "0,00", "Валюта (Цена продажи)": "евро", 'Цена: ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"': "0,00",
                 'Валюта (ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ")': "евро", "Закупочная цена": "0,00", "Валюта (Закупочная цена)": "евро",
                 "Минимальная цена": "0,00", "Валюта (Минимальная цена)": "евро", "Архивный": "нет", "Вес": "0", "Объем": "0"})
    base.update(kw)
    return tuple(base[h] for h in HEADER)


def test_num_and_names():
    assert num("2 064,10") == 2064.1 and num("2\xa0064,10") == 2064.1
    assert num("0,00") is None and num("") is None and num("abc") is None and num(5) == 5.0
    assert norm_name('ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"') == norm_name("ООО «Еврохим-Волгакалий»")


def test_parse_products():
    rows = [tuple(HEADER),
            row(),
            row(UUID="u2", Код="00002", Наименование="Ведущий мост", Артикул="554831", **{"Единица измерения": "шт",
                'Цена: ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"': "21 862,60", "Неснижаемый остаток для склада Котельниково": "2",
                "Доп. поле: Срок поставки, дней": "130", "Доп. поле: Применимо на оборудовании": "Sandvik LH410", "Код ТН ВЭД": "8708 50",
                "Архивный": "да"}),
            row(UUID="u3", Код="00003", Наименование="Ведущий мост (дубль)", Артикул="554831"),
            tuple([None] * len(HEADER))]
    recs, rep = parse_products(rows)
    assert rep["rows"] == 3 and rep["duplicate_articles"] == ["554831"]
    assert rep["clients"] == ['ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"'] and rep["warehouses"] == ["Бишкек", "Котельниково"]
    a, b = recs[0], recs[1]
    assert a["unit"] == "шт" and a["sale_price"] is None and a["client_prices"] == [] and a["status"] == "active"   # «0,00» = пусто
    assert b["client_prices"] == [{"client_name": 'ООО "ЕВРОХИМ-ВОЛГАКАЛИЙ"', "price": 21862.6, "currency": "EUR"}]
    assert b["min_stock"] == {"Котельниково": 2.0} and b["lead_days"] == 130 and b["hs_code"] == "870850" and b["status"] == "archive"
    assert b["applicability_text"] == "Sandvik LH410" and rep["filled"]["client_prices"] == 1


def test_parse_products_wrong_file():
    recs, rep = parse_products([("Номер", "Дата"), ("1", "2")])
    assert recs == [] and "не выгрузка" in rep["error"]


def test_parse_stock_by_warehouse_and_read_rows(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Остатки по складам"])
    ws.append(["Наименование", "Артикул", "Ед. изм.", "Остаток", "Котельниково", "Куркино", "Химки (ТЕМП-ТУЛ)"])
    ws.append(["Карданный вал", "521820", "шт", 2, 0, 0, 2])
    ws.append(["Болт", "543168", "шт", "12", "12", "0", "0"])
    ws.append([None, None])
    f = tmp_path / "stock.xlsx"
    wb.save(f)
    recs, rep = parse_stock(read_rows(str(f)))
    assert rep["warehouses"] == ["Котельниково", "Куркино", "Химки (ТЕМП-ТУЛ)"] and rep["rows"] == 2
    assert recs[0]["by_warehouse"] == {"Химки (ТЕМП-ТУЛ)": 2.0} and recs[1]["total"] == 12.0


def test_parse_stock_goods_report_without_split():
    rows = [("Наименование", "Артикул", "Остаток", "Резерв", "Ожидание", "Доступно", "Ед. изм."), ("Реле", "564356", "3", "1", "2", "2", "")]
    recs, rep = parse_stock(rows)
    assert rep["warehouses"] == [] and recs[0]["reserved"] == 1.0 and recs[0]["in_transit"] == 2.0 and recs[0]["by_warehouse"] == {}
