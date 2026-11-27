"""Узлы и агрегаты (J-c, D-200): общий справочник, узлы модели, сопоставление старых текстов «узел» из применимости.

Общий справочник (model_id пусто) подходит для любой техники, в том числе других производителей; узлы модели ГШО
дополняют его. Названия на трёх языках нужны для загрузки каталогов завода (J-i): китайское или английское название
узла из каталога сопоставляется с этим справочником.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import EquipmentAssembly, PartApplicability

GENERAL = [  # код, русский, английский, китайский
    ("ENG", "Двигатель", "Engine", "发动机"),
    ("FUEL", "Топливная система", "Fuel system", "燃油系统"),
    ("COOL", "Система охлаждения", "Cooling system", "冷却系统"),
    ("AIR", "Система впуска и выпуска", "Intake and exhaust", "进排气系统"),
    ("TRANS", "Трансмиссия", "Transmission", "变速箱"),
    ("AXLE", "Мосты и редукторы", "Axles and final drives", "驱动桥"),
    ("BRAKE", "Тормозная система", "Brake system", "制动系统"),
    ("STEER", "Рулевое управление", "Steering", "转向系统"),
    ("HYD", "Гидросистема", "Hydraulic system", "液压系统"),
    ("ELEC", "Электрооборудование", "Electrical system", "电气系统"),
    ("CAB", "Кабина", "Cab", "驾驶室"),
    ("FRAME", "Рама и кузов", "Frame and body", "车架"),
    ("WORK", "Рабочее оборудование", "Work equipment", "工作装置"),
    ("UNDER", "Ходовая часть", "Undercarriage", "行走系统"),
    ("FILT", "Фильтры", "Filters", "滤清器"),
    ("LUBE", "Смазка и масла", "Lubrication", "润滑系统"),
    ("FIRE", "Пожаротушение и безопасность", "Fire suppression and safety", "灭火与安全系统"),
]


# частые короткие названия → узел общего справочника (сопоставление старых текстов и каталогов завода)
SYNONYMS = {
    "гидравлика": "Гидросистема", "гидравлическая система": "Гидросистема", "hydraulics": "Гидросистема", "液压": "Гидросистема",
    "электрика": "Электрооборудование", "электросистема": "Электрооборудование", "electrics": "Электрооборудование", "电气": "Электрооборудование",
    "двс": "Двигатель", "мотор": "Двигатель", "кпп": "Трансмиссия", "коробка передач": "Трансмиссия", "gearbox": "Трансмиссия",
    "тормоза": "Тормозная система", "brakes": "Тормозная система", "рулевое": "Рулевое управление", "мосты": "Мосты и редукторы",
    "фильтр": "Фильтры", "фильтры": "Фильтры", "охлаждение": "Система охлаждения", "топливная": "Топливная система",
    "ходовая": "Ходовая часть", "кузов": "Рама и кузов", "рама": "Рама и кузов",
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


def ensure_general(db: Session) -> int:
    have = {norm(a.name_ru) for a in db.scalars(select(EquipmentAssembly).where(EquipmentAssembly.model_id.is_(None))).all()}
    made = 0
    for i, (code, ru, en, cn) in enumerate(GENERAL, start=1):
        if norm(ru) not in have:
            db.add(EquipmentAssembly(code=code, name_ru=ru, name_en=en, name_cn=cn, position=i * 10))
            made += 1
    db.flush()
    return made


def for_model(db: Session, model_id: Optional[str]) -> list[EquipmentAssembly]:
    """Общие узлы и узлы модели — что можно выбрать в применимости."""
    st = select(EquipmentAssembly).where(EquipmentAssembly.is_active.is_(True))
    st = st.where(or_(EquipmentAssembly.model_id.is_(None), EquipmentAssembly.model_id == model_id)) if model_id else st.where(EquipmentAssembly.model_id.is_(None))
    return db.scalars(st.order_by(EquipmentAssembly.model_id.is_(None), EquipmentAssembly.position, EquipmentAssembly.name_ru)).all()


def match(db: Session, text: str, model_id: Optional[str]) -> Optional[EquipmentAssembly]:
    """Узел по тексту на любом из трёх языков: сначала узлы модели, затем общий справочник."""
    n = norm(text)
    if not n:
        return None
    cands = for_model(db, model_id)
    for a in cands:
        if n in (norm(a.name_ru), norm(a.name_en), norm(a.name_cn), norm(a.code)):
            return a
    syn = norm(SYNONYMS.get(n, ""))
    if syn:
        return next((a for a in cands if norm(a.name_ru) == syn), None)
    return None


def migrate(db: Session, apply: bool) -> dict:
    """Перенос текстов «узел» из применимости в справочник. Совпало с общим или узлом модели — ссылка; не совпало —
    новый узел: у модели ГШО — узел этой модели, у техники других производителей — в общем справочнике."""
    rep = {"general_new": 0, "linked": 0, "created": [], "rows": 0}
    if apply:
        rep["general_new"] = ensure_general(db)
    else:
        have = {norm(a.name_ru) for a in db.scalars(select(EquipmentAssembly).where(EquipmentAssembly.model_id.is_(None))).all()}
        rep["general_new"] = sum(1 for _c, ru, _e, _z in GENERAL if norm(ru) not in have)
    pending: dict[tuple, str] = {}
    gen_names = {norm(ru): ru for _c, ru, _e, _z in GENERAL}
    for a in db.scalars(select(PartApplicability).where(PartApplicability.assembly_id.is_(None), PartApplicability.assembly != "")).all():
        rep["rows"] += 1
        found = match(db, a.assembly, a.equipment_model_id)
        if found is None and not apply and (norm(a.assembly) in gen_names or norm(SYNONYMS.get(norm(a.assembly), "")) in gen_names):
            rep["linked"] += 1
            continue
        if found is None:
            key = (a.equipment_model_id, norm(a.assembly))
            if key not in pending:
                pending[key] = a.assembly.strip()
                rep["created"].append(f"{'узел модели' if a.equipment_model_id else 'общий'}: {a.assembly.strip()}")
                if apply:
                    db.add(EquipmentAssembly(model_id=a.equipment_model_id, name_ru=a.assembly.strip()[:255], position=900))
                    db.flush()
            if apply:
                found = match(db, a.assembly, a.equipment_model_id)
            else:
                rep["linked"] += 1
                continue
        if apply and found is not None:
            a.assembly_id = found.id
        rep["linked"] += 1
    return rep
