"""Курсы ЦБ РФ: XML_daily.asp (на дату) и XML_dynamic.asp (история с 2014 г.).

Курс нормируется на единицу валюты (ЦБ публикует номинал, например 10 CNY до 2023 г.).
При недоступности сервиса вызывающий код использует последний загруженный курс с пометкой «cbr_stale».
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, timedelta

CBR_CODES = {"USD": "R01235", "EUR": "R01239", "CNY": "R01375"}
# валюты, курсы которых загружаются с cbr.ru: KGS — для компании группы в Киргизии (модуль запчастей)
SUPPORTED = ("CNY", "USD", "EUR", "KGS")


def parse_daily_xml(xml_text: str) -> dict[str, float]:
    root = ET.fromstring(xml_text)
    out: dict[str, float] = {"RUB": 1.0}
    for v in root.findall("Valute"):
        code = v.findtext("CharCode")
        if code in SUPPORTED:
            nominal = float(v.findtext("Nominal").replace(",", "."))
            value = float(v.findtext("Value").replace(",", "."))
            out[code] = value / nominal
    return out


def parse_daily_ids(xml_text: str) -> dict[str, str]:
    """Внутренние коды ЦБ (атрибут ID у Valute, напр. R01235) по буквенному коду — для запроса истории."""
    root = ET.fromstring(xml_text)
    return {v.findtext("CharCode"): v.get("ID") for v in root.findall("Valute") if v.get("ID")}


def parse_dynamic_xml(xml_text: str) -> list[tuple[date, float]]:
    root = ET.fromstring(xml_text)
    pts = []
    for r in root.findall("Record"):
        d = date(*[int(x) for x in reversed(r.get("Date").split("."))])
        nominal = float(r.findtext("Nominal").replace(",", "."))
        value = float(r.findtext("Value").replace(",", "."))
        pts.append((d, value / nominal))
    return pts


class CbrFxSource:
    def __init__(self, daily_url: str, dynamic_url: str):
        self.daily_url, self.dynamic_url = daily_url, dynamic_url

    def daily(self, on: date) -> dict[str, float]:   # pragma: no cover - сеть
        import httpx
        r = httpx.get(self.daily_url, params={"date_req": on.strftime("%d/%m/%Y")}, timeout=20)
        r.raise_for_status()
        return parse_daily_xml(r.text)

    def history(self, currency: str, start: date, end: date) -> list[tuple[date, float]]:   # pragma: no cover - сеть
        import httpx
        code = CBR_CODES.get(currency)
        if not code:   # код ЦБ берём из ежедневного справочника, а не задаём вручную
            r = httpx.get(self.daily_url, params={"date_req": end.strftime("%d/%m/%Y")}, timeout=20)
            r.raise_for_status()
            code = parse_daily_ids(r.text).get(currency)
            if not code:
                raise ValueError(f"ЦБ РФ не публикует курс {currency}")
        pts: list[tuple[date, float]] = []
        cur = start
        while cur <= end:   # сервис отдаёт не более ~ года за запрос
            chunk_end = min(end, cur + timedelta(days=360))
            r = httpx.get(self.dynamic_url, params={"date_req1": cur.strftime("%d/%m/%Y"), "date_req2": chunk_end.strftime("%d/%m/%Y"),
                                                    "VAL_NM_RQ": code}, timeout=30)
            r.raise_for_status()
            pts.extend(parse_dynamic_xml(r.text))
            cur = chunk_end + timedelta(days=1)
        return pts
