"""Начальные данные для dev: пользователи по ролям, клиент, заводы, ТН ВЭД, схемы оплаты, курсы (синтетика).

Запуск: python -m scripts.seed  (из каталога backend, с настроенным DATABASE_URL)
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Client, Factory, FxRate, HsCode, LegalEntity, MarkupPolicy, PaymentScheme, RouteStep, SupplyRoute, User

USERS = [
    ("manager@example.com", "Бочаров Егор (менеджер)", ["manager"]),
    ("logist@example.com", "Каталкина Оксана (логист)", ["logistician"]),
    ("service@example.com", "Шереметьев Дмитрий (сервис)", ["service_head"]),
    ("cd@example.com", "Волохов Сергей (КД)", ["cd"]),
    ("fd@example.com", "Склярова Ирина (ФД)", ["fd"]),
    ("gd@example.com", "Добров Евгений (ЗГД)", ["gd"]),
    ("admin@example.com", "Администратор", ["admin"]),
    ("observer@example.com", "Наблюдатель", ["observer"]),
    ("lawyer@example.com", "Земсков Евгений (юрист)", ["lawyer"]),
]
# прежние демонстрационные имена — при повторном запуске заменяются на актуальные (имена, заданные вручную, не трогаем)
OLD_NAMES = {"Иванов Иван (менеджер)", "Петров Пётр (логист)", "Сидоров Сергей (сервис)", "Кузнецова Анна (КД)", "Смирнов Олег (ФД)",
             "Орлов Дмитрий (ГД)", "Орлов Дмитрий (ЗГД)"}
HS = [
    ("8704101090", "Автомобили-самосвалы для эксплуатации в условиях бездорожья, прочие", 5.0, 22.0, True, ["ТР ТС 010/2011", "ПСМ"]),
    ("8429519000", "Погрузчики одноковшовые фронтальные, прочие", 0.0, 22.0, True, ["ТР ТС 010/2011", "ПСМ"]),
    ("8430410000", "Машины бурильные самоходные", 0.0, 22.0, True, ["ТР ТС 010/2011", "ЭПБ"]),
    ("8428330000", "Конвейеры ленточные", 0.0, 22.0, False, ["ТР ТС 010/2011"]),
]
SCHEMES = [
    ("30/70", [{"no": 1, "share_pct": 30, "event": "contract", "offset_days": 10}, {"no": 2, "share_pct": 70, "event": "delivery", "offset_days": 10}]),
    ("100 % предоплата", [{"no": 1, "share_pct": 100, "event": "contract", "offset_days": 10}]),
    ("20/70/10 с гарантией", [{"no": 1, "share_pct": 20, "event": "contract", "offset_days": 10, "guarantee": "advance_return", "guarantee_pct": 100, "guarantee_months": 6},
                             {"no": 2, "share_pct": 70, "event": "readiness", "offset_days": 5},
                             {"no": 3, "share_pct": 10, "event": "commissioning", "offset_days": 10}]),
]


DEV_PASSWORD = "berghaus-demo-2026"


def run() -> None:
    init_db()
    with SessionLocal() as db:
        for email, name, roles in USERS:
            u = db.scalar(select(User).where(User.email == email))
            if not u:
                db.add(User(external_id=email, email=email, display_name=name, roles=roles))
            elif u.display_name in OLD_NAMES:
                u.display_name = name
        db.flush()
        # тестовый стенд: у демо-пользователей общий пароль для страницы входа (только dev; в рабочем режиме пароли задаёт ЗГД)
        from app.core.auth import hash_password
        from app.core.config import get_settings
        if get_settings().is_dev:
            for u in db.scalars(select(User)).all():
                if not u.password_hash:
                    u.password_hash = hash_password(DEV_PASSWORD)
        mgr = db.scalar(select(User).where(User.email == "manager@example.com"))
        if not db.scalar(select(Client).where(Client.inn == "7701234567")):
            db.add(Client(name="ООО «Рудник Северный»", inn="7701234567", address="г. Норильск", sites=[{"name": "Месторождение Северное", "region": "Красноярский край"}],
                          contacts=[{"name": "Главный механик", "email": "mech@rudnik.example", "phone": "+7 900 000-00-00"}], manager_id=mgr.id))
        for name, addr in (("Xuzhou XCMG Mining Machinery Co., Ltd", "Xuzhou, Jiangsu"), ("Shandong Tiangong Mining Equipment Co., Ltd", "Jinan, Shandong")):
            if not db.scalar(select(Factory).where(Factory.name == name)):
                db.add(Factory(name=name, address=addr, typical_production_days=120))
        for code, desc, duty, vat, util, cert in HS:
            if not db.scalar(select(HsCode).where(HsCode.code == code, HsCode.valid_to.is_(None))):
                db.add(HsCode(code=code, description=desc, duty_pct=duty, vat_pct=vat, util_fee=util, certification=cert, valid_from=date(2026, 1, 1)))
        for name, lines in SCHEMES:
            if not db.scalar(select(PaymentScheme).where(PaymentScheme.name == name)):
                db.add(PaymentScheme(name=name, lines=lines))
        seed_parts_setup(db)
        mark_supplies(db)           # раздел «Поставщики»: ГШО / запасные части — по фактическому использованию
        parts_manufacturer_paus(db)  # разово (05.11.2026): у всех запчастей каталога производитель PAUS, Германия
        parts_supplier_grp(db)       # разово (05.11.2026): запчасти из МойСклад — поставщик GRP, маршрут Германия → Киргизия → РФ
        from app.services.moysklad_import import relink_client_prices
        relink_client_prices(db)    # договорные цены запчастей → клиенты, заведённые после переноса
        # синтетическая история курса CNY/RUB за 4 года (в проде — загрузка с cbr.ru: POST /api/admin/fx/history)
        if not db.scalar(select(FxRate).where(FxRate.currency == "CNY")):
            import sys
            sys.path.insert(0, ".")
            from tests.fixtures import synthetic_cny_history
            for d, r in synthetic_cny_history(date.today(), years=4, start_rate=11.0):
                db.add(FxRate(on=d, currency="CNY", rate=r, source="cbr"))
            db.add(FxRate(on=date.today(), currency="USD", rate=90.0, source="cbr"))
            db.add(FxRate(on=date.today(), currency="EUR", rate=98.0, source="cbr"))
        db.commit()
    print("seed: ok")


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


if __name__ == "__main__":
    run()


def mark_supplies(db) -> int:
    """Что поставляет поставщик: ГШО — есть модели в каталоге или позиции сделок; запчасти — карточки, запросы цен, заказы.
    Отметки только добавляются (снимает их пользователь); поставщик без признаков — ГШО (раздел раньше назывался «Заводы»)."""
    from sqlalchemy import select
    from app.models import EquipmentModel, Factory, Part, Position, PurchaseOrder, SupplierRfq
    gsho = set(db.scalars(select(EquipmentModel.factory_id)).all()) | set(db.scalars(select(Position.factory_id)).all())
    parts = set(db.scalars(select(Part.supplier_id).where(Part.supplier_id.is_not(None))).all()) \
        | set(db.scalars(select(SupplierRfq.supplier_id)).all()) | set(db.scalars(select(PurchaseOrder.supplier_id).where(PurchaseOrder.supplier_id.is_not(None))).all())
    n = 0
    for f in db.scalars(select(Factory)).all():
        cur = list(f.supplies or [])
        new = cur + [k for k, ids in (("gsho", gsho), ("parts", parts)) if f.id in ids and k not in cur]
        if not new:
            new = ["gsho"]
        if new != cur:
            f.supplies = new
            n += 1
    db.flush()
    return n


def parts_manufacturer_paus(db) -> int:
    """Разовая правка по решению владельца (05.11.2026): всем запчастям, которые есть в каталоге на момент выкатки, —
    производитель PAUS, страна происхождения Германия. Прежние значения сохраняются в журнале интеграций (откат возможен).
    Повторно не выполняется: карточки, заведённые позже, не трогает."""
    from sqlalchemy import select
    from app.models import IntegrationLog, Part
    if db.scalars(select(IntegrationLog).where(IntegrationLog.system == "migration", IntegrationLog.operation == "parts_manufacturer_paus")).first():
        return 0
    backup, n = [], 0
    for p in db.scalars(select(Part)).all():
        if p.manufacturer == "PAUS" and p.origin_country == "Германия":
            continue
        if (p.manufacturer or "") not in ("", "PAUS") or (p.origin_country or "") not in ("", "Германия"):
            backup.append({"id": p.id, "article": p.article, "manufacturer": p.manufacturer or "", "origin_country": p.origin_country or ""})
        p.manufacturer, p.origin_country = "PAUS", "Германия"
        n += 1
    db.add(IntegrationLog(system="migration", operation="parts_manufacturer_paus", ok=True,
                          details={"updated": n, "backup_count": len(backup), "backup": backup}))
    db.flush()
    return n


GRP_ROUTE = "Поставщик Германии → Киргизия → РФ"


def parts_supplier_grp(db) -> int:
    """Разовая правка по решению владельца (05.11.2026): всем запчастям, перенесённым из МойСклад, — основной поставщик GRP
    и маршрут «Поставщик Германии → Киргизия → РФ». Поставщик GRP заводится (Германия, запасные части, утверждён), если его нет.
    Прежние значения — в журнале интеграций; повторно не выполняется."""
    from datetime import datetime, timezone
    from sqlalchemy import func, or_, select
    from app.models import Factory, IntegrationLog, Part, SupplyRoute
    if db.scalars(select(IntegrationLog).where(IntegrationLog.system == "migration", IntegrationLog.operation == "parts_supplier_grp")).first():
        return 0
    route = db.scalars(select(SupplyRoute).where(SupplyRoute.name == GRP_ROUTE)).first()
    if route is None:
        return 0                                   # маршрут ещё не заведён — правка выполнится при следующем деплое
    grp = db.scalars(select(Factory).where(or_(func.upper(Factory.short_name) == "GRP", func.upper(Factory.name) == "GRP"))).first() \
        or db.scalars(select(Factory).where(or_(Factory.name.ilike("GRP %"), Factory.name.ilike("GRP,%")))).first()
    created = grp is None
    if created:
        grp = Factory(name="GRP", short_name="GRP", country="DE", language="de", default_currency="EUR", default_basis="FCA", supplier_status="",
                      supplies=["parts"], approval_status="approved", approved_at=datetime.now(timezone.utc),
                      notes="Заведён автоматически: основной поставщик запчастей, перенесённых из МойСклад")
        db.add(grp)
        db.flush()
    if "parts" not in (grp.supplies or []):
        grp.supplies = [*(grp.supplies or []), "parts"]
    grp.default_route_id = grp.default_route_id or route.id
    backup, n = [], 0
    for p in db.scalars(select(Part).where(Part.source == "moysklad")).all():
        if p.supplier_id == grp.id and p.route_id == route.id:
            continue
        if p.supplier_id or p.route_id:
            backup.append({"id": p.id, "article": p.article, "supplier_id": p.supplier_id, "route_id": p.route_id})
        p.supplier_id, p.route_id = grp.id, route.id
        n += 1
    db.add(IntegrationLog(system="migration", operation="parts_supplier_grp", ok=True,
                          details={"updated": n, "supplier_id": grp.id, "supplier_created": created, "route_id": route.id, "backup": backup}))
    db.flush()
    return n
