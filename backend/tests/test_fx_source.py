"""Курсы ЦБ РФ: в ежедневную загрузку входит киргизский сом (KGS) для компании группы в Киргизии."""
from app.adapters.fx_source import SUPPORTED, parse_daily_ids, parse_daily_xml

DAILY = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="05.11.2026" name="Foreign Currency Market">
<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name><Value>90,1000</Value></Valute>
<Valute ID="R01370"><NumCode>417</NumCode><CharCode>KGS</CharCode><Nominal>10</Nominal><Name>Киргизских сомов</Name><Value>10,3000</Value></Valute>
<Valute ID="R01375"><NumCode>156</NumCode><CharCode>CNY</CharCode><Nominal>1</Nominal><Name>Юань</Name><Value>12,5000</Value></Valute>
<Valute ID="R01335"><NumCode>398</NumCode><CharCode>KZT</CharCode><Nominal>100</Nominal><Name>Тенге</Name><Value>17,0000</Value></Valute>
</ValCurs>"""


def test_daily_includes_kgs_per_unit():
    assert "KGS" in SUPPORTED
    r = parse_daily_xml(DAILY)
    assert round(r["KGS"], 4) == 1.03 and r["USD"] == 90.1 and r["CNY"] == 12.5   # курс на 1 сом с учётом номинала
    assert "KZT" not in r                                                           # лишние валюты не загружаются


def test_daily_ids_for_history_request():
    assert parse_daily_ids(DAILY)["KGS"] == "R01370"
