"""Роли и серверная фильтрация полей (раздел 2 ТЗ).

Главное правило: поля, закрытые для роли, не передаются на клиент. Фильтрация выполняется на сервере
функцией `filter_payload` перед сериализацией ответа — интерфейс не «прячет», а просто не получает данных.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable


class Role(str, Enum):
    MANAGER = "manager"            # менеджер по продажам
    LOGISTICIAN = "logistician"    # логист / декларант
    SERVICE_HEAD = "service_head"  # руководитель сервисной службы
    CD = "cd"                      # коммерческий директор
    FD = "fd"                      # финансовый директор
    GD = "gd"                      # генеральный директор
    ADMIN = "admin"
    OBSERVER = "observer"


ROLE_TITLES = {
    Role.MANAGER: "Менеджер по продажам",
    Role.LOGISTICIAN: "Логист / декларант",
    Role.SERVICE_HEAD: "Руководитель сервисной службы",
    Role.CD: "Коммерческий директор",
    Role.FD: "Финансовый директор",
    Role.GD: "Генеральный директор",
    Role.ADMIN: "Администратор",
    Role.OBSERVER: "Наблюдатель",
}

# Роли, которым доступны себестоимость, маржа и финансовый блок
COST_VISIBLE_ROLES = {Role.CD, Role.FD, Role.GD}
# Роли, которым доступна цена продажи (менеджер видит итоговую цену только после утверждения — проверяется отдельно)
PRICE_VISIBLE_ROLES = {Role.CD, Role.FD, Role.GD, Role.MANAGER, Role.OBSERVER}

# Группы закрытых полей по именам ключей в JSON-ответах
COST_FIELDS = {
    "cost", "cost_total_rub", "cost_total_base_ccy", "items_rub", "items_fx", "margin_pct", "markup_pct",
    "profit_rub", "profit_before_finance_rub", "cash_gap", "cashflow", "finance_breakdown", "sensitivity",
    "customs_value_rub", "total_rub", "calc_result", "approval", "fx_components",
    "logistics", "service_calc", "history_checks", "overhead_pct_override", "overhead_override_comment",
    "avg_margin_pct", "sum_margin_rub",
}
PRICE_FIELDS = {"price_rub", "price_per_unit_rub", "price_with_vat_rub"}
LOGISTICS_INPUT_FIELDS = {"logistics", "batches"}
SERVICE_INPUT_FIELDS = {"service_calc", "service"}
FINANCE_FIELDS = {"cash_gap", "cashflow", "finance_breakdown", "fx_components", "sensitivity"}


def denied_fields(roles: Iterable[Role]) -> set[str]:
    """Множество ключей, которые нельзя отдавать пользователю с данным набором ролей."""
    roles = set(roles)
    if roles & COST_VISIBLE_ROLES:
        return set()
    denied: set[str] = set()
    if Role.ADMIN in roles and len(roles) == 1:
        return COST_FIELDS | PRICE_FIELDS | LOGISTICS_INPUT_FIELDS | SERVICE_INPUT_FIELDS
    if Role.MANAGER in roles:
        denied |= COST_FIELDS | LOGISTICS_INPUT_FIELDS | SERVICE_INPUT_FIELDS
    if Role.LOGISTICIAN in roles:
        denied |= COST_FIELDS | PRICE_FIELDS | FINANCE_FIELDS | SERVICE_INPUT_FIELDS
        denied.discard("logistics")
        denied.discard("batches")
        denied.discard("history_checks")
    if Role.SERVICE_HEAD in roles:
        denied |= COST_FIELDS | PRICE_FIELDS | FINANCE_FIELDS
        denied.discard("service_calc")
    if Role.OBSERVER in roles:
        denied |= COST_FIELDS
    # пересечение ролей расширяет видимость: убираем из denied то, что разрешено хотя бы одной роли
    if Role.LOGISTICIAN in roles:
        denied -= {"logistics", "batches", "history_checks"}
    if Role.SERVICE_HEAD in roles:
        denied -= {"service_calc", "service"}
    if Role.MANAGER in roles:
        denied -= PRICE_FIELDS
    return denied


def filter_payload(data: Any, roles: Iterable[Role]) -> Any:
    """Рекурсивно удаляет закрытые ключи из словарей/списков."""
    denied = denied_fields(roles)
    if not denied:
        return data
    return _strip(data, denied)


def _strip(obj: Any, denied: set[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v, denied) for k, v in obj.items() if k not in denied}
    if isinstance(obj, list):
        return [_strip(x, denied) for x in obj]
    return obj


def can_edit_block(roles: Iterable[Role], block: str) -> bool:
    roles = set(roles)
    if Role.GD in roles or Role.ADMIN in roles and block == "settings":
        return True
    mapping = {
        "manager": {Role.MANAGER},
        "logistics": {Role.LOGISTICIAN},
        "service": {Role.SERVICE_HEAD},
        "pricing": {Role.CD, Role.GD},
        "fd_visa": {Role.FD, Role.GD},
        "settings": {Role.ADMIN, Role.FD},
        "fact": {Role.LOGISTICIAN, Role.MANAGER, Role.SERVICE_HEAD, Role.FD},
    }
    return bool(roles & mapping.get(block, set()))
