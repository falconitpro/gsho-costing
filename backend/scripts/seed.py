"""Начальные данные для dev: пользователи по ролям, клиент, заводы, ТН ВЭД, схемы оплаты, курсы (синтетика).

Запуск: python -m scripts.seed  (из каталога backend, с настроенным DATABASE_URL)
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Client, Factory, FxRate, HsCode, PaymentScheme, User

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


if __name__ == "__main__":
    run()
