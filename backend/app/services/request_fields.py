"""Обязательные поля заявки (D-069): что требовать при создании позиции и что — перед «Отправить на расчёт».

При ручном вводе до запроса заводу известны только требования клиента: цену, модель, иногда и завод узнаём из ответа
завода. Поэтому по умолчанию при создании требуются тип техники и количество, а завод, модель, цена и валюта —
перед отправкой на расчёт. ЗГД (или администратор) меняет набор в «Настройки → Заявка».
"""
from __future__ import annotations

from typing import Any

# ключ → (название, код поля опросника RFQ)
FIELDS: dict[str, tuple[str, str]] = {
    "machine_type": ("Тип техники", "1.1"),
    "qty": ("Количество", "13.4"),
    "factory": ("Завод", "0.7"),
    "model": ("Модель", "1.2"),
    "unit_price": ("Цена за единицу", "13.5"),
    "currency": ("Валюта цены", "13.1"),
    "basis": ("Базис поставки", "13.2"),
    "delivery_point": ("Пункт поставки", "13.3"),
    "production_term": ("Срок производства", "14.1"),
    "warranty": ("Гарантия", "14.4"),
}
DEFAULT = {"create": ["machine_type", "qty"], "calc": ["factory", "model", "qty", "unit_price", "currency"]}


def normalize(v: Any) -> dict[str, list[str]]:
    v = v if isinstance(v, dict) else {}
    out = {}
    for stage in ("create", "calc"):
        keys = v.get(stage)
        keys = DEFAULT[stage] if keys is None else keys
        out[stage] = [k for k in FIELDS if k in set(keys or [])]
    # обязательное при создании обязательно и для расчёта
    out["calc"] = [k for k in FIELDS if k in set(out["calc"]) | set(out["create"])]
    return out


def get(db) -> dict[str, list[str]]:
    from . import settings_service
    return normalize(settings_service.get(db, "request_required_fields"))


def is_filled(key: str, p: dict) -> bool:
    if key == "machine_type":
        return bool((p.get("machine_type") or "").strip())
    if key == "qty":
        return (p.get("qty") or 0) > 0
    if key == "factory":
        return bool(p.get("factory_id") or p.get("factory"))
    if key == "model":
        return bool((p.get("model") or "").strip())
    if key == "unit_price":
        return (p.get("unit_price") or 0) > 0
    if key == "currency":
        return bool(p.get("currency"))
    if key == "basis":
        return bool(p.get("basis"))
    if key == "delivery_point":
        return bool((p.get("delivery_point") or "").strip())
    if key == "production_term":
        from .production_term import normalize as pt
        return bool(pt(p.get("production_term")))
    if key == "warranty":
        return bool(p.get("warranty_months") or p.get("warranty_hours"))
    return True


def missing(keys: list[str], p: dict) -> list[str]:
    return [FIELDS[k][0] for k in keys if k in FIELDS and not is_filled(k, p)]


def public(db) -> dict:
    """Для формы позиции: какие поля отметить звёздочкой (создание) и «нужно для расчёта»."""
    r = get(db)
    return {**r, "fields": {k: {"title": t, "code": c} for k, (t, c) in FIELDS.items()}}


def codes(db, stage: str = "create") -> set[str]:
    """Коды опросника RFQ, обязательные на этапе (для разбора файла и подсветки в форме)."""
    return {FIELDS[k][1] for k in get(db)[stage]}
