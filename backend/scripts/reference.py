"""Справочники, нужные и стенду, и рабочей системе: типовые схемы оплаты, компании группы и маршруты поставки.

Демо-данных здесь нет (клиенты, заводы, сотрудники, выдуманные курсы — только в scripts.seed для стенда).
Повторный запуск безопасен: существующие записи не меняются.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models import LegalEntity, MarkupPolicy, PaymentScheme, RouteStep, SupplyRoute  # noqa: F401

SCHEMES = [
    ("30/70", [{"no": 1, "share_pct": 30, "event": "contract", "offset_days": 10}, {"no": 2, "share_pct": 70, "event": "delivery", "offset_days": 10}]),
    ("100 % предоплата", [{"no": 1, "share_pct": 100, "event": "contract", "offset_days": 10}]),
    ("20/70/10 с гарантией", [{"no": 1, "share_pct": 20, "event": "contract", "offset_days": 10, "guarantee": "advance_return", "guarantee_pct": 100, "guarantee_months": 6},
                             {"no": 2, "share_pct": 70, "event": "readiness", "offset_days": 5},
                             {"no": 3, "share_pct": 10, "event": "commissioning", "offset_days": 10}]),
]


def payment_schemes(db) -> None:
    for name, lines in SCHEMES:
        if not db.scalar(select(PaymentScheme).where(PaymentScheme.name == name)):
            db.add(PaymentScheme(name=name, lines=lines))


# запчасти, шаг 1: компании группы и типовые маршруты (вариант В: Китай → FCA склад экспедитора в CNY;
# Киргизия → DAP склад РФ в валюте закупки). Названия компаний — заготовки, их правят КД и ЗГД.
ENTITIES = [
    ("ru", "Компания в РФ", "RU", "RUB", "physical", True),
    ("kg", "Компания в Киргизии", "KG", "KGS", "virtual", False),
    ("cn", "Компания в Китае", "CN", "CNY", "none", False),
]
ROUTES = [   # (название, страна поставщика, [(продавец, покупатель, Incoterms, место, валюта, выпуск)])
    ("Поставщик КНР → РФ", "CN", [(None, "ru", "FCA", "склад экспедитора в КНР", "CNY", "RU")]),
    ("Поставщик КНР → компания в Китае → РФ", "CN", [(None, "cn", "EXW", "склад поставщика", "CNY", ""),
                                                   ("cn", "ru", "FCA", "склад экспедитора в КНР", "CNY", "RU")]),
    ("Поставщик КНР → компания в Китае → Киргизия → РФ", "CN", [(None, "cn", "EXW", "склад поставщика", "CNY", ""),
                                                              ("cn", "kg", "FCA", "склад экспедитора в КНР", "CNY", "KG"),
                                                              ("kg", "ru", "DAP", "склад РФ", "CNY", "")]),
    ("Поставщик Германии → Киргизия → РФ", "DE", [(None, "kg", "FCA", "склад поставщика", "EUR", "KG"),
                                                 ("kg", "ru", "DAP", "склад РФ", "EUR", "")]),
    ("Поставщик Германии → РФ", "DE", [(None, "ru", "FCA", "склад поставщика", "EUR", "RU")]),
    ("Поставщик Киргизии → Киргизия → РФ", "KG", [(None, "kg", "EXW", "склад поставщика", "KGS", "KG"),
                                                 ("kg", "ru", "DAP", "склад РФ", "KGS", "")]),
]


def seed_parts_setup(db) -> None:
    by_code = {}
    for code, name, country, cur, wh, sells in ENTITIES:
        e = db.scalar(select(LegalEntity).where(LegalEntity.code == code))
        if e is None:
            e = LegalEntity(code=code, name=name, country=country, currency=cur, warehouse=wh, sells_to_clients=sells)
            db.add(e)
            db.flush()
        by_code[code] = e.id
    for name, country, steps in ROUTES:
        if db.scalar(select(SupplyRoute).where(SupplyRoute.name == name)):
            continue
        r = SupplyRoute(name=name, supplier_country=country)
        for i, (seller, buyer, inc, place, cur, customs) in enumerate(steps, start=1):
            r.steps.append(RouteStep(no=i, seller_entity_id=by_code[seller] if seller else None, buyer_entity_id=by_code[buyer],
                                     incoterms=inc, place=place, currency=cur, customs_country=customs))
        db.add(r)
    db.flush()
    # стартовая политика наценок — черновиком: в силу вступает только после виз ЗГД и ФД
    if not db.scalar(select(MarkupPolicy)):
        db.add(MarkupPolicy(version_no=1, status="draft", reason="Стартовые ставки: компания в Китае 5 %, компания в Киргизии 6 %",
                            rules={"default": {by_code["cn"]: 5.0, by_code["kg"]: 6.0}, "by_group": [], "by_supplier": [], "min_margin_pct": None}))
        db.flush()


def run(db) -> None:
    payment_schemes(db)
    seed_parts_setup(db)
