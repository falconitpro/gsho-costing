"""Роли и серверная фильтрация полей (раздел 2 ТЗ; задача 11 — уровни доступа как данные).

Главное правило: поля, закрытые для роли, не передаются на клиент. Фильтрация выполняется на сервере
функцией `filter_payload` перед сериализацией ответа — интерфейс не «прячет», а просто не получает данных.

Права описываются матрицей «роль × группа полей → уровень» (0 — не видит, 1 — видит, 2 — редактирует).
Стартовая матрица задана здесь (DEFAULT_MATRIX); администратор и ЗГД меняют её в интерфейсе
(таблица access_rules), а для отдельных сотрудников задают исключения (access_user_overrides).
Итоговые права пользователя = максимум по его ролям → затем исключения пользователя (приоритет у исключения).
Правило «ЗГД ⊇ КД»: пользователь с ролью ЗГД получает все права КД.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class Role(str, Enum):
    MANAGER = "manager"            # менеджер по продажам
    LOGISTICIAN = "logistician"    # логист / декларант
    SERVICE_HEAD = "service_head"  # руководитель сервисной службы
    CD = "cd"                      # коммерческий директор
    FD = "fd"                      # финансовый директор
    GD = "gd"                      # заместитель генерального директора (ЗГД)
    ADMIN = "admin"
    OBSERVER = "observer"
    PURCHASING = "purchasing"      # закупки (заготовка роли, задача 6)
    LAWYER = "lawyer"              # юрист: тендерная документация, договор, протокол разногласий
    CLERK = "clerk"                # делопроизводитель (офис-менеджер): Канцелярия — регистрация писем и документов, исполнители, сроки (D-128)
    ENGINEER = "engineer"          # инженер сервиса (D-180, D-181): свои наряды, машины и площадки по ним; сделок, цен и себестоимости не видит
    PAYROLL = "payroll"            # бухгалтер по заработной плате (D-205): блок «Персонал» — кадры, личные данные, оплата труда; сделок не видит


ROLE_TITLES = {
    Role.MANAGER: "Менеджер по продажам",
    Role.LOGISTICIAN: "Логист / декларант",
    Role.SERVICE_HEAD: "Руководитель сервисной службы",
    Role.CD: "Коммерческий директор",
    Role.FD: "Финансовый директор",
    Role.GD: "Заместитель генерального директора",
    Role.ADMIN: "Администратор",
    Role.OBSERVER: "Наблюдатель",
    Role.PURCHASING: "Закупки",
    Role.LAWYER: "Юрист",
    Role.CLERK: "Делопроизводитель (офис-менеджер)",
    Role.ENGINEER: "Инженер сервиса",
    Role.PAYROLL: "Бухгалтер по заработной плате",
}

NONE, READ, EDIT = 0, 1, 2
LEVEL_TITLES = {NONE: "не видит", READ: "видит", EDIT: "редактирует"}

# Роли, которым доступны себестоимость, маржа и финансовый блок (для обратной совместимости)
COST_VISIBLE_ROLES = {Role.CD, Role.FD, Role.GD}
PRICE_VISIBLE_ROLES = {Role.CD, Role.FD, Role.GD, Role.MANAGER, Role.OBSERVER}

# Группы закрытых полей по именам ключей в JSON-ответах
COST_FIELDS = {
    "cost", "cost_total_rub", "cost_total_base_ccy", "items_rub", "items_fx", "margin_pct", "markup_pct",
    "profit_rub", "profit_before_finance_rub", "cash_gap", "cashflow", "finance_breakdown", "sensitivity",
    "customs_value_rub", "total_rub", "calc_result", "approval", "fx_components",
    "logistics", "service_calc", "history_checks", "overhead_pct_override", "overhead_override_comment",
    "avg_margin_pct", "sum_margin_rub", "guarantee_cost_rub",
}
PRICE_FIELDS = {"price_rub", "price_per_unit_rub", "price_with_vat_rub", "price_amount", "price_fx_rate", "price_vat_included", "price_vat_pct", "price_input_amount"}
LOGISTICS_INPUT_FIELDS = {"logistics", "batches"}
SERVICE_INPUT_FIELDS = {"service_calc", "service"}
FINANCE_FIELDS = {"cash_gap", "cashflow", "finance_breakdown", "fx_components", "sensitivity"}
FACTORY_PRICE_HISTORY_FIELDS = {"factory_price_history"}
CLIENT_PRICE_HISTORY_FIELDS = {"client_kp_history", "client_price_history", "kp_history", "last_kp"}
# Финансовая аналитика: суммы, маржа, портфель, история цен — в карточках клиентов и поставщиков, списке сделок, отчётах
FINANCE_ANALYTICS_FIELDS = {"kp_totals", "avg_margin_pct", "sum_won_rub", "finance_analytics",
                            # раздел «Финансы» (вариант Б): остатки, ликвидность, полный экспорт, снимок источника обязательства
                            "available_balance", "projected_balance", "liquidity", "full_finance_export", "basis_snapshot"}
PAYMENT_TERMS_FIELDS = {"client_payments", "client_payments_alt", "factory_payments", "bank_guarantee", "advance_guarantee"}
LOCAL_OPTIONS_FIELDS = {"local_options", "local_option_cost_rub"}

# Группы доступа: код → (название, ключи JSON, которые закрываются при уровне 0)
FIELD_GROUPS: dict[str, tuple[str, set[str]]] = {
    "cost": ("Себестоимость, маржа, денежный поток", COST_FIELDS),
    "price": ("Цена продажи", PRICE_FIELDS),
    "logistics_input": ("Блок логиста (партии, статьи)", LOGISTICS_INPUT_FIELDS | {"history_checks"}),
    "service_input": ("Блок сервиса", SERVICE_INPUT_FIELDS),
    "factory_price_history": ("История цен и сроков завода", FACTORY_PRICE_HISTORY_FIELDS),
    "client_price_history": ("История КП и цен клиентам", CLIENT_PRICE_HISTORY_FIELDS),
    "finance_analytics": ("Финансовая аналитика: суммы, маржа, портфель, отчёты (карточки клиентов и поставщиков, список сделок)", FINANCE_ANALYTICS_FIELDS),
    # раздел «Финансы» (вариант Б): платёжный календарь, счета, остатки, операции. Отдельно от finance_analytics,
    # чтобы закрыть раздел КД, не отнимая у него суммы в сделках, карточках и сводке (D-116, заменяет D-040)
    "finance_section": ("Раздел «Финансы»: платёжный календарь, счета, остатки, банковские операции", set()),
    "payment_terms": ("Условия оплаты клиента и завода, БГ", PAYMENT_TERMS_FIELDS),
    "local_options": ("Опции, реализуемые в России", LOCAL_OPTIONS_FIELDS),
    "tech_params": ("Таблица технических характеристик и запрос заводу", set()),
    "images": ("Изображения и чертежи", set()),
    # D-077: просмотр файлов сделки — всем, кто видит сделку; скачивание — по этой группе или по одобренному запросу
    "file_download": ("Скачивание файлов сделок (просмотр в системе — всем, кто видит сделку)", set()),
    "kp_images_select": ("Выбор файлов и бренда для КП", set()),
    "catalog": ("Каталог оборудования (правка)", set()),
    "glossary": ("Глоссарий переводов", set()),
    "factory_offers": ("Отбор заводов для КП", set()),
    # зеркальные запросы через компанию группы (D-152): цена конечного поставщика, наценка и маржа компании группы
    "intragroup_chain": ("Цепочка компаний группы: цена конечного поставщика, наценка и маржа группы", {"group_chain"}),
    "cost_files": ("Папка «04 Расчёт себестоимости», полный Excel", set()),
    "execution": ("Исполнение контракта (даты этапов)", set()),
    "execution_payments": ("Фактические платежи и аккредитив", set()),
    "execution_close": ("Закрытие сделки после исполнения", set()),
    "settings": ("Ставки и нормативы", set()),
    "access_admin": ("Уровни доступа", set()),
    # эксплуатация (docs/operations/09 §2): статус и история резервных копий без секретов; не даёт восстановления и настройки
    "backup_status": ("Резервные копии: статус и история", set()),
    "security_admin": ("Безопасность сотрудников: сессии, сброс TOTP, отключение доступа, пересмотр прав", set()),
    "security_audit": ("Журнал событий безопасности (без содержимого обращений и диалогов)", set()),
    "support_admin": ("Поддержка: очередь обращений сотрудников, ответы и назначение (READ — видеть, EDIT — отвечать)", set()),
    "assistant_use": ("Помощник CRM: личные вопросы по своим данным и справке (не даёт данных сверх прав)", set()),
    "assistant_admin": ("Помощник CRM: настройки, лимиты и счётчики без содержимого диалогов", set()),
    "legal_docs": ("Документы клиента и юридические (тендерная документация, договор, протокол разногласий)", set()),
    "legal_profiles": ("Юридические профили контрагентов и БЕРГ-ХАУС, банковские реквизиты", set()),
    # Канцелярия (D-128): READ — видеть журналы, EDIT — регистрировать, назначать исполнителей, подписывать от имени подписанта
    "office": ("Канцелярия: журналы писем, приказов, распоряжений и внутренних документов", set()),
    "office_hr": ("Канцелярия: приказы по личному составу", set()),
    "office_signers": ("Канцелярия: подписанты, факсимиле, коды юр. лиц", set()),
    # D-150: приёмка поставок запчастей на склад и фото отклонений; решения по отклонениям — по-прежнему менеджеры, закупки, КД, ЗГД
    "parts_receiving": ("Склад запчастей: приёмка поставок и фото отклонений", set()),
    # K и I (D-180, D-181). Стартовые уровни согласованы владельцем 26.11.2026. Разделы подключаются к группам по мере выпуска:
    # площадки — K-2, парк и владение — K-3, контакты — K-1, сервис — I-1. Инженер видит машины, площадки и контакты только по своим нарядам.
    "client_contacts": ("Клиенты: контактные лица (персональные данные; просмотры записываются в журнал)", set()),
    "client_sites": ("Клиенты: площадки (карьеры, базы, адреса и координаты)", set()),
    "client_fleet": ("Клиенты: парк техники и владение машинами", set()),
    "service_orders": ("Сервис: обращения и наряды (EDIT — назначать инженеров и вести наряды)", set()),
    "service_refs": ("Сервис: справочники работ, нормативов, допусков и сроков реакции", set()),
    # L1 (D-205): блок «Персонал». Три класса данных — три группы. Область по юрлицам — hr_access_scopes
    # (ЗГД — всегда все; ФД и бухгалтер по зарплате — все, пока ЗГД не ограничит их списком юрлиц).
    "hr_core": ("Персонал: сотрудники, оргструктура, должности, штатное расписание (EDIT — кадровые события)", set()),
    "hr_personal": ("Персонал: личные данные — паспорт, адреса, семья, образование, сканы личных документов (зашифровано)", set()),
    "hr_payroll": ("Персонал: оплата труда — оклады, надбавки, банковские реквизиты, трудовые договоры (зашифровано)", set()),
}

# Стартовая матрица: роль → {группа: уровень}. Не перечисленные группы = 0.
DEFAULT_MATRIX: dict[Role, dict[str, int]] = {
    Role.MANAGER: {"price": READ, "payment_terms": READ, "local_options": EDIT, "tech_params": EDIT,
                   "images": EDIT, "glossary": EDIT, "execution": EDIT, "legal_docs": EDIT, "legal_profiles": EDIT},
    Role.LOGISTICIAN: {"logistics_input": EDIT, "intragroup_chain": EDIT, "local_options": READ, "tech_params": READ, "images": READ, "execution": EDIT, "legal_profiles": READ},
    Role.SERVICE_HEAD: {"service_input": EDIT, "local_options": EDIT, "tech_params": READ, "images": READ, "execution": EDIT, "legal_profiles": READ},
    Role.CD: {"cost": READ, "price": EDIT, "logistics_input": READ, "service_input": READ, "factory_price_history": EDIT,
              "client_price_history": READ, "finance_analytics": READ, "payment_terms": EDIT, "local_options": EDIT, "tech_params": EDIT, "images": EDIT,
              "kp_images_select": EDIT, "catalog": EDIT, "glossary": EDIT, "factory_offers": EDIT, "cost_files": READ, "intragroup_chain": EDIT,
              "execution": EDIT, "execution_close": EDIT, "settings": READ, "legal_docs": EDIT, "legal_profiles": EDIT},
    Role.FD: {"cost": READ, "price": READ, "logistics_input": READ, "service_input": READ, "factory_price_history": READ,
              "client_price_history": READ, "finance_analytics": READ, "finance_section": READ, "payment_terms": READ, "local_options": READ, "tech_params": READ, "images": READ, "cost_files": READ, "intragroup_chain": EDIT,
              "execution": READ, "execution_payments": EDIT, "settings": EDIT, "legal_docs": READ, "legal_profiles": EDIT},
    Role.GD: {"cost": READ, "price": EDIT, "logistics_input": EDIT, "service_input": EDIT, "factory_price_history": EDIT,
              "client_price_history": READ, "finance_analytics": READ, "finance_section": READ, "payment_terms": EDIT, "local_options": EDIT, "tech_params": EDIT, "images": EDIT,
              "kp_images_select": EDIT, "catalog": EDIT, "glossary": EDIT, "factory_offers": EDIT, "cost_files": READ, "intragroup_chain": EDIT,
              "execution": EDIT, "execution_payments": EDIT, "execution_close": EDIT, "settings": EDIT, "access_admin": EDIT, "legal_docs": EDIT, "legal_profiles": EDIT, "backup_status": READ,
              "security_admin": EDIT, "security_audit": READ, "support_admin": EDIT, "office": EDIT, "office_hr": EDIT, "office_signers": EDIT},
    Role.ADMIN: {"tech_params": READ, "images": READ, "catalog": READ, "glossary": EDIT, "settings": EDIT, "access_admin": EDIT, "legal_profiles": EDIT,
                 "backup_status": READ, "security_admin": EDIT, "security_audit": READ},
    Role.OBSERVER: {"price": READ, "payment_terms": READ, "tech_params": READ, "images": READ, "execution": READ},
    Role.PURCHASING: {"local_options": EDIT, "tech_params": READ, "images": READ, "execution": READ, "legal_profiles": READ},
    Role.LAWYER: {"price": READ, "payment_terms": READ, "tech_params": READ, "images": READ, "execution": READ, "legal_docs": EDIT, "legal_profiles": EDIT,
                  "office": READ},
    Role.CLERK: {"office": EDIT, "office_hr": EDIT, "legal_profiles": READ, "assistant_use": READ},
}

# Группы, которые нельзя закрыть для ЗГД и администратора (защита от потери доступа)
# Помощник CRM (09 §2): пользоваться — всем активным ролям (исключением можно закрыть), настройки — ЗГД
for _r in Role:
    DEFAULT_MATRIX.setdefault(_r, {}).setdefault("assistant_use", READ)
    DEFAULT_MATRIX[_r].setdefault("file_download", READ)   # по умолчанию скачивание открыто всем; закрывается в «Уровнях доступа»
DEFAULT_MATRIX[Role.GD]["assistant_admin"] = EDIT
# D-205: «Персонал» — решение владельца 28.11.2026: сразу ЗГД, ФД и бухгалтер по заработной плате, все три класса данных
for _r in (Role.GD, Role.FD, Role.PAYROLL):
    for _g in ("hr_core", "hr_personal", "hr_payroll"):
        DEFAULT_MATRIX.setdefault(_r, {})[_g] = EDIT
DEFAULT_MATRIX[Role.PAYROLL]["office_hr"] = READ   # D-209: Канцелярия — кадровые приказы (приказы с оплатой труда ведёт по праву hr_payroll)
DEFAULT_MATRIX[Role.PAYROLL]["file_download"] = NONE   # файлы сделок бухгалтеру по зарплате не нужны (сканы «Персонала» — по своим группам)
HR_ALL_EMPLOYERS = {Role.GD, Role.FD, Role.PAYROLL}   # без записей в области — видят всех работодателей
# Приёмка на склад (D-150, решение владельца 22.11.2026): менеджер и логист; закупки, КД и ЗГД — как раньше
for _r in (Role.MANAGER, Role.LOGISTICIAN, Role.PURCHASING, Role.CD, Role.GD):
    DEFAULT_MATRIX[_r]["parts_receiving"] = EDIT
# Канцелярия (D-128): КД и ФД видят журналы
for _r in (Role.CD, Role.FD):
    DEFAULT_MATRIX[_r].setdefault("office", READ)

# K и I (D-181): стартовая матрица по таблице, согласованной владельцем 26.11.2026
_KI = {  # роль: (контакты, площадки, парк, сервис, справочники сервиса)
    Role.MANAGER: (EDIT, EDIT, EDIT, READ, READ),
    Role.LOGISTICIAN: (READ, READ, READ, NONE, NONE),
    Role.SERVICE_HEAD: (EDIT, EDIT, EDIT, EDIT, EDIT),
    Role.CD: (EDIT, EDIT, EDIT, READ, READ),
    Role.FD: (READ, READ, READ, READ, NONE),
    Role.GD: (EDIT, EDIT, EDIT, EDIT, EDIT),
    Role.ADMIN: (NONE, READ, EDIT, NONE, NONE),
    Role.OBSERVER: (NONE, READ, NONE, NONE, NONE),
    Role.PURCHASING: (READ, READ, READ, NONE, NONE),
    Role.LAWYER: (READ, READ, NONE, NONE, NONE),
    Role.CLERK: (READ, NONE, NONE, NONE, NONE),
    Role.ENGINEER: (READ, READ, READ, READ, READ),   # только по своим нарядам; закрывает свои наряды (решение 5)
}
for _r, _lv in _KI.items():
    for _g, _l in zip(("client_contacts", "client_sites", "client_fleet", "service_orders", "service_refs"), _lv):
        if _l:
            DEFAULT_MATRIX[_r][_g] = _l
DEFAULT_MATRIX[Role.ENGINEER]["file_download"] = NONE   # файлы сделок инженеру не нужны: сделок он не видит

PROTECTED = {Role.GD: {"access_admin", "security_admin"}, Role.ADMIN: {"access_admin"}}


def effective_perms(roles: Iterable[Role], rules: Optional[Mapping[str, Mapping[str, int]]] = None,
                    overrides: Optional[Mapping[str, int]] = None) -> dict[str, int]:
    """Итоговые уровни по группам: максимум по ролям (ЗГД наследует КД), затем исключения пользователя."""
    roles = set(roles)
    if Role.GD in roles:
        roles.add(Role.CD)
    matrix: dict[str, dict[str, int]] = {r.value: dict(m) for r, m in DEFAULT_MATRIX.items()}
    for role, groups in (rules or {}).items():
        matrix.setdefault(role, {}).update(groups)
    perms = {g: NONE for g in FIELD_GROUPS}
    for r in roles:
        for g, lvl in matrix.get(r.value, {}).items():
            if g in perms:
                perms[g] = max(perms[g], int(lvl))
    for g, lvl in (overrides or {}).items():
        if g in perms:
            perms[g] = int(lvl)
    for r in roles:
        for g in PROTECTED.get(r, set()):
            perms[g] = EDIT
    return perms


def denied_fields_for(perms: Mapping[str, int]) -> set[str]:
    denied: set[str] = set()
    allowed: set[str] = set()
    for g, (_, keys) in FIELD_GROUPS.items():
        (allowed if perms.get(g, NONE) > NONE else denied).update(keys)
    return denied - allowed


def denied_fields(roles: Iterable[Role]) -> set[str]:
    """Множество ключей, которые нельзя отдавать пользователю с данным набором ролей (стартовая матрица)."""
    return denied_fields_for(effective_perms(roles))


def _perms_of(who: Any) -> Mapping[str, int]:
    if isinstance(who, Mapping):
        return who
    perms = getattr(who, "perms", None)
    if perms:
        return perms
    roles = getattr(who, "roles", None)
    if roles is not None and not isinstance(who, (list, set, tuple)):
        return effective_perms(roles)
    return effective_perms(who)


def filter_payload(data: Any, who: Any) -> Any:
    """Рекурсивно удаляет закрытые ключи из словарей/списков. `who` — набор ролей, словарь прав или CurrentUser."""
    denied = denied_fields_for(_perms_of(who))
    if not denied:
        return data
    return _strip(data, denied)


def _strip(obj: Any, denied: set[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v, denied) for k, v in obj.items() if k not in denied}
    if isinstance(obj, list):
        return [_strip(x, denied) for x in obj]
    return obj


def can(who: Any, group: str, level: int = READ) -> bool:
    return _perms_of(who).get(group, NONE) >= level


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
