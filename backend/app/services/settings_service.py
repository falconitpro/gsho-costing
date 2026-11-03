"""«Ставки и нормативы» с историей: действующее значение ключа = запись с valid_to IS NULL."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RateSetting

DEFAULTS: dict[str, Any] = {
    "margin_threshold_pct": 3.0,
    "scenario_dk": {"pessimistic": 5.0, "optimistic": -3.0},
    "scenario_dt": {"pessimistic": 30, "optimistic": -10},
    "use_historical_volatility": True,
    "vol_multipliers": {"pessimistic": 1.0, "optimistic": 0.6},
    "fx_weights": {"forward": 0.4, "trend": 0.3, "expert": 0.3},
    "fx_history_years": 3,
    "key_rate_pct": 16.0,
    "pboc_rate_pct": 3.1,
    "foreign_rates_pct": {"CNY": 3.1, "USD": 4.5, "EUR": 2.5},
    "fx_expert_points": {},
    "financing_rate_pct": 18.0,
    "deposit_rate_pct": None,
    "conversion_fee_pct": 0.5,
    "transfer_fee": {"mode": "fixed", "fixed_rub": 3000.0, "pct": 0.1},
    "external_commission_pct": 2.0,    # внешняя комиссия, % от цены клиента
    "manager_commission_pct": 0.0,     # комиссия менеджера, % от себестоимости
    "cd_commission_pct": 0.0,          # комиссия КД, % от себестоимости
    "util_base_rate_rub": 172_500.0,   # утилизационный сбор: базовая ставка (самоходная техника), ₽
    "util_coefficients": [],           # коэффициенты по виду техники и коду ТН ВЭД: [{hs_prefix, machine_type, coefficient, comment}]
    "guarantee_rate_pct": 3.0,
    "vat_offset_days": 90,
    "import_vat_in_cost": False,
    "overhead_scale": [{"up_to_rub": 50_000_000, "pct": 5.0}, {"up_to_rub": 200_000_000, "pct": 4.0}, {"up_to_rub": None, "pct": 3.0}],
    "overhead_actual_pct_last_year": None,
    "warranty_reserve_pct_by_type": {"default": 1.5},
    "unplanned_reserve_pct": 3.0,
    "labour_rates_rub_per_hour": {"engineer_1": 3500, "engineer_2": 2800, "mechanic": 2000},
    "region_norms": {"default": {"travel_rub": 30000, "lodging_rub_per_day": 4000, "per_diem_rub": 1500, "travel_hours": 16}},
    "hours_per_month_by_type": {"default": 400},
    "cash_gap_limit_rub": None,
    "gd_deal_limit_rub": None,
    "price_deviation_pct": 10.0,
    "logistics_deviation_pct": 15.0,
    "plan_fact_deviation_pct": 10.0,
    "sla_days": {"logistics": 1, "service": 2, "cd": 1, "fd": 1, "gd": 1, "manager_rework": 1},
    "kp_fx_clause_pct": 5.0,
    "kp_validity_days": 30,
    "commissioning_days": 5,
    # задача 1: типовые схемы оплаты (пока КД не задал условия) и БГ на аванс
    "default_client_payment_scheme": [
        {"no": 1, "share_pct": 30, "event": "contract", "offset_days": 0},
        {"no": 2, "share_pct": 70, "event": "border", "offset_days": 5, "offset_kind": "working", "via": "lc"},
    ],
    "default_factory_payment_scheme": [
        {"no": 1, "share_pct": 30, "event": "factory_contract", "offset_days": 14, "offset_kind": "working"},
        {"no": 2, "share_pct": 70, "event": "readiness", "offset_days": 14, "offset_kind": "working"},
    ],
    "advance_guarantee_cost": {"enabled": True, "mode": "annual_pct", "value": 3.0},
    "lc_opening_days": 14,
    "factory_contract_lag_days": 10,
    "rf_holidays": [],                 # дополнительные нерабочие дни/переносы, ISO-даты
    # задача 3: сроки
    "delivery_reserve_pct": 10.0,
    "rf_delivery_days_default": 5,
    # задача 6: опции РФ
    "local_options_order_reserve_days": 14,
    # задача 4: переводчик
    "translator_provider": "yandex",   # yandex | console
    # задача 9: бренд подачи КП по умолчанию
    "kp_brand_default": "factory",
    "approval_ladder": [
        {"name": "Коммерческая", "role": "cd", "condition": "always", "sla_days": 1},
        {"name": "Финансовая", "role": "fd", "condition": "margin_below_threshold|cash_gap_above_limit", "sla_days": 1},
        {"name": "Генеральная", "role": "gd", "condition": "deal_above_gd_limit|fd_declined|overhead_override", "sla_days": 1},
    ],
}


def expert_points(value: Any) -> dict[str, list[dict]]:
    """Экспертные точки курса в едином виде {валюта: [{on, rate}]}; принимает и старую запись {валюта: {дата: курс}}."""
    out: dict[str, list[dict]] = {}
    for cur, pts in (value or {}).items():
        if isinstance(pts, dict):
            pts = [{"on": d, "rate": r} for d, r in pts.items()]
        rows = []
        for p in pts or []:
            try:
                rows.append({"on": str(p["on"])[:10], "rate": float(p["rate"])})
            except (KeyError, TypeError, ValueError):
                continue
        if rows:
            out[str(cur).upper()[:3]] = sorted(rows, key=lambda x: x["on"])
    return out


def util_reference(s: dict[str, Any], hs_code: str = "", machine_type: str = "") -> dict[str, Any]:
    """Подсказка по утилизационному сбору: базовая ставка и коэффициент по коду ТН ВЭД (длиннейшее совпадение начала) и типу машины."""
    hs = (hs_code or "").replace(" ", "")
    mt = (machine_type or "").strip().lower()
    best, best_score = None, (-1, -1)
    for row in s.get("util_coefficients") or []:
        pref = str(row.get("hs_prefix") or "").replace(" ", "")
        rmt = str(row.get("machine_type") or "").strip().lower()
        if pref and not hs.startswith(pref):
            continue
        if rmt and rmt not in mt:
            continue
        score = (len(pref), len(rmt))
        if score > best_score:
            best, best_score = row, score
    return {"base_rate_rub": float(s.get("util_base_rate_rub") or 0), "coefficient": float(best["coefficient"]) if best and best.get("coefficient") not in (None, "") else None,
            "rule": best}


def get_all(db: Session) -> dict[str, Any]:
    rows = db.scalars(select(RateSetting).where(RateSetting.valid_to.is_(None))).all()
    out = dict(DEFAULTS)
    for r in rows:
        out[r.key] = r.value
    return out


def get(db: Session, key: str) -> Any:
    return get_all(db).get(key)


def set_value(db: Session, key: str, value: Any, user_id: str | None, comment: str = "") -> RateSetting:
    now = datetime.now(timezone.utc)
    for r in db.scalars(select(RateSetting).where(RateSetting.key == key, RateSetting.valid_to.is_(None))).all():
        r.valid_to = now
    row = RateSetting(key=key, value=value, changed_by=user_id, comment=comment, valid_from=now)
    db.add(row)
    return row


def history(db: Session, key: str) -> list[RateSetting]:
    return db.scalars(select(RateSetting).where(RateSetting.key == key).order_by(RateSetting.valid_from.desc())).all()


# ------------------------------------------------------------------ описание настроек для экрана «Настройки»
# type: number | bool | select | object (поля fields) | dict (свободные ключи → число) | table (строки с колонками columns) | dates | json
_PM = [{"key": "pessimistic", "label": "Пессимистичный"}, {"key": "optimistic", "label": "Оптимистичный"}]
_EVENTS = [
    {"value": "contract", "label": "подписание контракта"}, {"value": "client_prepayment", "label": "аванс клиента"},
    {"value": "lc_opened", "label": "открытие аккредитива"}, {"value": "factory_contract", "label": "контракт с заводом"},
    {"value": "factory_prepayment", "label": "аванс заводу"}, {"value": "readiness", "label": "готовность на заводе"},
    {"value": "shipment", "label": "отгрузка"}, {"value": "border", "label": "пересечение границы (CMR)"},
    {"value": "declaration", "label": "выпуск ДТ"}, {"value": "delivery", "label": "поставка клиенту"},
    {"value": "commissioning", "label": "ПНР"}, {"value": "runtime", "label": "наработка"},
]
_SCHEME_COLS = [
    {"key": "share_pct", "label": "Доля, %", "type": "number"},
    {"key": "event", "label": "Событие", "type": "select", "options": _EVENTS},
    {"key": "offset_days", "label": "Через, дней", "type": "number"},
    {"key": "offset_kind", "label": "Дни", "type": "select", "options": [{"value": "calendar", "label": "календарные"}, {"value": "working", "label": "рабочие"}]},
    {"key": "via", "label": "Способ", "type": "select", "options": [{"value": "transfer", "label": "перевод"}, {"value": "lc", "label": "аккредитив"}]},
]

META: dict[str, dict] = {
    # Маржа и визы
    "margin_threshold_pct": {"group": "Маржа и визы", "title": "Порог маржи", "unit": "%", "type": "number",
                             "help": "Если маржа сделки ниже порога, нужна виза финансового директора."},
    "cash_gap_limit_rub": {"group": "Маржа и визы", "title": "Лимит кассового разрыва", "unit": "₽", "type": "number", "nullable": True,
                           "help": "Кассовый разрыв больше лимита требует визы ФД. Пусто — лимита нет."},
    "gd_deal_limit_rub": {"group": "Маржа и визы", "title": "Сумма сделки для визы ГД", "unit": "₽", "type": "number", "nullable": True,
                          "help": "Сделки дороже этой суммы визирует генеральный директор. Пусто — не требуется."},
    "approval_ladder": {"group": "Маржа и визы", "title": "Ступени визации", "type": "table",
                        "help": "Порядок виз и условия, при которых ступень включается.",
                        "columns": [{"key": "name", "label": "Ступень", "type": "text"},
                                    {"key": "role", "label": "Кто визирует", "type": "select", "options": [{"value": "cd", "label": "КД"}, {"value": "fd", "label": "ФД"}, {"value": "gd", "label": "ГД"}]},
                                    {"key": "condition", "label": "Условие (служебное)", "type": "text"},
                                    {"key": "sla_days", "label": "Срок, раб. дн.", "type": "number"}]},
    # Сценарии и курс
    "scenario_dk": {"group": "Сценарии и прогноз курса", "title": "Отклонение курса в сценариях", "unit": "%", "type": "object", "fields": _PM,
                    "help": "На сколько процентов курс в сценарии отличается от базового (пессимистичный — дороже)."},
    "scenario_dt": {"group": "Сценарии и прогноз курса", "title": "Сдвиг сроков в сценариях", "unit": "дн.", "type": "object", "fields": _PM,
                    "help": "Задержка (+) или ускорение (−) производства и поставки относительно базового сценария."},
    "use_historical_volatility": {"group": "Сценарии и прогноз курса", "title": "Учитывать историческую волатильность курса", "type": "bool",
                                  "help": "Расширять диапазон сценариев по фактическим колебаниям курса за прошлые годы."},
    "vol_multipliers": {"group": "Сценарии и прогноз курса", "title": "Множители волатильности", "type": "object", "fields": _PM,
                        "help": "Какую часть исторической волатильности добавлять к сценарию."},
    "fx_weights": {"group": "Сценарии и прогноз курса", "title": "Веса методов прогноза курса", "type": "object",
                   "fields": [{"key": "forward", "label": "Форвард"}, {"key": "trend", "label": "Тренд и сезонность"}, {"key": "expert", "label": "Экспертная оценка"}],
                   "help": "Доли методов в итоговом прогнозе, в сумме 1 (например 0,4 / 0,3 / 0,3)."},
    "fx_history_years": {"group": "Сценарии и прогноз курса", "title": "Глубина истории курса", "unit": "лет", "type": "number"},
    "fx_expert_points": {"group": "Сценарии и прогноз курса", "title": "Экспертные точки курса", "type": "expert_points",
                         "help": "Ожидаемый курс валюты к рублю на даты (например, по прогнозу банков). Участвует в прогнозе с весом «Экспертная оценка»."},
    "kp_fx_clause_pct": {"group": "Сценарии и прогноз курса", "title": "Оговорка о курсе в КП", "unit": "%", "type": "number",
                         "help": "При изменении курса больше чем на эту величину цена КП пересматривается (текст в КП)."},
    # Стоимость денег и банк
    "key_rate_pct": {"group": "Стоимость денег и банк", "title": "Ключевая ставка ЦБ РФ", "unit": "%", "type": "number"},
    "pboc_rate_pct": {"group": "Стоимость денег и банк", "title": "Ставка Народного банка Китая", "unit": "%", "type": "number"},
    "foreign_rates_pct": {"group": "Стоимость денег и банк", "title": "Ставки по валютам (для форварда)", "unit": "%", "type": "dict", "key_label": "Валюта"},
    "financing_rate_pct": {"group": "Стоимость денег и банк", "title": "Ставка финансирования", "unit": "% годовых", "type": "number",
                           "help": "Стоимость денег для кассового разрыва."},
    "deposit_rate_pct": {"group": "Стоимость денег и банк", "title": "Ставка размещения", "unit": "% годовых", "type": "number", "nullable": True,
                         "help": "Доход на свободные деньги. Пусто — не учитывается."},
    "conversion_fee_pct": {"group": "Стоимость денег и банк", "title": "Комиссия за конвертацию", "unit": "%", "type": "number"},
    "transfer_fee": {"group": "Стоимость денег и банк", "title": "Комиссия за перевод", "type": "object",
                     "fields": [{"key": "mode", "label": "Как считать", "type": "select", "options": [
                                    {"value": "fixed", "label": "фиксированная сумма"}, {"value": "pct", "label": "процент от суммы"},
                                    {"value": "both", "label": "процент + фиксированная сумма"}]},
                                {"key": "fixed_rub", "label": "Фиксированная сумма, ₽"}, {"key": "pct", "label": "Процент от суммы перевода, %"}],
                     "help": "За каждый платёж заводу и каждый валютный платёж."},
    "external_commission_pct": {"group": "Комиссии", "title": "Внешняя комиссия", "unit": "% от цены клиента", "type": "number",
                                "help": "Статья 16 себестоимости; выплачивается вместе с оплатами клиента. Меняют КД или ГД."},
    "manager_commission_pct": {"group": "Комиссии", "title": "Комиссия менеджера", "unit": "% от себестоимости", "type": "number",
                               "help": "Статья 17: от суммы статей 1–15. Меняют КД или ГД."},
    "cd_commission_pct": {"group": "Комиссии", "title": "Комиссия КД", "unit": "% от себестоимости", "type": "number",
                          "help": "Статья 18: от суммы статей 1–15. Меняет ГД."},
    "util_base_rate_rub": {"group": "Таможня и утилизационный сбор", "title": "Утилизационный сбор: базовая ставка", "unit": "₽", "type": "number",
                           "help": "Подставляется логисту; сбор = базовая ставка × коэффициент по виду техники. Проверьте действующую редакцию постановления Правительства РФ."},
    "util_coefficients": {"group": "Таможня и утилизационный сбор", "title": "Коэффициенты утилизационного сбора", "type": "table",
                          "columns": [{"key": "hs_prefix", "label": "Код ТН ВЭД (начало)", "type": "text"}, {"key": "machine_type", "label": "Вид техники (тип машины)", "type": "text"},
                                      {"key": "coefficient", "label": "Коэффициент"}, {"key": "comment", "label": "Основание / комментарий", "type": "text"}],
                          "help": "Подсказка логисту: коэффициент выбирается по коду ТН ВЭД (самое длинное совпадение начала кода) и типу машины. Логист может изменить."},
    "guarantee_rate_pct": {"group": "Стоимость денег и банк", "title": "Ставка банковской гарантии (прочие)", "unit": "% годовых", "type": "number"},
    "advance_guarantee_cost": {"group": "Стоимость денег и банк", "title": "Банковская гарантия на аванс клиента", "type": "object",
                               "help": "Расход в статье 11, если в условиях клиента есть аванс. КД может изменить в сделке.",
                               "fields": [{"key": "enabled", "label": "Учитывать", "type": "bool"},
                                          {"key": "mode", "label": "Способ расчёта", "type": "select",
                                           "options": [{"value": "one_time_pct", "label": "разовый % от аванса"}, {"value": "annual_pct", "label": "% годовых до поставки"}, {"value": "fixed", "label": "фиксированная сумма, ₽"}]},
                                          {"key": "value", "label": "Значение"}]},
    # НДС
    "vat_offset_days": {"group": "НДС", "title": "Срок возмещения ввозного НДС", "unit": "дн.", "type": "number"},
    "import_vat_in_cost": {"group": "НДС", "title": "Ввозной НДС — расход (не возмещается)", "type": "bool",
                           "help": "Выключено: НДС возмещается и влияет только на кассовый разрыв."},
    # Накладные и резервы
    "overhead_scale": {"group": "Накладные и резервы", "title": "Шкала накладных расходов", "type": "table",
                       "help": "Процент накладных от себестоимости в зависимости от суммы сделки. Пустая граница — «и выше».",
                       "columns": [{"key": "up_to_rub", "label": "Сумма сделки до, ₽", "type": "number", "nullable": True}, {"key": "pct", "label": "Накладные, %", "type": "number"}]},
    "overhead_actual_pct_last_year": {"group": "Накладные и резервы", "title": "Фактические накладные прошлого года", "unit": "%", "type": "number", "nullable": True,
                                      "help": "Справочно, для сравнения со шкалой."},
    "warranty_reserve_pct_by_type": {"group": "Сервис", "title": "Расходы на гарантийные случаи", "unit": "% от цены завода", "type": "dict", "key_label": "Тип машины",
                                     "help": "Статья 13 себестоимости; меняется по типу машины и по каждой позиции в блоке сервиса. Ориентир: производители "
                                             "тяжёлой техники закладывают на гарантию в среднем ≈1,4 % выручки; по умолчанию 1,5 % от цены завода. "
                                             "«default» — для типов, которых нет в списке."},
    "unplanned_reserve_pct": {"group": "Накладные и резервы", "title": "Резерв на непредвиденные расходы", "unit": "%", "type": "number"},
    # Сервис
    "labour_rates_rub_per_hour": {"group": "Сервис", "title": "Ставки работ сервиса", "unit": "₽/ч", "type": "object",
                                  "fields": [{"key": "engineer_1", "label": "Инженер 1 категории"}, {"key": "engineer_2", "label": "Инженер 2 категории"}, {"key": "mechanic", "label": "Механик"}]},
    "region_norms": {"group": "Сервис", "title": "Нормативы выездов по регионам", "type": "dict_table", "key_label": "Регион",
                     "columns": [{"key": "travel_rub", "label": "Проезд, ₽"}, {"key": "lodging_rub_per_day", "label": "Проживание, ₽/сут"},
                                 {"key": "per_diem_rub", "label": "Суточные, ₽"}, {"key": "travel_hours", "label": "В пути, ч"}],
                     "help": "Для каждого региона: проезд, проживание в сутки, суточные (₽), часы в пути. «default» — для остальных."},
    "hours_per_month_by_type": {"group": "Сервис", "title": "Наработка в месяц по типу машины", "unit": "м·ч", "type": "dict", "key_label": "Тип машины"},
    "commissioning_days": {"group": "Сервис", "title": "Длительность ПНР", "unit": "дн.", "type": "number"},
    # Контроль отклонений
    "price_deviation_pct": {"group": "Контроль отклонений", "title": "Отклонение цены завода от истории", "unit": "%", "type": "number",
                            "help": "Больше — менеджер обязан написать комментарий."},
    "logistics_deviation_pct": {"group": "Контроль отклонений", "title": "Отклонение логистики от истории", "unit": "%", "type": "number",
                                "help": "Больше — логист обязан написать комментарий."},
    "plan_fact_deviation_pct": {"group": "Контроль отклонений", "title": "Допустимое отклонение план/факт", "unit": "%", "type": "number"},
    # Сроки
    "sla_days": {"group": "Сроки и календарь", "title": "Сроки выполнения задач (SLA)", "unit": "раб. дн.", "type": "object",
                 "fields": [{"key": "logistics", "label": "Логист"}, {"key": "service", "label": "Сервис"}, {"key": "cd", "label": "КД"},
                            {"key": "fd", "label": "ФД"}, {"key": "gd", "label": "ГД"}, {"key": "manager_rework", "label": "Доработка менеджером"}]},
    "kp_validity_days": {"group": "Сроки и календарь", "title": "Срок действия КП", "unit": "дн.", "type": "number"},
    "lc_opening_days": {"group": "Сроки и календарь", "title": "Открытие аккредитива после контракта (план)", "unit": "дн.", "type": "number"},
    "factory_contract_lag_days": {"group": "Сроки и календарь", "title": "Контракт с заводом после контракта с клиентом", "unit": "дн.", "type": "number"},
    "delivery_reserve_pct": {"group": "Сроки и календарь", "title": "Резерв к сроку поставки в КП", "unit": "%", "type": "number",
                             "help": "Срок для клиента = расчётный × (1 + резерв), с округлением до целой недели."},
    "rf_delivery_days_default": {"group": "Сроки и календарь", "title": "Доставка по РФ после выпуска ДТ (по умолчанию)", "unit": "дн.", "type": "number"},
    "local_options_order_reserve_days": {"group": "Сроки и календарь", "title": "Запас при заказе опций РФ", "unit": "дн.", "type": "number"},
    "rf_holidays": {"group": "Сроки и календарь", "title": "Дополнительные нерабочие дни", "type": "dates",
                    "help": "Переносы и праздники сверх производственного календаря, по одной дате в строке (ГГГГ-ММ-ДД)."},
    # Условия оплаты
    "default_client_payment_scheme": {"group": "Условия оплаты по умолчанию", "title": "Типовая схема оплаты клиента", "type": "table", "columns": _SCHEME_COLS,
                                      "help": "Применяется в расчёте, пока КД не задал условия в сделке."},
    "default_factory_payment_scheme": {"group": "Условия оплаты по умолчанию", "title": "Типовая схема оплаты заводу", "type": "table", "columns": _SCHEME_COLS},
    # Интеграции и КП
    "translator_provider": {"group": "Интеграции и КП", "title": "Переводчик", "type": "select",
                            "options": [{"value": "yandex", "label": "Яндекс Переводчик"}, {"value": "console", "label": "не подключён (только глоссарий)"}]},
    "kp_brand_default": {"group": "Интеграции и КП", "title": "Подача КП по умолчанию", "type": "select",
                         "options": [{"value": "factory", "label": "под маркой завода"}, {"value": "company", "label": "под брендом компании"}]},
}
GROUP_ORDER = ["Маржа и визы", "Комиссии", "Условия оплаты по умолчанию", "Стоимость денег и банк", "Сценарии и прогноз курса", "НДС",
               "Накладные и резервы", "Сервис", "Таможня и утилизационный сбор", "Контроль отклонений", "Сроки и календарь", "Интеграции и КП"]


def meta() -> dict:
    """Описание всех ключей для экрана настроек; ключи без описания — в группу «Прочее» (тип json)."""
    items = {k: {"key": k, **META.get(k, {"group": "Прочее", "title": k, "type": "json"})} for k in DEFAULTS}
    return {"groups": GROUP_ORDER + ["Прочее"], "items": items}
