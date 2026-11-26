"""Справочники сервиса (I-0, D-191): виды работ с нормативами, допуски, категории сроков реакции.

Черновик (решение 8 владельца — готовит разработчик): все записи с признаком is_draft, их проверяет и утверждает
руководитель сервиса. Нормативы и сроки — ориентир для обсуждения, не расчётные данные: пустое значение — «не задано»,
а не 0. Загрузка — scripts/service_refs_import.py (пробный прогон по умолчанию); уже заведённые коды не меняются.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ServicePermit, ServiceSlaLevel, ServiceWorkType

PERMITS = [
    ("EB3", "Электробезопасность, группа III (до 1000 В)", 12),
    ("EB4", "Электробезопасность, группа IV (выше 1000 В)", 12),
    ("PB-GR", "Промышленная безопасность: горные работы (Ростехнадзор)", 60),
    ("PB-UG", "Допуск к работам в подземных выработках", 12),
    ("HEIGHT", "Работы на высоте", 36),
    ("GPM", "Стропальщик / работы с грузоподъёмными механизмами", 12),
    ("HOT", "Огневые работы", 12),
    ("PRESS", "Сосуды и гидросистемы под давлением", 12),
    ("OT", "Охрана труда, общий курс", 36),
    ("FIRST-AID", "Первая помощь пострадавшим", 36),
]

SLA = [  # код, название, реакция, выезд, восстановление (часы), календарь
    ("CRIT", "Критично: машина стоит, нет резерва", 1, 24, 72, "24x7"),
    ("HIGH", "Высокий: машина стоит, есть резерв", 4, 48, 120, "24x7"),
    ("NORM", "Обычный: машина работает с ограничениями", 8, 72, None, "business"),
    ("PLAN", "Плановые работы (ТО, осмотр)", 24, None, None, "business"),
    ("CONS", "Консультация без выезда", 8, None, None, "business"),
]

T_BASE = ["Получен допуск на площадку, проведён инструктаж", "Машина остановлена и заблокирована (LOTO)"]
T_END = ["Машина проверена на холостом ходу и под нагрузкой", "Рабочее место убрано, отходы и масла утилизированы"]

WORK_TYPES = [  # код, название, категория, тип техники, норматив ч, бригада, допуски, чек-лист
    ("DIAG-GEN", "Выездная диагностика неисправности", "diagnostics", "", 4, 1, ["OT", "PB-GR"],
     T_BASE + ["Считаны коды ошибок контроллера", "Осмотр, фото неисправного узла", "Заполнена дефектная ведомость"]),
    ("DIAG-HYD", "Диагностика гидросистемы (давления, утечки)", "diagnostics", "", 3, 1, ["OT", "PB-GR", "PRESS"],
     T_BASE + ["Замерены рабочие давления", "Проверены РВД и соединения"]),
    ("TO-250", "ТО-250 моточасов", "maintenance", "", 6, 2, ["OT", "PB-GR"],
     T_BASE + ["Заменены масло и фильтры по карте ТО", "Смазка по карте", "Записаны показания моточасов"] + T_END),
    ("TO-500", "ТО-500 моточасов", "maintenance", "", 10, 2, ["OT", "PB-GR"],
     T_BASE + ["Выполнен объём ТО-250", "Заменены фильтры гидросистемы и топлива", "Проверены зазоры и натяжения"] + T_END),
    ("TO-1000", "ТО-1000 моточасов", "maintenance", "", 16, 2, ["OT", "PB-GR", "PRESS"],
     T_BASE + ["Выполнен объём ТО-500", "Заменено масло трансмиссии и мостов", "Проба масла отправлена в лабораторию"] + T_END),
    ("RPR-ENG", "Ремонт двигателя (узловой)", "repair", "", 24, 2, ["OT", "PB-GR", "GPM"],
     T_BASE + ["Снятые детали промаркированы", "Заменённые детали сохранены для гарантии"] + T_END),
    ("RPR-HYD", "Замена гидроцилиндра / насоса", "repair", "", 8, 2, ["OT", "PB-GR", "GPM", "PRESS"],
     T_BASE + ["Давление в системе сброшено", "Система прокачана после замены"] + T_END),
    ("RPR-EL", "Ремонт электрооборудования", "repair", "", 6, 1, ["OT", "PB-GR", "EB3"],
     T_BASE + ["Проверено отсутствие напряжения"] + T_END),
    ("RPR-WELD", "Сварочный ремонт металлоконструкций", "repair", "", 8, 2, ["OT", "PB-GR", "HOT"],
     T_BASE + ["Оформлен наряд-допуск на огневые работы", "Контроль сварного шва"] + T_END),
    ("COMM-NEW", "Пусконаладка новой машины", "commissioning", "", 16, 2, ["OT", "PB-GR", "EB3"],
     T_BASE + ["Проверена комплектность по упаковочному листу", "Обучение операторов, лист инструктажа"] + T_END),
    ("UG-LHD-TO", "ТО погрузочно-доставочной машины в подземной выработке", "maintenance", "Погрузочно-доставочная машина", 12, 2,
     ["OT", "PB-GR", "PB-UG"], T_BASE + ["Проверена система пожаротушения", "Проверены тормоза и аварийный останов"] + T_END),
    ("CONS-REM", "Консультация по телефону / видеосвязи", "other", "", 1, 1, [], ["Описание проблемы и рекомендация записаны"]),
]


def plan(db: Session) -> dict[str, list[str]]:
    """Какие коды будут добавлены (уже заведённые не трогаем)."""
    have = {
        "permits": set(db.scalars(select(ServicePermit.code)).all()),
        "sla": set(db.scalars(select(ServiceSlaLevel.code)).all()),
        "work_types": set(db.scalars(select(ServiceWorkType.code)).all()),
    }
    return {"permits": [c for c, *_ in PERMITS if c not in have["permits"]],
            "sla": [c for c, *_ in SLA if c not in have["sla"]],
            "work_types": [c for c, *_ in WORK_TYPES if c not in have["work_types"]]}


def load_drafts(db: Session) -> dict[str, list[str]]:
    todo = plan(db)
    for code, name, months in PERMITS:
        if code in todo["permits"]:
            db.add(ServicePermit(code=code, name=name, valid_months=months, is_draft=True))
    for code, name, r, a, x, cal in SLA:
        if code in todo["sla"]:
            db.add(ServiceSlaLevel(code=code, name=name, reaction_hours=r, arrival_hours=a, restore_hours=x, calendar=cal, is_draft=True))
    for code, name, cat, kind, hours, crew, permits, checklist in WORK_TYPES:
        if code in todo["work_types"]:
            db.add(ServiceWorkType(code=code, name=name, category=cat, machine_kind=kind, norm_hours=hours, crew_size=crew,
                                   permit_codes=list(permits), checklist=list(checklist), is_draft=True))
    db.flush()
    return todo
