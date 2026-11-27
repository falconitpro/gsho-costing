"""Схема БД (PostgreSQL, SQLAlchemy 2.0). Описание — docs/ARCHITECTURE.md, раздел «Схема БД».

Принципы:
- Исходные данные расчёта и результат хранятся в версии расчёта (deal_versions) как JSONB —
  версия неизменяема, курс ЦБ зафиксирован в ней.
- Нормализованные таблицы — для справочников, истории цен/логистики и списков (сделки, позиции, задачи).
- audit_log — только вставка (на уровне БД: REVOKE UPDATE/DELETE, см. migrations/001_init.sql).
- Коды статей себестоимости (cost_item_code) совпадают с ключами ядра и сопоставляются со статьями 1С.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.workflow import DealStatus
from ..db import Base

# D-113: денежные суммы и курсы — десятичные числа в базе (без «хвостов» двоичной дроби); в Python — float, как раньше
from sqlalchemy import Numeric as _Numeric  # noqa: E402
Money = _Numeric(20, 6, asdecimal=False)

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ------------------------------------------------------------------ пользователи

class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    external_id: Mapped[str] = mapped_column(String(255), unique=True)       # sub из Entra ID / Яндекс ID
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list[str]] = mapped_column(JSONType, default=list)        # несколько ролей на пользователя
    observer_scope: Mapped[dict] = mapped_column(JSONType, default=dict)    # объём доступа наблюдателя: client_ids, manager_ids, deal_ids, phase, until
    deputy_id: Mapped[Optional[str]] = mapped_column(String(36))             # заместитель на период отсутствия
    deputy_from: Mapped[Optional[date]] = mapped_column(Date)
    deputy_to: Mapped[Optional[date]] = mapped_column(Date)
    visa_limit_rub: Mapped[Optional[float]] = mapped_column(Float)
    notify_channels: Mapped[list[str]] = mapped_column(JSONType, default=lambda: ["outlook", "wecom", "app"])
    wecom_user_id: Mapped[Optional[str]] = mapped_column(String(100))
    # карточка контактов исполнителя: {position, department, mobile, work_phone, wechat, telegram, whatsapp, max, extra_email, note}
    contacts: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # изменения карточки, ожидающие подтверждения администратора / ЗГД: {fields: {name: [old, new]}, requested_at, requested_by}
    pending_changes: Mapped[dict] = mapped_column(JSONType, default=dict)
    prefs: Mapped[dict] = mapped_column(JSONType, default=dict)                # личные настройки интерфейса: {workplace: {order, hidden}}
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))          # вход по паролю (страница входа); пусто — только Microsoft 365
    password_set_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    password_must_change: Mapped[bool] = mapped_column(Boolean, default=False)       # временный пароль администратора: до смены — только смена пароля (04 §4)
    offboarded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # доступ отключён единой операцией (04 §5)
    offboarded_by: Mapped[Optional[str]] = mapped_column(String(36))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)             # защита от перебора: 5 ошибок → блок на 15 минут
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ уровни доступа (задача 11)

class AccessRule(Base):
    """Уровень доступа роли к группе полей: 0 — не видит, 1 — видит, 2 — редактирует. Пусто = стартовая матрица."""
    __tablename__ = "access_rules"
    __table_args__ = (UniqueConstraint("role", "field_group", name="uq_access_rule"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    role: Mapped[str] = mapped_column(String(32), index=True)
    field_group: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=0)
    changed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessUserOverride(Base):
    """Исключение для сотрудника: открыть или закрыть группу относительно его ролей (приоритет над ролью)."""
    __tablename__ = "access_user_overrides"
    __table_args__ = (UniqueConstraint("user_id", "field_group", name="uq_access_override"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    field_group: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=0)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ------------------------------------------------------------------ справочники

class Client(TimestampMixin, Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12), unique=True)                # дубли по ИНН запрещены
    address: Mapped[str] = mapped_column(Text, default="")
    sites: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # объекты / месторождения
    contacts: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    # реквизиты из реестра (ЕГРЮЛ/ЕГРИП через внешний источник, по ИНН)
    full_name: Mapped[str] = mapped_column(Text, default="")
    kpp: Mapped[str] = mapped_column(String(9), default="")
    ogrn: Mapped[str] = mapped_column(String(15), default="")
    director: Mapped[str] = mapped_column(String(255), default="")
    legal_status: Mapped[str] = mapped_column(String(32), default="")          # ACTIVE | LIQUIDATING | LIQUIDATED …
    okved: Mapped[str] = mapped_column(String(255), default="")
    registry_data: Mapped[dict] = mapped_column(JSONType, default=dict)        # ответ реестра (сокращённо); «registry» занято SQLAlchemy (D-148)
    registry_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    manager_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    default_payment_scheme_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_schemes.id"))
    # наборы реквизитов для договоров (юрлицо/филиал, банк, адреса, подписант): [{id, title, is_default, legal_name, inn, kpp, ogrn,
    #   legal_address, postal_address, bank_name, bik, account, corr_account, signatory_name, signatory_position, signatory_basis, phone, email, comment}]
    requisites: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)              # архив: клиент используется в сделках — не удаляется, а скрывается
    # адреса доставки (D-073): [{id, address, status: approved|pending, proposed_by, proposed_at, deal_number, approved_by, approved_at}]
    delivery_addresses: Mapped[list[dict]] = mapped_column(JSONType, default=list)


def designation(trademark: Optional[str], model: Optional[str]) -> str:
    """«Торговая марка + модель»; марка не дублируется, если уже входит в название модели."""
    tm, m = (trademark or "").strip(), (model or "").strip()
    if not tm or m.lower().startswith(tm.lower()):
        return m or tm
    return f"{tm} {m}".strip()


def default_trademark(factory: Optional["Factory"]) -> str:
    """Торговая марка по сопоставлению «завод → марки» (первая в списке)."""
    marks = [t for t in ((factory.trademarks if factory else None) or []) if t]
    return marks[0] if marks else ""


class Factory(TimestampMixin, Base):
    __tablename__ = "factories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)              # 0.7
    name_zh: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(Text, default="")                   # 0.8
    country: Mapped[str] = mapped_column(String(64), default="CN")
    # контакты по направлениям: [{name, name_zh, position, roles: [rfq|sales|logistics|finance|service|quality|management|other],
    #   phone, mobile, email, wechat, wecom_id, whatsapp, language, primary, comment}]
    contacts: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    wecom_external_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    short_name: Mapped[str] = mapped_column(String(128), default="")
    address_zh: Mapped[str] = mapped_column(Text, default="")
    province: Mapped[str] = mapped_column(String(128), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    website: Mapped[str] = mapped_column(String(255), default="")
    uscc: Mapped[str] = mapped_column(String(32), default="")                # 统一社会信用代码 — единый код юрлица КНР
    supplier_status: Mapped[str] = mapped_column(String(32), default="")     # manufacturer | official_dealer | trader
    bank_details: Mapped[dict] = mapped_column(JSONType, default=dict)       # {beneficiary, bank, swift, account, bank_address}
    default_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    default_basis: Mapped[str] = mapped_column(String(16), default="")
    machine_types: Mapped[list[str]] = mapped_column(JSONType, default=list)  # что выпускает
    trademarks: Mapped[list[str]] = mapped_column(JSONType, default=list)     # торговые марки завода (первая — по умолчанию), напр. SHILI
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    payment_terms: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    typical_production_days: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[float]] = mapped_column(Float)
    # запчасти (шаг 1): тип поставщика — supplier_status, плюс group_company — компания группы как поставщик
    legal_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))   # для supplier_status = group_company
    language: Mapped[str] = mapped_column(String(8), default="")         # язык переписки: ru | zh | en | de
    default_route_id: Mapped[Optional[str]] = mapped_column(ForeignKey("supply_routes.id"))   # маршрут поставки по умолчанию
    supplies: Mapped[list[str]] = mapped_column(JSONType, default=list)   # что поставляет: gsho (горно-шахтное оборудование) | parts (запасные части)
    approval_status: Mapped[Optional[str]] = mapped_column(String(16))             # pending | approved | rejected (NULL — заведён до утверждений = утверждён)
    approved_by: Mapped[Optional[str]] = mapped_column(String(36))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


class MachineType(TimestampMixin, Base):
    """Справочник типов машин (задача 7): каталог, шаблоны технических характеристик, нормативы по типу."""
    __tablename__ = "machine_types"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    name_zh: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="approved")   # approved | pending (предложен менеджером)
    proposed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class EquipmentModel(TimestampMixin, Base):
    """Каталог оборудования: производитель → тип машины → модель → исполнение. Единый источник для логиста, сервиса и КП."""
    __tablename__ = "equipment_models"
    __table_args__ = (UniqueConstraint("factory_id", "model", name="uq_factory_model"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"))
    machine_type: Mapped[str] = mapped_column(String(255))                   # 1.1 (текст, совместимость)
    machine_type_id: Mapped[Optional[str]] = mapped_column(ForeignKey("machine_types.id"))
    model: Mapped[str] = mapped_column(String(255))                          # 1.2
    trademark: Mapped[str] = mapped_column(String(128), default="")          # торговая марка оборудования (марка завода)
    brand_name: Mapped[str] = mapped_column(String(255), default="")         # собственное наименование для подачи под брендом компании
    names: Mapped[dict] = mapped_column(JSONType, default=dict)              # {"ru":..., "en":..., "zh":...}
    tech_params: Mapped[dict] = mapped_column(JSONType, default=dict)        # разделы RFQ 1–11 по кодам
    logistics_params: Mapped[dict] = mapped_column(JSONType, default=dict)   # поля с меткой L
    default_hs_code: Mapped[Optional[str]] = mapped_column(String(10))
    hs_info: Mapped[dict] = mapped_column(JSONType, default=dict)            # код ТН ВЭД, введённый логистом: {code, description, duty_pct, vat_pct, util_applies}
    maintenance_regulations: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    default_hours_per_month: Mapped[Optional[float]] = mapped_column(Float)
    acceptance_checklist: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # чек-лист приёмки на заводе: [{group, item}]
    status: Mapped[str] = mapped_column(String(16), default="approved")      # approved | pending | archived
    proposed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class EquipmentExecution(TimestampMixin, Base):
    """Исполнение модели: полный набор технических характеристик (формат таблицы задачи 4), опции РФ и изображения."""
    __tablename__ = "equipment_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(ForeignKey("equipment_models.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    brand_name: Mapped[str] = mapped_column(String(255), default="")
    tech_params: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # строки PositionTechParam без значений сделки
    local_options: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    images: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # {attachment_id, caption, kind, source}
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)   # хеш набора характеристик (без дублей)
    status: Mapped[str] = mapped_column(String(16), default="pending")        # pending | approved | archived
    source_deal_id: Mapped[Optional[str]] = mapped_column(String(36))
    source_position_id: Mapped[Optional[str]] = mapped_column(String(36))
    proposed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FactoryPriceHistory(Base):
    __tablename__ = "factory_price_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), index=True)
    model: Mapped[str] = mapped_column(String(255), index=True)
    execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    configuration: Mapped[str] = mapped_column(Text, default="")
    unit_price: Mapped[float] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    basis: Mapped[str] = mapped_column(String(32), default="")
    production_term: Mapped[dict] = mapped_column(JSONType, default=dict)    # {value, unit, start_event} (задача 3)
    factory_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    on: Mapped[date] = mapped_column(Date)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(16), default="rfq")   # rfq | offer | fact | price_list


class ClientPriceHistory(Base):
    """История цен, предложенных клиентам (задача 7/8): по каждой отправленной версии КП. Видят КД и ЗГД."""
    __tablename__ = "client_price_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    deal_number: Mapped[str] = mapped_column(String(32), default="")
    version_id: Mapped[Optional[str]] = mapped_column(String(36))
    version_no: Mapped[int] = mapped_column(Integer, default=0)
    kp_no: Mapped[str] = mapped_column(String(64), default="")
    variant_id: Mapped[Optional[str]] = mapped_column(String(36))
    variant_name: Mapped[str] = mapped_column(String(255), default="")
    factory_id: Mapped[Optional[str]] = mapped_column(String(36))
    factory_name: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    unit_price_rub: Mapped[Optional[float]] = mapped_column(Money)
    total_rub: Mapped[Optional[float]] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")          # валюта КП (D-095); цены в ней = RUB / fx_rate
    fx_rate: Mapped[Optional[float]] = mapped_column(Money)                 # курс на момент назначения цены (RUB за единицу)
    payment_terms: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    delivery_term: Mapped[str] = mapped_column(String(255), default="")
    kp_brand: Mapped[str] = mapped_column(String(16), default="factory")   # factory | company
    kp_path: Mapped[str] = mapped_column(String(512), default="")
    manager_id: Mapped[Optional[str]] = mapped_column(String(36))
    on: Mapped[date] = mapped_column(Date, index=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(16))              # обновляется при смене результата сделки
    chosen: Mapped[Optional[bool]] = mapped_column(Boolean)                  # какое предложение выбрал клиент


class LogisticsHistory(Base):
    __tablename__ = "logistics_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_code: Mapped[str] = mapped_column(String(32), index=True)
    route: Mapped[str] = mapped_column(String(255), default="")
    machine_type: Mapped[str] = mapped_column(String(255), default="")
    region: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    per_unit: Mapped[Optional[float]] = mapped_column(Money)         # ставка за единицу транспорта
    on: Mapped[date] = mapped_column(Date)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36))
    batch_id: Mapped[Optional[str]] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(16), default="plan")   # plan | fact


class HsCode(Base):
    """Справочник ТН ВЭД с версиями ставок и датами действия."""
    __tablename__ = "hs_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(10), index=True)
    description: Mapped[str] = mapped_column(Text)
    duty_pct: Mapped[float] = mapped_column(Float, default=0)
    vat_pct: Mapped[float] = mapped_column(Float, default=22)
    util_fee: Mapped[bool] = mapped_column(Boolean, default=False)
    licence_required: Mapped[bool] = mapped_column(Boolean, default=False)
    certification: Mapped[list[str]] = mapped_column(JSONType, default=list)  # ТР ТС 010/2011, ЭПБ, ПСМ
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    version_file: Mapped[str] = mapped_column(String(255), default="")


class RateSetting(Base):
    """«Ставки и нормативы» (раздел 11): ключ → значение (JSON) с историей изменений.
    Ключи: margin_threshold_pct, scenario_dk, scenario_dt, financing_rate_pct, labour_rates, per_diem,
    warranty_reserve_pct_by_type, overhead_scale, bank_fees, cash_gap_limit_rub, gd_deal_limit_rub, sla_days,
    fx_weights, fx_history_years, key_rate_pct, pboc_rate_pct, unplanned_reserve_pct, price_deviation_pct,
    logistics_deviation_pct, plan_fact_deviation_pct, kp_fx_clause_pct."""
    __tablename__ = "rate_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[Any] = mapped_column(JSONType)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # NULL = действует
    changed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")


class PaymentScheme(Base):
    __tablename__ = "payment_schemes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True)      # «30/70», «100 % предоплата»
    lines: Mapped[list[dict]] = mapped_column(JSONType)              # PaymentLine[]


class Glossary(TimestampMixin, Base):
    """Глоссарий переводов RU ↔ CN (задача 4): параметры, единицы, значения, фразы."""
    __tablename__ = "glossary"
    __table_args__ = (UniqueConstraint("term_ru", "kind", name="uq_glossary_term"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    term_ru: Mapped[str] = mapped_column(String(512), index=True)
    term_zh: Mapped[str] = mapped_column(String(512), default="", index=True)
    kind: Mapped[str] = mapped_column(String(16), default="param")    # param | unit | value | phrase
    status: Mapped[str] = mapped_column(String(16), default="confirmed")   # confirmed | machine
    changed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class TechParamTemplate(Base):
    """Шаблон перечня технических характеристик по типу машины (задача 4). Обязательность задают КД/ЗГД."""
    __tablename__ = "tech_param_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_type_id: Mapped[Optional[str]] = mapped_column(ForeignKey("machine_types.id"), index=True)   # NULL = общий шаблон
    no: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(16), default="")      # код RFQ (1.5 …), если параметр из фиксированного перечня
    name_ru: Mapped[str] = mapped_column(String(512))
    name_zh: Mapped[str] = mapped_column(String(512), default="")
    unit: Mapped[str] = mapped_column(String(64), default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    section: Mapped[str] = mapped_column(String(255), default="")


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), index=True)        # rfq | kp_docx | excel | mail | wecom | logo
    version: Mapped[int] = mapped_column(Integer, default=1)
    file_path: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # юридический блок (этап 2): библиотека шаблонов договоров и приложений + база примеров
    title: Mapped[str] = mapped_column(String(255), default="")
    language: Mapped[str] = mapped_column(String(16), default="ru")           # ru | ru-zh | ru-en | en | zh
    country: Mapped[str] = mapped_column(String(64), default="")              # страна контрагента / сделки (CN, KG, RU, …)
    deal_kind: Mapped[str] = mapped_column(String(16), default="")            # purchase | sale
    variables: Mapped[dict] = mapped_column(JSONType, default=dict)           # найденные при загрузке: {"variables": [...], "loops": {...}, "unknown": [...]}
    notes: Mapped[str] = mapped_column(Text, default="")
    is_example: Mapped[bool] = mapped_column(Boolean, default=False)          # база примеров (договоры заказчиков, протоколы), не шаблон


class FxRate(Base):
    """Курсы ЦБ РФ (история с 2014 г.) и ручные курсы администратора."""
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("on", "currency", "source", name="uq_fx_day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    on: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    rate: Mapped[float] = mapped_column(Float)         # RUB за единицу валюты (с учётом номинала)
    source: Mapped[str] = mapped_column(String(16), default="cbr")   # cbr | manual
    comment: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ сделки

class Deal(TimestampMixin, Base):
    __tablename__ = "deals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)           # D-2026-0042
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    contact: Mapped[str] = mapped_column(String(255), default="")
    site: Mapped[str] = mapped_column(String(255), default="")              # объект / месторождение
    region: Mapped[str] = mapped_column(String(255), default="")
    requested_at: Mapped[date] = mapped_column(Date)
    kp_due: Mapped[Optional[date]] = mapped_column(Date)
    report_date: Mapped[Optional[date]] = mapped_column(Date)                  # дата отчёта (расчёта): курсы и прогноз на эту дату; задают КД / ЗГД
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[DealStatus] = mapped_column(SAEnum(DealStatus, name="deal_status"), default=DealStatus.DRAFT, index=True)
    manager_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    logistician_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    service_head_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    base_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    deal_currency: Mapped[Optional[str]] = mapped_column(String(3))   # валюта сделки (КП, контракт); пусто — валюта предложения поставщика (D-080)
    operating_conditions: Mapped[dict] = mapped_column(JSONType, default=dict)   # 0.4–0.6
    service_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    target_hours: Mapped[Optional[float]] = mapped_column(Float)
    client_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # задаёт КД (задача 1); пусто = типовая схема
    client_payment_wishes: Mapped[str] = mapped_column(Text, default="")             # пожелания клиента по оплате (менеджер)
    bank_guarantee: Mapped[dict] = mapped_column(JSONType, default=dict)             # {mode: one_time_pct|annual_pct|fixed, value} — переопределение КД
    scenario_override: Mapped[dict] = mapped_column(JSONType, default=dict)    # ΔK/Δt сделки (КД, с комментарием)
    # рабочее место логиста (D-073): {pickup: {factory_id: адрес}, delivery_address, requests: [...], quotes: [...]}
    logistics_info: Mapped[dict] = mapped_column(JSONType, default=dict)
    negotiation_info: Mapped[dict] = mapped_column(JSONType, default=dict)   # торги: внутренние курсы заказчика и их история (D-079)
    logistics_done: Mapped[bool] = mapped_column(Boolean, default=False)
    service_done: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    approved_version_id: Mapped[Optional[str]] = mapped_column(String(36))
    storage_folder: Mapped[str] = mapped_column(String(512), default="")
    outcome: Mapped[Optional[str]] = mapped_column(String(16))
    outcome_reason: Mapped[str] = mapped_column(Text, default="")
    # ожидание результата после отправки КП: когда менеджер последний раз уточнял статус, что сказал клиент, когда ждём решение
    decision_expected_on: Mapped[Optional[date]] = mapped_column(Date)
    status_checked_on: Mapped[Optional[date]] = mapped_column(Date)
    status_check_note: Mapped[str] = mapped_column(Text, default="")
    plan_fact_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    # задача 5: альтернативные предложения в одном КП (по умолчанию одно предложение без выбора вариантов)
    has_alternatives: Mapped[bool] = mapped_column(Boolean, default=False)
    won_variant_id: Mapped[Optional[str]] = mapped_column(String(36))
    # задача 9: несколько заводов по одному запросу клиента, дублирование заявки, бренд подачи
    parent_deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    request_group_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    kp_brand: Mapped[str] = mapped_column(String(16), default="factory")           # factory | company
    kp_images_mode: Mapped[str] = mapped_column(String(16), default="document")     # document | attachment | both (задача 10)
    # задача 2: заключение и исполнение контракта
    contract_number: Mapped[str] = mapped_column(String(128), default="")
    contract_signed_at: Mapped[Optional[date]] = mapped_column(Date)
    factory_contract_signed_at: Mapped[Optional[date]] = mapped_column(Date)
    factory_contract_number: Mapped[Optional[str]] = mapped_column(String(64))     # номер контракта с заводом
    client_requisites_id: Mapped[Optional[str]] = mapped_column(String(36))        # выбранный набор реквизитов клиента
    client_requisites: Mapped[dict] = mapped_column(JSONType, default=dict)        # снимок реквизитов на момент договора
    # изменения номера/даты подписанных договоров (только ЗГД или КД): [{side, old_number, old_date, number, date, reason, by, at}]
    contract_history: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    contract_steps: Mapped[dict] = mapped_column(JSONType, default=dict)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)   # удалена КД/ЗГД (в корзине, можно восстановить)
    deleted_by: Mapped[Optional[str]] = mapped_column(String(36))
    delete_reason: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)   # не черновик — переносится в архив, а не удаляется
    archived_by: Mapped[Optional[str]] = mapped_column(String(36))
    archive_reason: Mapped[str] = mapped_column(Text, default="")
    legal_review: Mapped[dict] = mapped_column(JSONType, default=dict)   # проверка юриста: {status: in_review|ok|remarks, comment, by, at}   # подэтапы заключения контракта: код → {done, on, by, comment}
    execution_closed_at: Mapped[Optional[date]] = mapped_column(Date)
    contract_terms_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_execution: Mapped[bool] = mapped_column(Boolean, default=False)              # сделка на стадии контракта/исполнения (возврат после повторной визы)
    execution_started: Mapped[bool] = mapped_column(Boolean, default=False)         # контракт подписан, исполнение начато (повторная виза возвращает в исполнение)

    client = relationship("Client")
    positions = relationship("Position", back_populates="deal", cascade="all, delete-orphan", order_by="Position.created_at")   # D-120: порядок позиций — как вводили (КП, карточка)
    chain_approval: Mapped[dict] = mapped_column(JSONType, default=dict)   # виза ФД/ЗГД на внутреннюю цепочку до КП (D-166): {sig, by, by_name, at, chain}
    variants = relationship("Variant", back_populates="deal", cascade="all, delete-orphan")
    batches = relationship("Batch", back_populates="deal", cascade="all, delete-orphan")
    versions = relationship("DealVersion", back_populates="deal", order_by="DealVersion.version_no")


class Position(TimestampMixin, Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    factory_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"))   # пусто — завод ещё не выбран (до ответа завода, D-069)
    equipment_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id"))
    machine_type: Mapped[str] = mapped_column(String(255), default="")
    trademark: Mapped[str] = mapped_column(String(128), default="")            # торговая марка оборудования (по заводу)
    model: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1)                       # 13.4
    unit_price: Mapped[float] = mapped_column(Money)                           # 13.5
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    options: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # 12.x
    options_price: Mapped[float] = mapped_column(Money, default=0)
    basis: Mapped[str] = mapped_column(String(32), default="FCA")             # 13.2
    delivery_point: Mapped[str] = mapped_column(String(255), default="")       # 13.3
    production_days: Mapped[int] = mapped_column(Integer, default=90)          # 14.1 (календарные дни, производное от срока ниже)
    delivery_days: Mapped[int] = mapped_column(Integer, default=10)            # 14.2
    # задача 3: срок производства с единицами и точкой отсчёта; срок поставки клиенту — единица и точка отсчёта
    production_term: Mapped[dict] = mapped_column(JSONType, default=dict)      # {value, unit: calendar_days|working_days|weeks|date, date, start_event: factory_contract|factory_prepayment}
    delivery_term: Mapped[dict] = mapped_column(JSONType, default=dict)        # {unit: calendar_days|working_days|weeks, start_event: client_contract|client_prepayment|lc_opened}
    warranty_months: Mapped[Optional[int]] = mapped_column(Integer)            # 14.4
    warranty_hours: Mapped[Optional[float]] = mapped_column(Float)             # 14.5
    zip_price: Mapped[Optional[float]] = mapped_column(Money)                  # 13.8
    factory_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # задаёт КД (задача 1); пусто = типовая схема
    factory_payments_offer: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # условия завода из RFQ/ответа (справка для КД)
    tech_params: Mapped[dict] = mapped_column(JSONType, default=dict)          # все поля RFQ позиции (по кодам)
    logistics_params: Mapped[dict] = mapped_column(JSONType, default=dict)     # метка L
    price_reference: Mapped[dict] = mapped_column(JSONType, default=dict)      # «Справка по цене»: last, on, deviation, comment
    rfq_import_id: Mapped[Optional[str]] = mapped_column(ForeignKey("rfq_imports.id"))
    execution_id: Mapped[Optional[str]] = mapped_column(String(36))            # исполнение из каталога (задача 7)
    offer_id: Mapped[Optional[str]] = mapped_column(String(36))                # ответ завода, из которого взята позиция (задача 9)
    # блок логиста (уровень позиции)
    logistics: Mapped[dict] = mapped_column(JSONType, default=dict)            # domestic, export, hs_code, duty, vat, util, cert + comments
    batch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batches.id"))
    # блок сервиса
    service_calc: Mapped[dict] = mapped_column(JSONType, default=dict)         # ServiceInput + ServiceResult
    zip_items: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # ЗИП позиции: [{assembly, article, name, unit, qty, price, currency, interval_hours, kind: supply|to, lead_days, comment}]
    warranty_reserve_pct: Mapped[Optional[float]] = mapped_column(Float)
    # задача 2: фактические даты событий позиции (готовность, отгрузка, ПНР …) — пересчёт платежей от факта
    actual_events: Mapped[dict] = mapped_column(JSONType, default=dict)        # {event: "YYYY-MM-DD"}

    deal = relationship("Deal", back_populates="positions")
    tech_param_rows = relationship("PositionTechParam", cascade="all, delete-orphan", order_by="PositionTechParam.no")
    local_option_rows = relationship("LocalOption", cascade="all, delete-orphan")
    image_rows = relationship("PositionImage", cascade="all, delete-orphan")

    @property
    def designation(self) -> str:
        """Обозначение оборудования: торговая марка + модель (SHILI FL-0.5(C))."""
        return designation(self.trademark, self.model)


class PositionTechParam(TimestampMixin, Base):
    """Строка таблицы технических характеристик позиции (задача 4): № / параметр / ед. / требования заказчика / значение / комментарий."""
    __tablename__ = "position_tech_params"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    no: Mapped[int] = mapped_column(Integer, default=1)
    code: Mapped[str] = mapped_column(String(16), default="")
    name_ru: Mapped[str] = mapped_column(String(512))
    name_zh: Mapped[str] = mapped_column(String(512), default="")
    unit: Mapped[str] = mapped_column(String(64), default="")
    customer_requirement_ru: Mapped[str] = mapped_column(Text, default="")
    customer_requirement_zh: Mapped[str] = mapped_column(Text, default="")
    value_ru: Mapped[str] = mapped_column(Text, default="")
    value_zh: Mapped[str] = mapped_column(Text, default="")
    comment_ru: Mapped[str] = mapped_column(Text, default="")
    comment_zh: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    local_ru: Mapped[bool] = mapped_column(Boolean, default=False)           # реализуется в России (задача 6) — не идёт заводу
    source: Mapped[str] = mapped_column(String(16), default="manual")        # manual | client_import | factory_answer | catalog | rfq
    translation_status: Mapped[str] = mapped_column(String(16), default="none")   # none | glossary | machine | verified
    section: Mapped[str] = mapped_column(String(255), default="")              # раздел опросника («1. Общие данные»), как в файле
    filled_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class LocalOption(TimestampMixin, Base):
    """Опция, реализуемая в России (задача 6): закупка, установка, статья «Доукомплектование в РФ»."""
    __tablename__ = "local_options"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    tech_param_id: Mapped[Optional[str]] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    supplier: Mapped[str] = mapped_column(String(255), default="")
    quote_attachment_id: Mapped[Optional[str]] = mapped_column(String(36))
    cost: Mapped[float] = mapped_column(Money, default=0.0)                   # за единицу
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    supplier_lead_days: Mapped[int] = mapped_column(Integer, default=30)
    needs_install: Mapped[bool] = mapped_column(Boolean, default=True)
    install_stage: Mapped[str] = mapped_column(String(32), default="during_commissioning")   # before_handover | during_commissioning
    installer: Mapped[str] = mapped_column(String(32), default="service")    # service | contractor
    install_days: Mapped[int] = mapped_column(Integer, default=0)
    install_cost_rub: Mapped[float] = mapped_column(Money, default=0.0)
    per_unit: Mapped[bool] = mapped_column(Boolean, default=False)          # D-089: кол-во и монтаж — на 1 машину (× количество позиции); старые — на всю позицию
    status: Mapped[str] = mapped_column(String(16), default="estimate")      # estimate | approved | ordered | received | installed
    cost_approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    ordered_at: Mapped[Optional[date]] = mapped_column(Date)
    installed_at: Mapped[Optional[date]] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, default="")


class PositionImage(Base):
    """Изображения и чертежи позиции (задача 10); выбор для КП делает КД/ЗГД."""
    __tablename__ = "position_images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), index=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"))
    kind: Mapped[str] = mapped_column(String(16), default="photo")           # photo | render | drawing | scheme
    source: Mapped[str] = mapped_column(String(16), default="own")           # client | factory | own
    caption: Mapped[str] = mapped_column(String(512), default="")
    has_factory_logo: Mapped[bool] = mapped_column(Boolean, default=False)
    in_kp: Mapped[bool] = mapped_column(Boolean, default=False)
    kp_order: Mapped[int] = mapped_column(Integer, default=0)
    send_to_factory: Mapped[bool] = mapped_column(Boolean, default=False)    # приложить к запросу заводу
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactoryOffer(TimestampMixin, Base):
    """Ответ завода на запрос клиента (задача 9): кандидат для сравнения «параметр × завод» и отбора КД/ЗГД."""
    __tablename__ = "factory_offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    source_position_id: Mapped[Optional[str]] = mapped_column(String(36))    # позиция-запрос клиента, по которой собирались ответы
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"))
    model: Mapped[str] = mapped_column(String(255), default="")
    unit_price: Mapped[Optional[float]] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    basis: Mapped[str] = mapped_column(String(32), default="FCA")
    production_term: Mapped[dict] = mapped_column(JSONType, default=dict)
    factory_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    params: Mapped[list[dict]] = mapped_column(JSONType, default=list)       # строки технических характеристик с value_ru/value_zh/comment
    images: Mapped[list[str]] = mapped_column(JSONType, default=list)        # attachment ids
    request_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    answer_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="requested")     # requested | answered | selected | rejected
    selected_deal_id: Mapped[Optional[str]] = mapped_column(String(36))      # заявка, в которую перенесён ответ
    selected_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")
    # зеркальные запросы через компанию группы (D-150): предложение компании группы (U) видно в сделке, запрос компании группы
    # конечному поставщику (L) — внутри него. U: end_supplier_id, route_id, chain; L: mirror_of_id → U.
    mirror_of_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)       # L → U
    end_supplier_id: Mapped[Optional[str]] = mapped_column(String(36))                # U: конечный поставщик (завод в другой стране)
    route_id: Mapped[Optional[str]] = mapped_column(String(36))                       # U: маршрут поставки через компанию группы
    chain: Mapped[dict] = mapped_column(JSONType, default=dict)                       # U: снимок цепочки (ступени, наценки, маржа группы, курсы, версия политики)
    mirror_state: Mapped[str] = mapped_column(String(24), default="")                 # U: waiting | ok | no_policy | error | changed_after_select
    revision: Mapped[int] = mapped_column(Integer, default=0)                         # U: редакция зеркального предложения
    transfer_price: Mapped[Optional[float]] = mapped_column(Money)                    # U: трансфертная цена, зафиксированная ФД/ЗГД (вместо наценки)
    transfer_note: Mapped[str] = mapped_column(Text, default="")


class ExecutionStage(TimestampMixin, Base):
    """Этап исполнения контракта (задача 2): плановая и фактическая дата, ответственный, сумма для платёжных этапов."""
    __tablename__ = "execution_stages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[Optional[str]] = mapped_column(String(36))
    batch_id: Mapped[Optional[str]] = mapped_column(String(36))
    local_option_id: Mapped[Optional[str]] = mapped_column(String(36))
    code: Mapped[str] = mapped_column(String(32))                             # contract_signed, factory_contract, client_advance, lc_opened, …
    title: Mapped[str] = mapped_column(String(255))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    planned_on: Mapped[Optional[date]] = mapped_column(Date)                   # прогноз: пересчитывается от фактов и уточнённых дат
    baseline_on: Mapped[Optional[date]] = mapped_column(Date)                  # базовый план на момент начала исполнения (не меняется)
    refined_on: Mapped[Optional[date]] = mapped_column(Date)                   # уточнённая внутренняя дата (напр., завод подтвердил готовность)
    refined_note: Mapped[str] = mapped_column(Text, default="")
    actual_on: Mapped[Optional[date]] = mapped_column(Date)
    amount: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    actual_amount: Mapped[Optional[float]] = mapped_column(Float)
    amount_rub_net: Mapped[Optional[float]] = mapped_column(Money)            # D-120: платежи клиента — справочно ₽ без НДС (как в расчёте)
    responsible_roles: Mapped[list[str]] = mapped_column(JSONType, default=list)
    event: Mapped[str] = mapped_column(String(32), default="")                # событие расчёта, которое закрывает этап
    done_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    date_history: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # изменения дат: [{kind: factory|refine|actual|acceptance, on, note, by, at}]


class Batch(TimestampMixin, Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="Партия 1")
    route: Mapped[str] = mapped_column(String(255), default="завод → Маньчжурия → Забайкальск → пункт поставки")
    transport_units: Mapped[int] = mapped_column(Integer, default=1)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    ready_from: Mapped[Optional[date]] = mapped_column(Date)
    ready_to: Mapped[Optional[date]] = mapped_column(Date)
    transit_days: Mapped[int] = mapped_column(Integer, default=14)
    customs_days: Mapped[int] = mapped_column(Integer, default=5)
    rf_delivery_days: Mapped[int] = mapped_column(Integer, default=5)      # доставка по РФ после выпуска ДТ (задача 3)
    border_planned: Mapped[Optional[date]] = mapped_column(Date)           # плановая дата пересечения границы КНР–РФ (CMR) (задача 1)
    border_actual: Mapped[Optional[date]] = mapped_column(Date)            # факт: CMR с отметкой таможни — вводит логист
    actual_events: Mapped[dict] = mapped_column(JSONType, default=dict)    # {shipment|border|declaration|delivery: "YYYY-MM-DD"} (задача 2)
    allocation: Mapped[str] = mapped_column(String(8), default="cost")
    items: Mapped[dict] = mapped_column(JSONType, default=dict)            # freight, insurance, broker, rf_delivery + comments
    payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # график платежей перевозчику/брокеру

    deal = relationship("Deal", back_populates="batches")


class Variant(TimestampMixin, Base):
    __tablename__ = "variants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    position_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    price_rub: Mapped[Optional[float]] = mapped_column(Money)              # без НДС, назначает КД
    price_mode: Mapped[str] = mapped_column(String(16), default="amount")  # amount | markup | margin
    price_currency: Mapped[Optional[str]] = mapped_column(String(3))       # валюта цены клиенту (по умолчанию — валюта предложения завода)
    price_amount: Mapped[Optional[float]] = mapped_column(Money)           # цена в этой валюте без НДС
    price_fx_rate: Mapped[Optional[float]] = mapped_column(Money)          # курс ЦБ (RUB за единицу) на момент назначения цены
    price_vat_included: Mapped[bool] = mapped_column(Boolean, default=False)   # КД ввёл цену с НДС (D-066); price_amount / price_rub — всегда без НДС
    price_vat_pct: Mapped[Optional[float]] = mapped_column(Float)              # ставка НДС на момент ввода
    price_input_amount: Mapped[Optional[float]] = mapped_column(Money)         # сумма, как её ввёл КД (с НДС или без)
    price_input_currency: Mapped[Optional[str]] = mapped_column(String(3))     # валюта ввода (D-080); цена хранится в валюте сделки
    price_input_units: Mapped[Optional[int]] = mapped_column(Integer)          # введено за 1 шт. при этом количестве (D-085)
    service_in_kp: Mapped[bool] = mapped_column(Boolean, default=False)
    client_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    client_payments_alt: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # сравнение двух условий оплаты
    overhead_pct_override: Mapped[Optional[float]] = mapped_column(Float)
    overhead_override_comment: Mapped[str] = mapped_column(Text, default="")
    in_kp: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)       # служебный вариант «одно предложение» (задача 5)

    deal = relationship("Deal", back_populates="variants")


class KpRevision(Base):
    """Редакция КП (КД / ЗГД): скидки и надбавки, услуги, комплектация — с файлами и описанием изменений."""
    __tablename__ = "kp_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(36))                 # утверждённая версия расчёта, от которой редакция
    no: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    description: Mapped[str] = mapped_column(Text, default="")                    # что изменено и почему
    changes: Mapped[dict] = mapped_column(JSONType, default=dict)                 # по предложениям: adjustments, extras, configuration, service_in_kp
    price_before_rub: Mapped[Optional[float]] = mapped_column(Money)
    price_rub: Mapped[Optional[float]] = mapped_column(Money)                     # итог КП без НДС после изменений
    margin_pct: Mapped[Optional[float]] = mapped_column(Float)                    # маржа базового сценария по итоговой цене (ориентир)
    prices: Mapped[dict] = mapped_column(JSONType, default=dict)                  # {variant_id: итог без НДС}
    kp_path: Mapped[str] = mapped_column(String(1024), default="")
    pdf_path: Mapped[str] = mapped_column(String(1024), default="")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class QuoteLink(Base):
    """Ссылка для ответа логистической компании на запрос (D-107): без входа в CRM, по одному адресу получателя.
    В базе — только хэш ссылки; ответ пишется в ответы сделки, повторная отправка по той же ссылке заменяет прежний ответ."""
    __tablename__ = "quote_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), default="")
    request_no: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    quote_ids: Mapped[str] = mapped_column(Text, default="")          # ответы по ссылке (через запятую): по строке на услугу
    answers: Mapped[int] = mapped_column(Integer, default=0)


class OfferPrice(Base):
    """Цены предложения (D-109): каждое назначение цены — запись; действующая цена берётся отсюда одной функцией
    (services/offer_prices.py). Виды: kd — цена КД в предложении (в т. ч. после переноса при изменении условий контракта),
    revision — итог редакции КП утверждённой версии (в т. ч. шаг торгов). Записи не меняются и не удаляются."""
    __tablename__ = "offer_prices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    variant_id: Mapped[str] = mapped_column(String(36), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(36))          # для редакций — утверждённая версия
    kind: Mapped[str] = mapped_column(String(16))                          # kd | revision
    source_id: Mapped[str] = mapped_column(String(36), default="")         # редакция КП
    revision_no: Mapped[Optional[int]] = mapped_column(Integer)
    total_rub: Mapped[float] = mapped_column(Money)                        # итог без НДС, RUB (для редакции — с услугами КП)
    extras_rub: Mapped[float] = mapped_column(Money, default=0)            # услуги, добавленные в КП (в расчёт маржи не входят)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")        # валюта цены клиенту
    fx_rate: Mapped[Optional[float]] = mapped_column(Money)                # RUB за единицу валюты цены
    units: Mapped[int] = mapped_column(Integer, default=1)                 # машин в предложении
    unit_amount: Mapped[Optional[float]] = mapped_column(Money)            # за 1 машину в валюте цены, без НДС
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    seq: Mapped[int] = mapped_column(BigInteger, default=0)                # порядок записей в одной транзакции


class FileDownloadRequest(Base):
    """Запрос на скачивание файла сделки, когда скачивание закрыто для роли (D-077); одобрение действует 24 часа."""
    __tablename__ = "file_download_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    file_name: Mapped[str] = mapped_column(String(512), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")     # pending | approved | declined
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[Optional[str]] = mapped_column(String(36))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class NegotiationStep(Base):
    """Шаг торгов (D-074): снижение цены и (или) изменение условий после КП; порождает редакцию КП."""
    __tablename__ = "negotiation_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(36))
    no: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20), default="price")                # price | terms | price_terms
    reason: Mapped[str] = mapped_column(String(30), default="client")
    comment: Mapped[str] = mapped_column(Text, default="")
    input: Mapped[dict] = mapped_column(JSONType, default=dict)                   # {variant_id: {mode, basis, currency, value}}
    terms: Mapped[dict] = mapped_column(JSONType, default=dict)                   # {variant_id: {client_payments, delivery_term}}
    result: Mapped[dict] = mapped_column(JSONType, default=dict)                  # {variant_id: цены, маржа по сценариям, минимальная цена…}
    price_before_rub: Mapped[Optional[float]] = mapped_column(Money)
    price_rub: Mapped[Optional[float]] = mapped_column(Money)
    margin_pct: Mapped[Optional[float]] = mapped_column(Float)
    show_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    kp_revision_id: Mapped[Optional[str]] = mapped_column(String(36))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_to: Mapped[list] = mapped_column(JSONType, default=list)
    client_answer: Mapped[str] = mapped_column(Text, default="")
    review: Mapped[dict] = mapped_column(JSONType, default=dict)                  # «на рассмотрении клиента»: since, remind_on, task_id, assignee_id, note, closed_at


class DealVersion(Base):
    """Версия расчёта: создаётся при каждом возврате на доработку и каждом утверждении. Неизменяема."""
    __tablename__ = "deal_versions"
    __table_args__ = (UniqueConstraint("deal_id", "version_no", name="uq_deal_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))                 # approval | rework | visa_reset | contract_change
    calc_input: Mapped[dict] = mapped_column(JSONType)              # DealCalcInput (включая курс ЦБ на дату)
    calc_result: Mapped[dict] = mapped_column(JSONType)             # DealCalcResult
    excel_path: Mapped[str] = mapped_column(String(512), default="")
    excel_manager_path: Mapped[str] = mapped_column(String(512), default="")
    kp_path: Mapped[str] = mapped_column(String(512), default="")
    kp_meta: Mapped[dict] = mapped_column(JSONType, default=dict)   # бренд подачи, вложенные изображения, способ вложения (задачи 9, 10)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    deal = relationship("Deal", back_populates="versions")


class RfqImport(Base):
    __tablename__ = "rfq_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    factory_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"))
    file_path: Mapped[str] = mapped_column(String(512))
    file_hash: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(32), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    parsed: Mapped[dict] = mapped_column(JSONType, default=dict)
    errors: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    imported_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    assignee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    block: Mapped[str] = mapped_column(String(64), default="")   # «stage:<uuid>», «local_option_quote:<uuid>»
    due: Mapped[date] = mapped_column(Date)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    overdue_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_reassignment: Mapped[bool] = mapped_column(Boolean, default=False)   # исполнитель отключён — задачу нужно передать (04 §5.2)


class Approval(Base):
    """Виза: фиксирует версию расчёта, пользователя, дату и комментарий."""
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deal_versions.id"))
    step: Mapped[str] = mapped_column(String(32))          # commercial | financial | general
    role: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str] = mapped_column(Text, default="")
    prices_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # сброс при изменении цены


class ChangeEvent(Base):
    """Реестр событий изменений (D-181): только добавление, без значений полей — сущность, id, действие, кто.

    Основа истории и синхронизации приложения без сети: клиент читает события после своего курсора и
    перезапрашивает изменившиеся записи через обычные API (с его правами). Изменение и удаление строк
    запрещены триггером в базе. xid — номер транзакции: читаются только события завершённых транзакций,
    поэтому медленная транзакция с меньшим номером не «проскочит» мимо курсора."""
    __tablename__ = "change_events"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    xid: Mapped[int] = mapped_column(BigInteger, server_default=sa_text("(pg_current_xact_id()::text)::bigint"))
    actor_id: Mapped[Optional[str]] = mapped_column(String(36))
    entity: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)


class AuditLog(Base):
    """Журнал действий: кто, когда, что было / стало. Только вставка."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(128))   # id, «роль/группа», имя файла
    action: Mapped[str] = mapped_column(String(32))
    before: Mapped[Optional[dict]] = mapped_column(JSONType)
    after: Mapped[Optional[dict]] = mapped_column(JSONType)
    comment: Mapped[str] = mapped_column(Text, default="")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    folder: Mapped[str] = mapped_column(String(32))     # 01_rfq | 02_logistics | 03_service | 04_cost | 05_kp | 06_mail | 07_fact
    entity: Mapped[str] = mapped_column(String(32), default="")   # batch | position | version | message | deal_doc
    entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(32), default="")   # документы сделки: tender | contract_form | client_spec | client_other | disagreements | legal_opinion | contract_signed
    comment: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    storage_url: Mapped[str] = mapped_column(String(1024), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # проверка антивирусом (04 §8, D-049): clean | infected | error | legacy_unscanned (загружен без проверки) | generated (создан системой)
    scan_status: Mapped[str] = mapped_column(String(20), default="legacy_unscanned", server_default="legacy_unscanned")
    scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FeedEntry(Base):
    """Лента сделки: переписка с заводом (WeCom), с клиентом (Outlook), системные события."""
    __tablename__ = "deal_feed"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))        # wecom | mail_in | mail_out | system | comment
    direction: Mapped[str] = mapped_column(String(8), default="in")
    counterparty: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachment_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    # D-127: обсуждения сделки — сообщения (kind="comment"), загруженные письма (mail_in / mail_out) и события в одной ленте
    section: Mapped[str] = mapped_column(String(16), default="", server_default="")    # sale | logistics | service | pricing | legal | execution | finance | "" — общее
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)          # ответ на сообщение
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)                     # q_client | a_client | q_factory | a_factory | internal
    mentions: Mapped[list[str]] = mapped_column(JSONType, default=list)                 # id сотрудников
    awaiting: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")   # «ждём ответа»
    due_on: Mapped[Optional[date]] = mapped_column(Date)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(36))                      # сотрудник или ответное сообщение
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pinned_by: Mapped[Optional[str]] = mapped_column(String(36))
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    edit_history: Mapped[list[dict]] = mapped_column(JSONType, default=list)             # прежние тексты после правки
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PlanFactEntry(Base):
    """План vs факт (раздел 12): фактические суммы по статьям и платежам."""
    __tablename__ = "plan_fact_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[Optional[str]] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str] = mapped_column(String(16))        # cost_item | payment_in | payment_out | delivery | fx
    cost_item_code: Mapped[Optional[str]] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[float] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    fx_rate: Mapped[Optional[float]] = mapped_column(Money)
    on: Mapped[date] = mapped_column(Date)
    deviation_reason: Mapped[str] = mapped_column(String(64), default="")   # fx | route | border_delay | client_req | estimate_error
    comment: Mapped[str] = mapped_column(Text, default="")
    entered_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationLog(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    event: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued | sent | failed | read
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    error: Mapped[str] = mapped_column(Text, default="")
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # канал «в системе»: когда прочитано
    link: Mapped[Optional[str]] = mapped_column(String(300))   # куда ведёт уведомление (поддержка: /support/<id>); без содержимого


class ImportMapping(TimestampMixin, Base):
    """Задача 4: запомненное сопоставление столбцов импорта таблицы для клиента / завода (повторные загрузки)."""
    __tablename__ = "import_mappings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(80), index=True)          # client:<id> | factory:<id>
    target: Mapped[str] = mapped_column(String(32))                     # requirements | factory_answer
    header_sig: Mapped[list] = mapped_column(JSONType, default=list)   # нормализованные заголовки — по ним узнаём «тот же формат»
    mapping: Mapped[dict] = mapped_column(JSONType, default=dict)      # {header_row, first_row, columns}
    updated_by: Mapped[Optional[str]] = mapped_column(String(36))


class AuthSession(Base):
    """Вход сотрудника на сервере (docs/operations/02 §5): JWT несёт только sid; отзыв действует сразу. Сам токен не хранится."""
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_active", "user_id", "revoked_at", "absolute_expires_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    sid: Mapped[str] = mapped_column(String(36), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[Optional[str]] = mapped_column(String(36))
    revoke_reason: Mapped[str] = mapped_column(String(16), default="")   # logout | user | admin | password | offboard | mfa_reset | security
    remember: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_method: Mapped[str] = mapped_column(String(16), default="password")   # password | oidc | recovery | dev | legacy
    mfa_level: Mapped[str] = mapped_column(String(16), default="none")         # none | totp | recovery
    last_mfa_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_step_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # подтверждение опасного действия (TOTP или пароль)
    ip_prefix: Mapped[str] = mapped_column(String(64), default="")
    user_agent_short: Mapped[str] = mapped_column(String(120), default="")


class MfaCredential(Base):
    """TOTP сотрудника: секрет только зашифрованным; новый секрет не заменяет действующий до подтверждения кодом (04 §3.1)."""
    __tablename__ = "mfa_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text, default="")
    key_id: Mapped[str] = mapped_column(String(16), default="")
    enabled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int] = mapped_column(BigInteger, default=0)       # защита от повторного использования того же кода
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pending_secret_ciphertext: Mapped[str] = mapped_column(Text, default="")
    pending_key_id: Mapped[str] = mapped_column(String(16), default="")
    pending_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    used_session_id: Mapped[Optional[str]] = mapped_column(String(36))


class MfaChallenge(Base):
    """Второй шаг входа (mfa_pending): одноразовый, 5 минут, не даёт доступа к CRM (04 §3.2). Хранится только хэш проверочного значения."""
    __tablename__ = "mfa_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    verifier_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    remember: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_method: Mapped[str] = mapped_column(String(16), default="password")
    ip_prefix: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PasswordHistory(Base):
    __tablename__ = "password_history"
    __table_args__ = (UniqueConstraint("user_id", "password_hash", name="uq_password_history"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    """D-143: отметки фоновых заданий (последний запуск, успех, ошибка); строки heartbeat:* — «процесс жив»."""
    __tablename__ = "job_runs"
    job: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    error_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    runs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    host: Mapped[str] = mapped_column(String(64), default="", server_default="")


class PasswordLink(Base):
    """D-132: одноразовая ссылка «Задать пароль» (приглашение нового сотрудника или сброс). В базе — только хэш токена."""
    __tablename__ = "password_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))                  # invite | reset | forgot
    sent_to: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))     # пусто — сотрудник сам нажал «Забыли пароль?»
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SecurityEvent(Base):
    """События безопасности (02 §5): только вставка; без паролей, кодов, токенов и содержимого документов."""
    __tablename__ = "security_events"
    __table_args__ = (Index("ix_security_events_actor_type_at", "actor_user_id", "event_type", "occurred_at"),
                      Index("ix_security_events_subject_at", "subject_user_id", "occurred_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(36))
    subject_user_id: Mapped[Optional[str]] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(32))       # auth | session | mfa | password | offboard | access | download | export
    action: Mapped[str] = mapped_column(String(48))
    result: Mapped[str] = mapped_column(String(16), default="success")   # success | denied | error
    request_id: Mapped[str] = mapped_column(String(36), default="")
    session_id: Mapped[Optional[str]] = mapped_column(String(36))
    ip_prefix: Mapped[str] = mapped_column(String(64), default="")
    entity_type: Mapped[str] = mapped_column(String(32), default="")
    entity_id: Mapped[str] = mapped_column(String(128), default="")
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    format: Mapped[str] = mapped_column(String(16), default="")
    row_count: Mapped[Optional[int]] = mapped_column(Integer)
    byte_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer)
    alert_id: Mapped[Optional[str]] = mapped_column(String(36))


class AppError(Base):
    """Ошибка системы (D-117): необработанная ошибка сервера или страницы в браузере. Одна запись на «отпечаток»
    (где + тип + место в коде), повторы — счётчик. Без тел запросов, содержимого файлов и персональных данных."""
    __tablename__ = "app_errors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(16))                 # server | browser
    kind: Mapped[str] = mapped_column(String(120))                  # тип ошибки (ValueError, TypeError…)
    message: Mapped[str] = mapped_column(Text, default="")
    where: Mapped[str] = mapped_column(String(300), default="")     # метод и путь API / адрес страницы без параметров
    trace: Mapped[str] = mapped_column(Text, default="")            # строки кода приложения, без значений переменных
    count: Mapped[int] = mapped_column(Integer, default=1)
    users: Mapped[list] = mapped_column(JSONType, default=list)     # последние 10 сотрудников, у кого случилась
    last_request_id: Mapped[str] = mapped_column(String(64), default="")
    release: Mapped[str] = mapped_column(String(40), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(36))


class SecurityAlert(Base):
    """Предупреждение безопасности (02 «security_alerts», 04 §7.3): одна открытая запись на правило и сотрудника, повторы — счётчик.
    Без закрытого содержимого: только сводка, обезличенные детали и ссылка на последнее событие."""
    __tablename__ = "security_alerts"
    __table_args__ = (CheckConstraint("status IN ('new', 'acknowledged', 'resolved')", name="ck_security_alert_status"),
                      CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name="ck_security_alert_severity"),
                      Index("ix_security_alerts_status_seen", "status", "last_seen_at"),
                      Index("uq_security_alert_open", "rule_key", sa_text("COALESCE(subject_user_id, '')"), unique=True,
                            postgresql_where=sa_text("status <> 'resolved'")))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rule_key: Mapped[str] = mapped_column(String(48))
    rule_version: Mapped[str] = mapped_column(String(16), default="")
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="new")
    subject_user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(String(300), default="")
    details_sanitized: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_event_id: Mapped[Optional[str]] = mapped_column(String(36))
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(36))
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ack_comment: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[Optional[str]] = mapped_column(String(36))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_comment: Mapped[str] = mapped_column(Text, default="")


class AttachmentScan(Base):
    """Результат проверки файла (02 §6): при загрузке (route, uploaded_by) или проверкой хранилища (storage_path).
    Без содержимого файла; заражённый образец не хранится — только хэш, размер, имя сигнатуры (D-049)."""
    __tablename__ = "attachment_scans"
    __table_args__ = (CheckConstraint("status IN ('pending', 'clean', 'infected', 'error', 'legacy_unscanned')", name="ck_attachment_scan_status"),
                      Index("ix_attachment_scans_status_created", "status", "created_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attachment_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    quarantine_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: uuid.uuid4().hex)
    source: Mapped[str] = mapped_column(String(16), default="upload")      # upload | storage
    route: Mapped[str] = mapped_column(String(160), default="")
    storage_path: Mapped[str] = mapped_column(String(1024), default="")
    file_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    engine: Mapped[str] = mapped_column(String(16), default="clamav")
    engine_version: Mapped[str] = mapped_column(String(32), default="")
    signature_version: Mapped[str] = mapped_column(String(32), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    detected_name: Mapped[str] = mapped_column(String(120), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    last_error_code: Mapped[str] = mapped_column(String(32), default="")
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(36))
    scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AppMarker(Base):
    """Служебные отметки базы (04 §10.1): класс данных, с которым база запускалась. Проверяется при старте backend."""
    __tablename__ = "app_markers"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    app_env: Mapped[str] = mapped_column(String(32), default="")


class SupportNumberSequence(Base):
    """Счётчик номеров обращений по году (02 §7): берётся с блокировкой строки, без count+1."""
    __tablename__ = "support_number_sequences"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class SupportTicket(Base):
    """Обращение в поддержку внутри CRM (05, 02 §7). Только «в системе», без почты."""
    __tablename__ = "support_tickets"
    __table_args__ = (CheckConstraint("category IN ('error', 'question', 'access', 'improvement')", name="ck_support_category"),
                      CheckConstraint("requested_urgency IN ('normal', 'high', 'critical')", name="ck_support_urgency"),
                      CheckConstraint("priority IN ('low', 'normal', 'high', 'critical')", name="ck_support_priority"),
                      CheckConstraint("status IN ('new', 'in_progress', 'waiting_reporter', 'resolved', 'closed')", name="ck_support_status"),
                      UniqueConstraint("reporter_id", "idempotency_key", name="uq_support_idempotency"),
                      Index("ix_support_reporter_activity", "reporter_id", "last_activity_at"),
                      Index("ix_support_status_priority_activity", "status", "priority", "last_activity_at"),
                      Index("ix_support_assignee_status", "assignee_id", "status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(16), unique=True)
    reporter_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(16))
    requested_urgency: Mapped[str] = mapped_column(String(16), default="normal")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    current_route: Mapped[str] = mapped_column(String(300), default="")
    app_commit: Mapped[str] = mapped_column(String(40), default="")
    correlation_id: Mapped[str] = mapped_column(String(40), default="")
    client_metadata: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    reopened_from_id: Mapped[Optional[str]] = mapped_column(String(36))


class SupportMessage(Base):
    """Сообщение обращения: неизменяемое. kind=system пишет только сервер."""
    __tablename__ = "support_messages"
    __table_args__ = (CheckConstraint("kind IN ('user', 'specialist', 'system')", name="ck_support_message_kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportAttachment(Base):
    """Вложение обращения (D-051): своё хранение вне attachments — у тех deal_id обязателен, а доступ к файлам сделок идёт
    через can_see_deal. Здесь доступ только через обращение (автор или поддержка); файл принят только после проверки."""
    __tablename__ = "support_attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    message_id: Mapped[Optional[str]] = mapped_column(ForeignKey("support_messages.id"))
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    scan_status: Mapped[str] = mapped_column(String(20), default="legacy_unscanned")
    scan_id: Mapped[Optional[str]] = mapped_column(String(36))
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BriefingItemState(Base):
    """Отметки пользователя на элементах сводки (02 §9): закрепить, отложить, скрыть — для конкретной версии источника.
    Существенное изменение источника (срок, статус, остаток, исполнитель) даёт новую подпись — элемент возвращается."""
    __tablename__ = "briefing_item_states"
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_briefing_state_user_item"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(120))
    source_signature: Mapped[str] = mapped_column(String(64))
    pinned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AiConversation(Base):
    """Диалог помощника CRM (02 §8): доступен только владельцу; удаление мягкое, содержимое стирается по сроку хранения."""
    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_owner_last", "owner_id", "last_message_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(120), default="")
    default_mode: Mapped[str] = mapped_column(String(16), default="crm")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AiMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'assistant', 'system_notice')", name="ck_ai_message_role"),
                      CheckConstraint("status IN ('complete', 'fallback', 'error')", name="ck_ai_message_status"),
                      UniqueConstraint("conversation_id", "client_request_id", name="uq_ai_message_request"),
                      Index("ix_ai_messages_conversation_created", "conversation_id", "created_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("ai_conversations.id"))
    role: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16), default="crm")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="complete")
    provider: Mapped[str] = mapped_column(String(32), default="none")
    model: Mapped[str] = mapped_column(String(64), default="")
    sources: Mapped[list] = mapped_column(JSONType, default=list)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    facts: Mapped[list] = mapped_column(JSONType, default=list)
    request_id: Mapped[str] = mapped_column(String(40), default="")
    client_request_id: Mapped[Optional[str]] = mapped_column(String(64))
    reply_to_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AiToolAudit(Base):
    """Технический след вызова инструмента: обезличенные аргументы, число и ID результатов — без самих данных."""
    __tablename__ = "ai_tool_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(36))
    actor_user_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_name: Mapped[str] = mapped_column(String(48))
    arguments_sanitized: Mapped[dict] = mapped_column(JSONType, default=dict)
    result_status: Mapped[str] = mapped_column(String(16), default="ok")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_ids: Mapped[list] = mapped_column(JSONType, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiFeedback(Base):
    __tablename__ = "ai_feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_ai_feedback"),
                      CheckConstraint("rating IN ('positive', 'negative')", name="ck_ai_feedback_rating"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(ForeignKey("ai_messages.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiUsageDaily(Base):
    """Счётчик запросов за день и за текущую минуту — атомарно, одной строкой (лимиты проверяются до ответа)."""
    __tablename__ = "ai_usage_daily"
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    token_in: Mapped[int] = mapped_column(Integer, default=0)
    token_out: Mapped[int] = mapped_column(Integer, default=0)
    minute_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    minute_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AccessReviewCampaign(Base):
    """Пересмотр прав (docs/operations/04 §6, 02): ежеквартальная кампания по активным сотрудникам."""
    __tablename__ = "access_review_campaigns"
    __table_args__ = (CheckConstraint("status IN ('draft', 'open', 'completed', 'cancelled')", name="ck_access_review_campaign_status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)            # {"roles": [...]} или {} — все активные
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[Optional[str]] = mapped_column(String(36))
    completion_note: Mapped[str] = mapped_column(Text, default="")


class AccessReviewItem(Base):
    """Сотрудник в кампании: снимок прав на старте (без паролей, секретов TOTP и токенов) и решение."""
    __tablename__ = "access_review_items"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id", name="uq_access_review_item"),
                      CheckConstraint("decision IN ('pending', 'keep', 'change', 'disable', 'skipped')", name="ck_access_review_item_decision"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("access_review_campaigns.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    reviewer_id: Mapped[Optional[str]] = mapped_column(String(36))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[dict] = mapped_column(JSONType, default=dict)         # для change: фактическая разница прав на момент решения
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class BackupRun(Base):
    """Запуск резервного копирования на хосте (docs/operations/02 §4, D-024…D-027).

    Источник — host-side `last-run.json` и `runs/*.json` systemd-контура restic → Yandex Object Storage; таблица — история для UI.
    Только безопасные поля: ни ключей, ни пароля restic, ни путей с секретами, ни вывода команд."""
    __tablename__ = "backup_runs"
    __table_args__ = (Index("ix_backup_runs_status_started", "status", "started_at"),
                      CheckConstraint("reason IN ('daily', 'pre_deploy', 'manual', 'restore_check')", name="ck_backup_runs_reason"),
                      CheckConstraint("status IN ('running', 'success', 'failed')", name="ck_backup_runs_status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_key: Mapped[str] = mapped_column(String(36), unique=True)            # UUID, сгенерированный скриптом на хосте
    reason: Mapped[str] = mapped_column(String(16))                          # daily | pre_deploy | manual | restore_check
    status: Mapped[str] = mapped_column(String(16))                          # running | success | failed
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(16))           # короткий ID снимка restic
    git_commit: Mapped[Optional[str]] = mapped_column(String(40))
    schema_revision: Mapped[Optional[str]] = mapped_column(String(64))       # последний SQL change set в каталоге проекта
    storage_provider: Mapped[str] = mapped_column(String(16), default="unknown")   # local | s3 | unknown
    database_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    files_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    files_count: Mapped[Optional[int]] = mapped_column(Integer)
    manifest_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    check_status: Mapped[str] = mapped_column(String(16), default="")       # ok | failed | skipped
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retention_status: Mapped[str] = mapped_column(String(16), default="")   # ok | failed | skipped — ошибка prune не скрывается (03 §11)
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_summary: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntegrationLog(Base):
    """Журнал обменов с внешними системами (Graph, WeCom, ЦБ)."""
    __tablename__ = "integration_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    system: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


# ------------------------------------------------------------------ запчасти: компании группы, маршруты, наценки (ТЗ запчастей, раздел 2)

class LegalEntity(TimestampMixin, Base):
    """Компания группы: РФ (продаёт клиентам), Киргизия, Китай."""
    __tablename__ = "legal_entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(16), unique=True)            # ru | kg | cn — короткий код
    name: Mapped[str] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(3), default="RUB")       # валюта учёта компании
    warehouse: Mapped[str] = mapped_column(String(16), default="none")    # physical | virtual | none
    sells_to_clients: Mapped[bool] = mapped_column(Boolean, default=False)   # продаёт клиентам (компания в РФ)
    # каналы компании для запросов от её имени (D-151): {"mail_profile": "alliance", "mail_from": "...", "wecom_profile": "alliance"};
    # секреты — в переменных окружения профиля (MAIL_PROFILE_<P>_*, WECOM_PROFILE_<P>_*), не в БД
    channels: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    doc_prefix: Mapped[str] = mapped_column(String(8), default="", server_default="")   # код в номерах Канцелярии: РФ / КГ / КН (D-128)


class LegalProfile(TimestampMixin, Base):
    """Юридический профиль контрагента (юридический блок): привязан к клиенту, поставщику или компании группы (БЕРГ-ХАУС).
    У одного владельца может быть несколько профилей (головная организация, филиал, другое юрлицо группы); один — по умолчанию."""
    __tablename__ = "legal_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_kind: Mapped[str] = mapped_column(String(16), index=True)            # client | factory | own (компания группы)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="other")             # ru_customer | cn_factory | foreign_supplier | kg_company | intermediary | own | other
    title: Mapped[str] = mapped_column(String(255), default="")                # понятное название профиля («Головной офис», «Филиал Норильск»)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    country: Mapped[str] = mapped_column(String(64), default="RU")
    # наименования по языкам: {"ru": {"full","short"}, "en": {...}, "zh": {...}}
    names: Mapped[dict] = mapped_column(JSONType, default=dict)
    opf: Mapped[str] = mapped_column(String(128), default="")                  # организационно-правовая форма
    reg_number: Mapped[str] = mapped_column(String(64), default="")            # ОГРН / рег. номер / 统一社会信用代码
    tax_number: Mapped[str] = mapped_column(String(64), default="")            # ИНН / налоговый номер
    kpp: Mapped[str] = mapped_column(String(16), default="")
    legal_address: Mapped[str] = mapped_column(Text, default="")
    actual_address: Mapped[str] = mapped_column(Text, default="")
    postal_address: Mapped[str] = mapped_column(Text, default="")
    director: Mapped[str] = mapped_column(String(255), default="")             # руководитель / законный представитель
    signatory_name: Mapped[str] = mapped_column(String(255), default="")
    signatory_position: Mapped[str] = mapped_column(String(255), default="")
    signatory_basis: Mapped[str] = mapped_column(String(255), default="")      # Устава / доверенности № … от …
    contacts: Mapped[list[dict]] = mapped_column(JSONType, default=list)       # [{name, position, phone, email}]; у клиентов — только чтение (D-189)
    org_phone: Mapped[str] = mapped_column(String(64), default="")            # D-189: телефон и почта организации для договоров (не контактного лица)
    org_email: Mapped[str] = mapped_column(String(255), default="")
    documents: Mapped[list[dict]] = mapped_column(JSONType, default=list)      # регистрационные документы: [{title, number, issued_on, valid_until, attachment_id}]
    powers_of_attorney: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # [{person, number, issued_on, valid_until, scope, attachment_id}]
    extra: Mapped[dict] = mapped_column(JSONType, default=dict)                # страновые поля: business_license {...}, legal_representative, и т. п.
    check_status: Mapped[str] = mapped_column(String(16), default="not_checked")   # not_checked | checked | issues
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checked_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    lawyer_comment: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # [{at, by, fields: {name: [old, new]}}]

    banks = relationship("BankProfile", cascade="all, delete-orphan", order_by="BankProfile.created_at")


class BankProfile(TimestampMixin, Base):
    """Банковский профиль юридического профиля: несколько счетов (валюты, банки), статусы действующий/архивный, история изменений."""
    __tablename__ = "bank_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    profile_id: Mapped[str] = mapped_column(ForeignKey("legal_profiles.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")                # «Основной RUB», «Валютный USD»
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    country: Mapped[str] = mapped_column(String(64), default="")
    beneficiary: Mapped[str] = mapped_column(String(255), default="")          # получатель платежа (если отличается от наименования)
    bank_name: Mapped[str] = mapped_column(String(255), default="")
    bank_address: Mapped[str] = mapped_column(Text, default="")
    account: Mapped[str] = mapped_column(String(64), default="")
    bik: Mapped[str] = mapped_column(String(16), default="")
    swift: Mapped[str] = mapped_column(String(16), default="")
    corr_account: Mapped[str] = mapped_column(String(64), default="")
    corr_bank: Mapped[str] = mapped_column(String(255), default="")           # банк-корреспондент (для валютных платежей)
    purpose: Mapped[str] = mapped_column(String(255), default="")              # назначение: расчёты с поставщиками КНР, с клиентами РФ …
    status: Mapped[str] = mapped_column(String(16), default="active")          # active | archived
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)             # реквизиты проверены (ФД / юрист)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # [{at, by, action, fields: {name: [old, new]}}]


class LegalZipSet(TimestampMixin, Base):
    """Договорный комплект ЗИП по сделке и поставщику (юридический блок, этап 3): что из сервисного / RFQ-ЗИП идёт в приложение № 2.
    Хранится отдельно от сервисного расчёта — правки юриста/менеджера не меняют себестоимость."""
    __tablename__ = "legal_zip_sets"
    __table_args__ = (UniqueConstraint("deal_id", "factory_id", name="uq_legal_zip_set"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), index=True)
    # [{id, position_id, article, name, name_zh, assembly, unit, qty, unit_price, currency, interval_hours, kind, lead_days, included, selected, source, comment}]
    lines: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    pricing_mode: Mapped[str] = mapped_column(String(16), default="included")   # included — в стоимости оборудования | separate — оплачивается отдельно
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)


class LegalPackage(TimestampMixin, Base):
    """Пакет юридических документов по сделке (юридический блок): закупка у поставщика (договор + 2 приложения) или продажа заказчику
    (рабочая копия договора заказчика + протокол разногласий + техспецификация). Версионируется; хранит снимки всех исходных данных."""
    __tablename__ = "legal_packages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))                               # purchase | sale
    counterparty_kind: Mapped[str] = mapped_column(String(16))                  # factory | client
    counterparty_id: Mapped[str] = mapped_column(String(36), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    deal_version_id: Mapped[Optional[str]] = mapped_column(String(36))
    deal_version_no: Mapped[Optional[int]] = mapped_column(Integer)
    own_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))   # компания группы (покупатель в закупке / продавец в продаже)
    own_profile_id: Mapped[Optional[str]] = mapped_column(String(36))
    own_bank_id: Mapped[Optional[str]] = mapped_column(String(36))
    cp_profile_id: Mapped[Optional[str]] = mapped_column(String(36))             # профиль контрагента
    cp_bank_id: Mapped[Optional[str]] = mapped_column(String(36))
    language: Mapped[str] = mapped_column(String(16), default="ru")
    templates: Mapped[dict] = mapped_column(JSONType, default=dict)             # {doc_kind: {"id", "version", "title"} | None (встроенный генератор)}
    rules_version: Mapped[str] = mapped_column(String(32), default="")
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)              # все данные, подставленные в документы (стороны, банки, позиции, ЗИП, итоги, договор, источники)
    checks: Mapped[dict] = mapped_column(JSONType, default=dict)                # {"errors": [...], "warnings": [...]}
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)           # [{kind, title, file_name, attachment_id, size, format}]
    status: Mapped[str] = mapped_column(String(32), default="draft")            # needs_data | draft | lawyer_review | commercial_review | approved | signed | superseded | archived
    stage_code: Mapped[str] = mapped_column(String(32), default="")             # этап исполнения, к которому привязан пакет (factory_contract | contract_signed)
    approvals: Mapped[list[dict]] = mapped_column(JSONType, default=list)       # [{at, by, from, to, comment}]
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    superseded_by: Mapped[Optional[str]] = mapped_column(String(36))


class Contract(TimestampMixin, Base):
    """Договор компании группы (юридический блок): с клиентом, поставщиком/заводом, подрядчиком и прочие.
    Единственный источник условий оплаты, сроков исполнения и неустоек для денежного потока и претензий.
    Доп. соглашения и спецификации — записи с parent_id."""
    __tablename__ = "contracts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)                  # доп. соглашение / спецификация к договору
    doc_type: Mapped[str] = mapped_column(String(16), default="contract")                     # contract | amendment | specification
    kind: Mapped[str] = mapped_column(String(32), default="other")                            # client_supply | client_parts | client_service | supplier | logistics | customs | works | …
    direction: Mapped[str] = mapped_column(String(8), default="none")                         # income | expense | none
    number: Mapped[str] = mapped_column(String(64), default="", index=True)                   # внутренний номер: К-RU-2026-001
    seq: Mapped[Optional[int]] = mapped_column(Integer)
    cp_number: Mapped[str] = mapped_column(String(128), default="")                           # номер у контрагента
    title: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    own_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    own_profile_id: Mapped[Optional[str]] = mapped_column(String(36))
    own_bank_id: Mapped[Optional[str]] = mapped_column(String(36))
    signatory: Mapped[dict] = mapped_column(JSONType, default=dict)                           # {name, position, basis, poa_id}
    cp_kind: Mapped[str] = mapped_column(String(16), default="other")                         # client | factory | other
    cp_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    cp_name: Mapped[str] = mapped_column(String(255), default="")
    cp_inn: Mapped[str] = mapped_column(String(32), default="")
    cp_profile_id: Mapped[Optional[str]] = mapped_column(String(36))
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)                    # основная сделка
    deal_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)                       # рамочный — несколько сделок
    package_id: Mapped[Optional[str]] = mapped_column(String(36))                             # юридический пакет, из которого создан
    amount: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    vat_mode: Mapped[str] = mapped_column(String(16), default="included")                     # included | on_top | none
    vat_rate: Mapped[Optional[float]] = mapped_column(Float)
    price_type: Mapped[str] = mapped_column(String(16), default="fixed")                      # fixed | by_spec | framework
    fx_clause: Mapped[str] = mapped_column(Text, default="")
    signed_on: Mapped[Optional[date]] = mapped_column(Date)
    effective_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_terms: Mapped[list[dict]] = mapped_column(JSONType, default=list)                 # [{no, share_pct|amount, event, offset_days, offset_kind, via, event_on, day_of_month, note}]
    performance_terms: Mapped[list[dict]] = mapped_column(JSONType, default=list)             # сроки исполнения: [{code, title, event, offset_days, offset_kind, fixed_on, note}]
    penalties: Mapped[list[dict]] = mapped_column(JSONType, default=list)                     # [{payer, reason, rate_kind, rate, base, grace_days, cap_kind, cap_value, term, note}]
    liability_cap_pct: Mapped[Optional[float]] = mapped_column(Float)                         # общий предел ответственности, % цены
    security: Mapped[dict] = mapped_column(JSONType, default=dict)                            # {kind: bank_guarantee|lc|retention, amount, currency, valid_to, bank, note}
    warranty: Mapped[dict] = mapped_column(JSONType, default=dict)                            # {months, hours, note}
    law: Mapped[dict] = mapped_column(JSONType, default=dict)                                 # {law, court, claim_days, pre_trial}
    sign_method: Mapped[str] = mapped_column(String(8), default="paper")                      # paper | edo
    edo: Mapped[dict] = mapped_column(JSONType, default=dict)                                 # {operator, doc_id, signed_at}
    original: Mapped[dict] = mapped_column(JSONType, default=dict)                            # {received_on, by, location}
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)                         # [{id, kind, name, size, version, uploaded_by, uploaded_at, note}]
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    curator_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    lawyer_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(16), default="manual")                         # manual | package | deal_import
    mirror_kind: Mapped[str] = mapped_column(String(16), default="")                          # via_group — договор компании группы с конечным поставщиком (D-160)
    is_framework: Mapped[bool] = mapped_column(Boolean, default=False)                        # рамочный: сделки оформляются спецификациями к нему; amount — лимит (D-168)
    client_order: Mapped[bool] = mapped_column(Boolean, default=False)                        # закупка под заказ клиента (ГШО / запчасти) — в маршруте КД
    approval: Mapped[dict] = mapped_column(JSONType, default=dict)                            # маршрут: {round, submitted_at, submitted_by, steps: [{code, title, decision, by, at, comment}]}
    notified: Mapped[list[str]] = mapped_column(JSONType, default=list)                       # отправленные напоминания: expiring_30, expiring_7, expired
    facts: Mapped[dict] = mapped_column(JSONType, default=dict)                               # исполнение: {pay: {no: {paid_on, amount}}, perf: {i: {done_on}}, pen: {key: {status, amount, on, comment}}}


class ContractClaim(TimestampMixin, Base):
    """Претензия по договору: исходящая (требуем с контрагента долг, исполнение, неустойку) или входящая (требования к нам).
    Лестница: напоминание за 3 дня до срока → уведомление в 1-й день просрочки → черновик претензии на 5-й день; подписывает КД или ЗГД."""
    __tablename__ = "contract_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    contract_id: Mapped[str] = mapped_column(String(36), index=True)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(8), default="outgoing")                      # outgoing | incoming
    number: Mapped[str] = mapped_column(String(64), default="", index=True)                    # ПР-RU-2026-001 / входящий номер
    seq: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")                           # draft | signing | signed | sent | received | answered | court | closed | cancelled
    lines: Mapped[list[dict]] = mapped_column(JSONType, default=list)                          # [{kind: debt|performance|penalty, key, title, amount, days, due_on, penalty_key, rate_text}]
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    demand: Mapped[str] = mapped_column(Text, default="")                                      # дополнительное требование / текст
    answer_days: Mapped[int] = mapped_column(Integer, default=30)
    issued_on: Mapped[Optional[date]] = mapped_column(Date)
    signer_id: Mapped[Optional[str]] = mapped_column(String(36))
    signer_role: Mapped[str] = mapped_column(String(8), default="")                            # cd | gd
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_on: Mapped[Optional[date]] = mapped_column(Date)
    sent_via: Mapped[str] = mapped_column(String(16), default="")                              # post | courier | email | edo
    track: Mapped[str] = mapped_column(String(128), default="")
    answer_due: Mapped[Optional[date]] = mapped_column(Date, index=True)
    result: Mapped[str] = mapped_column(String(16), default="")                                # satisfied | partial | rejected | no_answer
    result_amount: Mapped[Optional[float]] = mapped_column(Float)
    answer_on: Mapped[Optional[date]] = mapped_column(Date)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    notified: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source: Mapped[str] = mapped_column(String(8), default="manual")                           # manual | auto
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


class PowerOfAttorney(TimestampMixin, Base):
    """Доверенность от компании группы (юридический блок): конструктор, реестр, печать, копии, статусы, отзыв.
    Персональные данные представителя (паспорт) видят только юрист, ЗГД, ФД и администратор."""
    __tablename__ = "powers_of_attorney"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"), index=True)       # доверитель — компания группы
    profile_id: Mapped[Optional[str]] = mapped_column(String(36))                             # юридический профиль доверителя (снимок в principal)
    number: Mapped[str] = mapped_column(String(64), default="")                               # присваивается при оформлении: Д-<код>-<год>-<NNN>
    seq: Mapped[Optional[int]] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="special")                          # general | special | single
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)              # draft | active | expired | revoked | cancelled
    issued_on: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    place: Mapped[str] = mapped_column(String(255), default="")
    principal: Mapped[dict] = mapped_column(JSONType, default=dict)                           # снимок доверителя: name, inn, kpp, ogrn, address, signatory, signatory_position, signatory_basis
    attorney_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))           # представитель — сотрудник (если из списка)
    attorney: Mapped[dict] = mapped_column(JSONType, default=dict)                            # full_name, position, birth_date, birth_place, passport {series, number, issued_by, issued_on, code}, address, phone, email
    powers: Mapped[list[dict]] = mapped_column(JSONType, default=list)                        # [{code, text}] — из каталога или свои
    limits: Mapped[dict] = mapped_column(JSONType, default=dict)                              # amount_limit, currency, counterparties (текст/список), territory, substitution (bool), extra
    documents_text: Mapped[str] = mapped_column(Text, default="")                             # итоговый текст (для шаблона и поиска)
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)                         # [{id, kind: draft|issued|signed_scan|revocation|other, name, size, uploaded_by, uploaded_at}]
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)                       # [{at, by, action, comment}]
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    revoke_reason: Mapped[str] = mapped_column(Text, default="")
    notified: Mapped[list[str]] = mapped_column(JSONType, default=list)                       # отправленные напоминания: expiring_30, expiring_7, expired
    facts: Mapped[dict] = mapped_column(JSONType, default=dict)                               # исполнение: {pay: {no: {paid_on, amount}}, perf: {i: {done_on}}, pen: {key: {status, amount, on, comment}}}
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class SupplyRoute(TimestampMixin, Base):
    """Маршрут поставки: цепочка продаж от поставщика до компании, продающей клиенту."""
    __tablename__ = "supply_routes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    supplier_country: Mapped[str] = mapped_column(String(64), default="")    # подсказка при выборе маршрута: CN | DE | KG
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    steps: Mapped[list["RouteStep"]] = relationship(back_populates="route", cascade="all, delete-orphan", order_by="RouteStep.no")


class RouteStep(Base):
    """Ступень маршрута: кто кому продаёт, на каких условиях и в какой валюте."""
    __tablename__ = "route_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    route_id: Mapped[str] = mapped_column(ForeignKey("supply_routes.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    seller_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))   # NULL — поставщик
    buyer_entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"))
    incoterms: Mapped[str] = mapped_column(String(8), default="")
    place: Mapped[str] = mapped_column(String(255), default="")           # место по Incoterms: склад экспедитора в КНР, склад РФ…
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    customs_country: Mapped[str] = mapped_column(String(64), default="")   # где выпуск в свободное обращение на этой ступени: RU | KG
    pay_days: Mapped[Optional[int]] = mapped_column(Integer)              # отсрочка оплаты, дн
    transit_days: Mapped[Optional[int]] = mapped_column(Integer)          # типовой срок перевозки, дн
    comment: Mapped[str] = mapped_column(Text, default="")
    route: Mapped["SupplyRoute"] = relationship(back_populates="steps")


class MarkupPolicy(Base):
    """Версия политики наценок между компаниями группы. Вступает в силу после двух виз: ЗГД и ФД."""
    __tablename__ = "markup_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")      # draft | on_approval | approved | rejected | superseded
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[dict] = mapped_column(JSONType, default=dict)           # см. services/markup_chain.py
    approvals: Mapped[dict] = mapped_column(JSONType, default=dict)       # {"gd": {user_id, name, at, comment}, "fd": {...}}
    rejection: Mapped[dict] = mapped_column(JSONType, default=dict)       # {user_id, name, at, comment}
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ запчасти: каталог (карточка утверждена 05.11.2026, ТЗ запчастей, раздел 4)

class Warehouse(TimestampMixin, Base):
    """Склад: физический (РФ) или виртуальный (Киргизия). Имя — как в МойСклад, для сверки и переноса остатков."""
    __tablename__ = "warehouses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    legal_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))
    kind: Mapped[str] = mapped_column(String(16), default="physical")    # physical | virtual
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Part(TimestampMixin, Base):
    """Карточка запасной части. Артикул может повторяться у разных производителей — дубли показываются на разбор."""
    __tablename__ = "parts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # основное
    article: Mapped[str] = mapped_column(String(128), index=True)          # основной номер *
    name_ru: Mapped[str] = mapped_column(String(512))                     # *
    unit: Mapped[str] = mapped_column(String(16), default="шт")           # *
    part_group: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")     # active | archive
    names: Mapped[dict] = mapped_column(JSONType, default=dict)           # {"en": …, "cn": …, "de": …}
    description: Mapped[str] = mapped_column(Text, default="")
    # номера
    manufacturer: Mapped[str] = mapped_column(String(255), default="")   # производитель
    oem_number: Mapped[str] = mapped_column(String(128), default="", index=True)   # артикул производителя (OEM)
    # закупка
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"))   # основной поставщик
    route_id: Mapped[Optional[str]] = mapped_column(ForeignKey("supply_routes.id"))  # маршрут по умолчанию
    purchase_price: Mapped[Optional[float]] = mapped_column(Float)        # последняя закупочная цена
    purchase_currency: Mapped[str] = mapped_column(String(3), default="")
    purchase_on: Mapped[Optional[date]] = mapped_column(Date)
    lead_days: Mapped[Optional[int]] = mapped_column(Integer)            # срок поставки поставщика
    hs_code: Mapped[str] = mapped_column(String(14), default="")
    origin_country: Mapped[str] = mapped_column(String(64), default="")
    moq: Mapped[Optional[float]] = mapped_column(Float)                  # минимальная партия
    multiple: Mapped[Optional[float]] = mapped_column(Float)             # кратность
    # логистика
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    length_mm: Mapped[Optional[float]] = mapped_column(Float)
    width_mm: Mapped[Optional[float]] = mapped_column(Float)
    height_mm: Mapped[Optional[float]] = mapped_column(Float)
    volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    # цены (себестоимость рассчитывается модулем и в карточке не хранится)
    sale_price: Mapped[Optional[float]] = mapped_column(Float)           # цена продажи (прайс)
    sale_currency: Mapped[str] = mapped_column(String(3), default="")
    min_price: Mapped[Optional[float]] = mapped_column(Float)
    min_price_currency: Mapped[str] = mapped_column(String(3), default="")
    # файлы и сделки
    photos: Mapped[list[str]] = mapped_column(JSONType, default=list)    # ссылки / пути к фото
    photo_files: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # файлы фото в хранилище: [{path, name, ms_image_id, size}]
    spec_no: Mapped[str] = mapped_column(String(128), default="")        # спецификация, №
    addendum_no: Mapped[str] = mapped_column(String(128), default="")    # доп. соглашение, №
    # перенос из МойСклад
    ms_id: Mapped[Optional[str]] = mapped_column(String(36), unique=True)   # UUID МойСклад
    ms_code: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")    # manual | moysklad | excel
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    cross_refs: Mapped[list["PartCrossRef"]] = relationship(cascade="all, delete-orphan", order_by="PartCrossRef.kind",
                                                           foreign_keys="PartCrossRef.part_id")
    applicability: Mapped[list["PartApplicability"]] = relationship(cascade="all, delete-orphan")
    prices: Mapped[list["PartClientPrice"]] = relationship(cascade="all, delete-orphan")
    stock: Mapped[list["PartStock"]] = relationship(cascade="all, delete-orphan")


class PartCrossRef(Base):
    """Номера поставщиков и аналоги."""
    __tablename__ = "part_cross_refs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))           # supplier | analog_full | analog_rework
    number: Mapped[str] = mapped_column(String(128), index=True)
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"))   # для номера поставщика
    analog_part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id"))   # аналог, если он есть в каталоге
    manufacturer: Mapped[str] = mapped_column(String(255), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    # D-202: кроссировки «номер завода машины ↔ OEM» — статус проверки, основание, источник (ручной ввод, импорт, ИИ)
    status: Mapped[str] = mapped_column(String(16), default="unverified")   # те же статусы, что у применимости
    basis_kind: Mapped[str] = mapped_column(String(16), default="")
    basis: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")       # manual | import | ai
    source_ref: Mapped[str] = mapped_column(String(255), default="")       # файл, документ, письмо
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PartApplicability(Base):
    """Применимость: модель из каталога ГШО или модель другого производителя (текстом), узел / агрегат."""
    __tablename__ = "part_applicability"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    equipment_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id"))
    other_manufacturer: Mapped[str] = mapped_column(String(255), default="")
    other_model: Mapped[str] = mapped_column(String(255), default="")
    assembly: Mapped[str] = mapped_column(String(255), default="")      # узел / агрегат
    # J-b (D-196): статус проверки, уровень исполнения, диапазон серийных номеров, основание, кто и когда проверил.
    # Прежние записи — «не подтверждено» (решение владельца 7, D-180).
    execution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_executions.id", ondelete="SET NULL"))
    assembly_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_assemblies.id", ondelete="SET NULL"))   # J-c (D-200)
    status: Mapped[str] = mapped_column(String(16), default="unverified")   # confirmed | probable | unverified | not_applicable | replaced
    serial_from: Mapped[str] = mapped_column(String(64), default="")
    serial_to: Mapped[str] = mapped_column(String(64), default="")
    basis_kind: Mapped[str] = mapped_column(String(16), default="")         # manual | supplier | service | analog | comment
    basis: Mapped[str] = mapped_column(Text, default="")
    verified_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PartClientPrice(Base):
    """Договорная цена клиента (прайс-лист клиента в МойСклад — колонка «Цена: <клиент>»)."""
    __tablename__ = "part_client_prices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    client_name: Mapped[str] = mapped_column(String(255), default="")    # как в источнике — пока клиент не сопоставлен
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)


class PartStock(Base):
    """Остаток, резерв и транзит по складу (до полного складского учёта — снимок из переноса) и минимальный остаток."""
    __tablename__ = "part_stock"
    __table_args__ = (UniqueConstraint("part_id", "warehouse_id", name="uq_part_stock"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id", ondelete="CASCADE"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    qty: Mapped[float] = mapped_column(Float, default=0)
    reserved: Mapped[float] = mapped_column(Float, default=0)
    in_transit: Mapped[float] = mapped_column(Float, default=0)
    min_qty: Mapped[Optional[float]] = mapped_column(Float)
    cell: Mapped[str] = mapped_column(String(32), default="")           # ячейка хранения (последняя при приёмке)
    as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ запчасти: заявки клиентов (ТЗ запчастей, раздел 5, попозиционный учёт)

class PartRequest(TimestampMixin, Base):
    """Заявка клиента на запчасти. Статус заявки собирается из статусов строк."""
    __tablename__ = "part_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)          # ЗЧ-2026-0001
    machine_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)   # машина клиента из парка (подбор по применимости, ТО)
    sla_notified: Mapped[list[str]] = mapped_column(JSONType, default=list)   # отправленные напоминания о сроках: kp
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    manager_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    contact: Mapped[str] = mapped_column(String(255), default="")
    channel: Mapped[str] = mapped_column(String(16), default="email")    # email | wecom | phone | excel | other
    machine: Mapped[str] = mapped_column(String(255), default="")        # машина клиента (текстом, до парка машин)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)         # «машина стоит»
    warranty: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    closed: Mapped[str] = mapped_column(String(16), default="")          # "" | lost | cancelled | done
    close_reason: Mapped[str] = mapped_column(Text, default="")
    approvals: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # [{role, user_id, name, at, comment, lines: [...], level}]
    chain_approval: Mapped[dict] = mapped_column(JSONType, default=dict)   # виза ФД/ЗГД на внутреннюю цепочку до КП (D-166)
    lines: Mapped[list["PartRequestLine"]] = relationship(cascade="all, delete-orphan", order_by="PartRequestLine.no")


class PartRequestLine(TimestampMixin, Base):
    """Строка заявки — единица учёта: свой подбор, источник, поставщик, срок, цена и статус."""
    __tablename__ = "part_request_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("part_requests.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    parent_line_id: Mapped[Optional[str]] = mapped_column(ForeignKey("part_request_lines.id"))   # часть разделённой строки
    raw_article: Mapped[str] = mapped_column(String(128), default="")    # как написал клиент
    raw_name: Mapped[str] = mapped_column(String(512), default="")
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id"))
    match_kind: Mapped[str] = mapped_column(String(16), default="")      # article | oem | supplier | analog | manual | ""
    source: Mapped[str] = mapped_column(String(16), default="")          # stock | order
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"))
    route_id: Mapped[Optional[str]] = mapped_column(ForeignKey("supply_routes.id"))
    lead_days: Mapped[Optional[int]] = mapped_column(Integer)            # срок поставки клиенту, дн
    price: Mapped[Optional[float]] = mapped_column(Float)                # цена клиенту за единицу
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    price_source: Mapped[str] = mapped_column(String(16), default="")    # contract | pricelist | calc | manual
    calc: Mapped[dict] = mapped_column(JSONType, default=dict)          # снимок расчёта: себестоимость, порог, маржа (видят КД, ФД, ЗГД)
    status: Mapped[str] = mapped_column(String(24), default="new")
    comment: Mapped[str] = mapped_column(Text, default="")
    # выбранный ответ поставщика (заказ у поставщиков): цена поставщика по этой строке вместо последней закупочной
    rfq_line_id: Mapped[Optional[str]] = mapped_column(String(36))
    quote_price: Mapped[Optional[float]] = mapped_column(Float)
    quote_currency: Mapped[str] = mapped_column(String(3), default="")
    eta: Mapped[Optional[date]] = mapped_column(Date)                    # ожидаемая дата на складе РФ — из заказа поставщику
    shipped_qty: Mapped[float] = mapped_column(Float, default=0)         # отгружено клиенту


class PartKp(Base):
    """Редакция КП по заявке на запчасти: снимок выбранных строк, цены, срок действия. КП можно отправлять частями."""
    __tablename__ = "part_kps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("part_requests.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    number: Mapped[str] = mapped_column(String(48))                      # КП-ЗЧ-2026-0001/1
    currency: Mapped[str] = mapped_column(String(3), default="RUB")      # валюта КП
    fx_rate: Mapped[Optional[float]] = mapped_column(Float)             # курс ЦБ на дату КП (для валюты КП)
    lines: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # снимок строк: line_id, артикул, наименование, кол-во, цена, сумма, срок
    handling_fee: Mapped[float] = mapped_column(Float, default=0)       # «Обработка заказа» в валюте КП
    handling_fee_removed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    total: Mapped[float] = mapped_column(Float, default=0)
    valid_until: Mapped[date] = mapped_column(Date)
    changes: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # что изменилось к прошлой редакции
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class PartReservation(Base):
    """Резерв товара под строку заявки: мягкий — при КП, на срок КП; жёсткий — после заказа клиента."""
    __tablename__ = "part_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("part_request_lines.id", ondelete="CASCADE"), index=True)
    kp_id: Mapped[Optional[str]] = mapped_column(ForeignKey("part_kps.id", ondelete="SET NULL"))
    qty: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(8), default="soft")         # soft | hard
    expires_at: Mapped[Optional[date]] = mapped_column(Date)            # мягкий — до конца срока КП
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ------------------------------------------------------------------ запчасти: запросы цен поставщикам (ТЗ запчастей, раздел 7)

class SupplierRfq(TimestampMixin, Base):
    """Запрос цены одному поставщику по строкам одной или нескольких заявок. Язык — по поставщику (Китай — CN, Германия — DE…)."""
    __tablename__ = "supplier_rfqs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)          # ЗП-2026-0001
    sla_notified: Mapped[list[str]] = mapped_column(JSONType, default=list)   # overdue — напоминание о просроченном ответе
    supplier_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), index=True)
    language: Mapped[str] = mapped_column(String(4), default="ru")        # ru | zh | de | en
    status: Mapped[str] = mapped_column(String(16), default="draft")      # draft | sent | partial | answered | closed
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    send_log: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # [{at, via, to, ok, error}]
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    # зеркальный запрос через компанию группы (D-156): U — предложение компании группы (supplier_id = она, end_supplier_id = X),
    # L — её запрос поставщику X (mirror_of_id = U); строки U считаются из ответа X по маршруту и наценкам группы
    mirror_of_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    end_supplier_id: Mapped[Optional[str]] = mapped_column(String(36))
    route_id: Mapped[Optional[str]] = mapped_column(String(36))
    mirror_state: Mapped[str] = mapped_column(String(24), default="")    # waiting | ok | no_policy | error | changed_after_select
    chain: Mapped[dict] = mapped_column(JSONType, default=dict)          # политика, курсы, маршрут на момент расчёта
    lines: Mapped[list["SupplierRfqLine"]] = relationship(cascade="all, delete-orphan", order_by="SupplierRfqLine.no")


class SupplierRfqLine(Base):
    """Строка запроса поставщику и его ответ по ней: цена, срок, наличие, минимальная партия или аналог."""
    __tablename__ = "supplier_rfq_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rfq_id: Mapped[str] = mapped_column(ForeignKey("supplier_rfqs.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    request_line_id: Mapped[Optional[str]] = mapped_column(ForeignKey("part_request_lines.id", ondelete="SET NULL"), index=True)
    part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id"))
    number: Mapped[str] = mapped_column(String(128), default="")          # номер для поставщика: его артикул, OEM или наш артикул
    name_ru: Mapped[str] = mapped_column(String(512), default="")
    name_foreign: Mapped[str] = mapped_column(String(512), default="")
    machine_translated: Mapped[bool] = mapped_column(Boolean, default=False)
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    # ответ
    availability: Mapped[str] = mapped_column(String(16), default="")     # "" (нет ответа) | available | on_order | none | analog
    price: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="")
    lead_days: Mapped[Optional[int]] = mapped_column(Integer)
    moq: Mapped[Optional[float]] = mapped_column(Float)
    analog_number: Mapped[str] = mapped_column(String(128), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    chain: Mapped[dict] = mapped_column(JSONType, default=dict)          # строка U: цена поставщика, ступени, маржа группы, ожидающее изменение


class PurchaseOrder(TimestampMixin, Base):
    """Заказ поставщику (external) или внутригрупповой заказ на следующей ступени маршрута (intragroup, parent_po_id — внешний заказ)."""
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ЗК-2026-0001, внутригрупповые — ЗК-2026-0001/2
    sla_notified: Mapped[list[str]] = mapped_column(JSONType, default=list)   # confirm — нет подтверждения поставщика в срок
    kind: Mapped[str] = mapped_column(String(16), default="external")                # external | intragroup
    parent_po_id: Mapped[Optional[str]] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    step_no: Mapped[int] = mapped_column(Integer, default=1)
    route_id: Mapped[Optional[str]] = mapped_column(ForeignKey("supply_routes.id"))
    buyer_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))
    seller_entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))   # внутригрупповой: продавец — компания группы
    supplier_id: Mapped[Optional[str]] = mapped_column(ForeignKey("factories.id"), index=True)  # внешний: поставщик
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    incoterms: Mapped[str] = mapped_column(String(8), default="")
    language: Mapped[str] = mapped_column(String(4), default="ru")
    status: Mapped[str] = mapped_column(String(16), default="draft")                 # draft | sent | confirmed | in_progress | closed | cancelled
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    send_log: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    supplier_ref: Mapped[str] = mapped_column(String(64), default="")                # номер подтверждения / счёта поставщика
    planned_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    actual_ship_date: Mapped[Optional[date]] = mapped_column(Date)
    payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)             # график оплат: [{due, amount, comment, paid_on}]
    total: Mapped[float] = mapped_column(Float, default=0)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    lines: Mapped[list["PoLine"]] = relationship(cascade="all, delete-orphan", order_by="PoLine.no")


class PoLine(Base):
    """Строка заказа: под строку заявки клиента или на пополнение склада. Статус поставки ведётся по строке внешнего заказа."""
    __tablename__ = "po_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    no: Mapped[int] = mapped_column(Integer)
    request_line_id: Mapped[Optional[str]] = mapped_column(ForeignKey("part_request_lines.id", ondelete="SET NULL"), index=True)
    parent_line_id: Mapped[Optional[str]] = mapped_column(String(36))                 # строка внешнего заказа (для внутригрупповых)
    part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id"))
    number: Mapped[str] = mapped_column(String(128), default="")                     # номер у поставщика
    name: Mapped[str] = mapped_column(String(512), default="")
    name_foreign: Mapped[str] = mapped_column(String(512), default="")
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    price: Mapped[float] = mapped_column(Float, default=0)
    lead_days: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ordered")
    received_qty: Mapped[float] = mapped_column(Float, default=0)
    eta: Mapped[Optional[date]] = mapped_column(Date)                                # плановая дата на складе РФ
    overdue_eta: Mapped[Optional[date]] = mapped_column(Date)                         # по какой дате уже было уведомление о просрочке
    warranty_claim_id: Mapped[Optional[str]] = mapped_column(String(36))              # гарантийная замена по претензии (цена 0)
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)           # сборная партия у экспедитора
    extra_cost_rub: Mapped[float] = mapped_column(Float, default=0)                   # доля расходов партии на строку, ₽
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)              # [{at, status, eta, by}]
    comment: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ запчасти: склад — приёмка, отклонения, отгрузка, движения (ТЗ запчастей, разделы 7–8)

class PartStockMove(Base):
    """Движение остатка: приёмка (+), отгрузка (−), корректировка. Остаток PartStock.qty — сумма движений поверх перенесённого снимка."""
    __tablename__ = "part_stock_moves"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"), index=True)
    qty: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16))                                    # receipt | shipment | adjust
    doc_number: Mapped[str] = mapped_column(String(32), default="")
    receipt_line_id: Mapped[Optional[str]] = mapped_column(String(36))
    shipment_line_id: Mapped[Optional[str]] = mapped_column(String(36))
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    onec_status: Mapped[str] = mapped_column(String(16), default="pending")          # pending | sent | error — выгрузка в 1С


class PartReceipt(TimestampMixin, Base):
    """Приёмка по заказу поставщику на физический склад."""
    __tablename__ = "part_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ПР-2026-0001
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    received_on: Mapped[date] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    lines: Mapped[list["PartReceiptLine"]] = relationship(cascade="all, delete-orphan")


class PartReceiptLine(Base):
    __tablename__ = "part_receipt_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("part_receipts.id", ondelete="CASCADE"), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey("po_lines.id"), index=True)
    part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id"))
    qty_expected: Mapped[float] = mapped_column(Float, default=0)
    qty: Mapped[float] = mapped_column(Float, default=0)                             # принято годного
    qty_bad: Mapped[float] = mapped_column(Float, default=0)                         # брак / пересорт — не в остаток
    cell: Mapped[str] = mapped_column(String(32), default="")
    discrepancy: Mapped[str] = mapped_column(String(16), default="")                 # "" | short | over | wrong | damaged
    reserved_for_line_id: Mapped[Optional[str]] = mapped_column(String(36))
    comment: Mapped[str] = mapped_column(Text, default="")


class PartDeviation(TimestampMixin, Base):
    """Отклонение поставки: недопоставка, излишек, пересорт, брак — решение и претензия поставщику."""
    __tablename__ = "part_deviations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ОТ-2026-0001
    po_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey("po_lines.id"), index=True)
    receipt_line_id: Mapped[Optional[str]] = mapped_column(ForeignKey("part_receipt_lines.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))                                    # short | over | wrong | damaged
    qty: Mapped[float] = mapped_column(Float, default=0)
    decision: Mapped[str] = mapped_column(String(16), default="")                    # "" | wait | replace | cancel | accept
    status: Mapped[str] = mapped_column(String(16), default="open")                  # open | claim | closed
    claim_text: Mapped[str] = mapped_column(Text, default="")
    claim_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    photos: Mapped[list[dict]] = mapped_column(JSONType, default=list)               # [{path, name, at}]
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PartShipment(TimestampMixin, Base):
    """Отгрузка клиенту по заявке, частичная допускается. Документ уходит в 1С."""
    __tablename__ = "part_shipments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ОГ-2026-0001
    request_id: Mapped[str] = mapped_column(ForeignKey("part_requests.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    shipped_on: Mapped[date] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    onec_status: Mapped[str] = mapped_column(String(16), default="pending")
    lines: Mapped[list["PartShipmentLine"]] = relationship(cascade="all, delete-orphan")


class PartShipmentLine(Base):
    __tablename__ = "part_shipment_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    shipment_id: Mapped[str] = mapped_column(ForeignKey("part_shipments.id", ondelete="CASCADE"), index=True)
    request_line_id: Mapped[str] = mapped_column(ForeignKey("part_request_lines.id"), index=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    qty: Mapped[float] = mapped_column(Float)


class PartInventory(TimestampMixin, Base):
    """Инвентаризация склада: задание (склад, ячейки, группа товара), подсчёт с телефона, утверждение КД → корректировка остатков."""
    __tablename__ = "part_inventories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ИН-2026-0001
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("warehouses.id"))
    cell_prefix: Mapped[str] = mapped_column(String(32), default="")
    part_group: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="counting")              # counting | submitted | approved | cancelled
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    submitted_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lines: Mapped[list["PartInventoryLine"]] = relationship(cascade="all, delete-orphan")


class PartInventoryLine(Base):
    __tablename__ = "part_inventory_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    inventory_id: Mapped[str] = mapped_column(ForeignKey("part_inventories.id", ondelete="CASCADE"), index=True)
    part_id: Mapped[str] = mapped_column(ForeignKey("parts.id"))
    cell: Mapped[str] = mapped_column(String(32), default="")
    expected: Mapped[float] = mapped_column(Float, default=0)                        # учётный остаток на начало подсчёта
    counted: Mapped[Optional[float]] = mapped_column(Float)                          # NULL — ещё не посчитано
    added: Mapped[bool] = mapped_column(Boolean, default=False)                      # найдено при подсчёте, в учёте не было
    counted_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    counted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ запчасти: парк машин клиентов, наработка, ТО-комплекты (ТЗ запчастей, раздел 9)

class OtherMachineModel(TimestampMixin, Base):
    """Модель другого производителя — без техпараметров и цен, только для парка и подбора запчастей."""
    __tablename__ = "other_machine_models"
    __table_args__ = (UniqueConstraint("manufacturer", "model", name="uq_other_machine_model"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    manufacturer: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    machine_type: Mapped[str] = mapped_column(String(255), default="")


class ClientSite(TimestampMixin, Base):
    """Площадка клиента (D-182, K-2): карьер, рудник, фабрика, ремонтная база, склад, офис. Контакты — отдельно (K-1)."""
    __tablename__ = "client_sites"
    __table_args__ = (
        Index("ix_client_sites_client_active", "client_id", "is_active"),
        Index("ix_client_sites_name", "name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    site_type: Mapped[str] = mapped_column(String(32), default="other")     # SITE_TYPES в api/client_sites.py
    code: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(String(2), default="")
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    timezone: Mapped[str] = mapped_column(String(64), default="")
    access_mode: Mapped[str] = mapped_column(String(32), default="")        # ACCESS_MODES в api/client_sites.py
    hse_notes: Mapped[str] = mapped_column(Text, default="")               # требования охраны труда и допуска
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class ClientContact(TimestampMixin, Base):
    """Контактное лицо клиента (D-184, K-1). Персональные данные: только рабочие телефон и почта (решение 1, D-180);
    просмотры — в client_contact_views; удаление по запросу — без следов значений в журнале."""
    __tablename__ = "client_contacts"
    __table_args__ = (Index("ix_client_contacts_client_active", "client_id", "is_active"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_sites.id", ondelete="SET NULL"), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[str] = mapped_column(String(255), default="")
    department: Mapped[str] = mapped_column(String(255), default="")
    roles: Mapped[list[str]] = mapped_column(JSONType, default=list)        # CONTACT_ROLES в api/client_contacts.py
    work_phone: Mapped[str] = mapped_column(String(64), default="")
    mobile_phone: Mapped[str] = mapped_column(String(64), default="")         # D-190: мобильный (рабочий) — по просьбе владельца 27.11
    work_email: Mapped[str] = mapped_column(String(255), default="")
    messenger: Mapped[str] = mapped_column(String(255), default="")          # рабочий мессенджер (Telegram, WeCom…)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_on: Mapped[Optional[date]] = mapped_column(Date)                 # отметка о согласии — когда включена в настройках
    consent_source: Mapped[str] = mapped_column(String(255), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    # D-188: дополнительные рабочие адреса [{email, label}] и обезличивание по запросу (запись и связи остаются)
    extra_emails: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    anonymized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ClientContactLink(TimestampMixin, Base):
    """Связь контакта (человека) с клиентом (D-188): роли, площадка, основной, период. Один человек — несколько организаций.
    client_contacts.client_id — клиент, у которого контакт заведён; списки клиента строятся по связям."""
    __tablename__ = "client_contact_links"
    __table_args__ = (Index("ix_client_contact_links_client_active", "client_id", "is_active"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    contact_id: Mapped[str] = mapped_column(ForeignKey("client_contacts.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_sites.id", ondelete="SET NULL"))
    roles: Mapped[list[str]] = mapped_column(JSONType, default=list)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class ClientContactHistory(Base):
    """История изменений контакта (D-188): было / стало по полям. Доступ — по группе «Клиенты: контактные лица» (правка);
    при обезличивании стирается. В общем журнале действий персональных данных нет."""
    __tablename__ = "client_contact_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[str] = mapped_column(String(36), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")


class KpDispatch(Base):
    """Отправка КП (D-188): неизменяемый снимок — кто, когда, как (из системы / вручную), номер письма. Ключ повтора — без дублей."""
    __tablename__ = "kp_dispatches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(36))
    revision_no: Mapped[Optional[int]] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16))                 # system | manual
    sent_by: Mapped[Optional[str]] = mapped_column(String(36))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    subject: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str] = mapped_column(String(255), default="")
    dispatch_key: Mapped[str] = mapped_column(String(64), unique=True)


class KpDispatchRecipient(Base):
    __tablename__ = "kp_dispatch_recipients"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispatch_id: Mapped[str] = mapped_column(ForeignKey("kp_dispatches.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(4))                    # to | cc
    position: Mapped[int] = mapped_column(Integer, default=0)
    contact_id: Mapped[Optional[str]] = mapped_column(String(36))
    link_id: Mapped[Optional[str]] = mapped_column(String(36))
    display_name: Mapped[str] = mapped_column(String(255), default="")
    job_title: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255))


class ContactMigrationMap(Base):
    """Разовый перенос контактов (D-189): источник → контакт и связь справочника. Повторный перенос по ключу ничего не дублирует."""
    __tablename__ = "contact_migration_map"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(24))                  # client_card | legal_profile
    source_key: Mapped[str] = mapped_column(String(160), unique=True)
    contact_id: Mapped[Optional[str]] = mapped_column(String(36))
    link_id: Mapped[Optional[str]] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(16))                 # created | matched | ambiguous
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientContactView(Base):
    """Журнал просмотров контактов клиентов (152-ФЗ, D-184): кто и когда открыл контакты какого клиента. Только добавление."""
    __tablename__ = "client_contact_views"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    contact_id: Mapped[Optional[str]] = mapped_column(String(36))           # пусто — просмотр списка контактов клиента
    kind: Mapped[str] = mapped_column(String(16), default="list")           # list | card | export


class ClientMachine(TimestampMixin, Base):
    """Машина клиента: из закрытой сделки ГШО (по позиции и номеру единицы) или заведённая вручную."""
    __tablename__ = "client_machines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[Optional[str]] = mapped_column(String(36))
    unit_no: Mapped[int] = mapped_column(Integer, default=1)                         # единица позиции сделки (qty > 1)
    equipment_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id"))
    other_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("other_machine_models.id"))
    manufacturer: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    serial: Mapped[str] = mapped_column(String(128), default="")
    commissioned_on: Mapped[Optional[date]] = mapped_column(Date)
    site: Mapped[str] = mapped_column(String(255), default="")                       # место работы
    hours_per_day: Mapped[Optional[float]] = mapped_column(Float)                    # вручную, если замеров мало
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    site_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_sites.id", ondelete="SET NULL"), index=True)   # D-182
    origin: Mapped[str] = mapped_column(String(24), default="unknown")   # berghaus | third_party | rental | other | unknown
    warranty_until: Mapped[Optional[date]] = mapped_column(Date)


class MachineParty(TimestampMixin, Base):
    """Сторона по машине (D-186, K-3; решение 2 владельца): собственник, лизингодатель, эксплуатант — с датами «с — по».
    Главная действующая сторона одна; её клиент = client_machines.client_id (сервис и счета). Закрытые записи — история передач."""
    __tablename__ = "machine_parties"
    __table_args__ = (Index("ix_machine_parties_machine_active", "machine_id", "valid_to"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(ForeignKey("client_machines.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")           # owner | lessor | operator | other
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)                    # пусто — действует
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class MachineHours(Base):
    """Замер наработки (моточасы) — сервис или менеджер со слов клиента."""
    __tablename__ = "machine_hours"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(ForeignKey("client_machines.id", ondelete="CASCADE"), index=True)
    on: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Float)
    entered_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaintenanceKit(TimestampMixin, Base):
    """ТО-комплект: запчасти на одно ТО модели с периодичностью в моточасах."""
    __tablename__ = "maintenance_kits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    equipment_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id"), index=True)
    other_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("other_machine_models.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))                                   # ТО-1, ТО-500 …
    interval_hours: Mapped[float] = mapped_column(Float)
    lines: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # [{part_id, article, name, qty}]
    source: Mapped[str] = mapped_column(String(16), default="manual")                # manual | deal_zip (ЗИП на ТО из RFQ сделки)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # J-e (D-201): работы из справочника сервиса и диапазон заводских номеров машин, к которым подходит комплект
    works: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # [{work_type_id, code, title, norm_hours}]
    serial_from: Mapped[str] = mapped_column(String(64), default="")
    serial_to: Mapped[str] = mapped_column(String(64), default="")


class MachineReminder(Base):
    """Напоминание менеджеру о ТО машины (одно на машину и ТО по моточасам)."""
    __tablename__ = "machine_reminders"
    __table_args__ = (UniqueConstraint("machine_id", "to_hours", name="uq_machine_reminder"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    machine_id: Mapped[str] = mapped_column(ForeignKey("client_machines.id", ondelete="CASCADE"), index=True)
    to_hours: Mapped[float] = mapped_column(Float)
    due_on: Mapped[Optional[date]] = mapped_column(Date)
    kit_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    manager_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_id: Mapped[Optional[str]] = mapped_column(String(36))                    # заявка, созданная по напоминанию
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ запчасти: гарантийные претензии к заводу, сборные партии (ТЗ запчастей, разделы 7, 9)

class WarrantyClaim(TimestampMixin, Base):
    """Гарантийная претензия к заводу: что вышло из строя, наработка, фото; ответ завода и компенсация (деталью, деньгами, скидкой)."""
    __tablename__ = "warranty_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ГП-2026-0001
    supplier_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"))
    machine_id: Mapped[Optional[str]] = mapped_column(String(36))
    deal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("deals.id"))            # сделка ГШО — расход на её гарантийный резерв
    request_id: Mapped[Optional[str]] = mapped_column(String(36))                     # гарантийная заявка клиента
    failure_on: Mapped[Optional[date]] = mapped_column(Date)
    machine_hours: Mapped[Optional[float]] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    lines: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # [{part_id, article, name, qty, cost_rub}]
    photos: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")                 # draft | sent | accepted | partial | rejected | compensated | closed
    answer_due: Mapped[Optional[date]] = mapped_column(Date)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    answer: Mapped[str] = mapped_column(Text, default="")
    compensations: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # [{id, kind: part|money|discount, qty, amount, currency, note, po_line_id, received_on}]
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str] = mapped_column(Text, default="")
    sla_notified: Mapped[list[str]] = mapped_column(JSONType, default=list)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class PartBatch(TimestampMixin, Base):
    """Сборная партия у перевозчика или экспедитора: строки заказов поставщикам, статусы перевозки, расходы и их распределение."""
    __tablename__ = "part_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)                     # ПТ-2026-0001
    forwarder: Mapped[str] = mapped_column(String(255), default="")
    route_id: Mapped[Optional[str]] = mapped_column(ForeignKey("supply_routes.id"))
    status: Mapped[str] = mapped_column(String(16), default="forming")               # forming | transit | customs | arrived | closed
    shipped_on: Mapped[Optional[date]] = mapped_column(Date)
    eta: Mapped[Optional[date]] = mapped_column(Date)
    costs: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # [{item, amount, currency}]
    allocation: Mapped[str] = mapped_column(String(8), default="weight")             # weight | value
    tracking: Mapped[str] = mapped_column(String(128), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class FactoryChange(Base):
    """Предложенное изменение карточки утверждённого поставщика: в карточку попадает после утверждения КД или ЗГД."""
    __tablename__ = "factory_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id", ondelete="CASCADE"), index=True)
    proposed_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data: Mapped[dict] = mapped_column(JSONType, default=dict)                       # только изменённые поля: {поле: новое значение}
    before: Mapped[dict] = mapped_column(JSONType, default=dict)                     # их значения на момент предложения
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")               # pending | approved | rejected
    decided_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ документы модели и приёмка машины на заводе

class ModelDocument(Base):
    """Документ модели каталога: руководство по эксплуатации (версии, языки), чертежи, схемы (гидравлика, электрика…), каталог запчастей."""
    __tablename__ = "model_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(ForeignKey("equipment_models.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))                                    # manual | drawing | hydraulic | electric | pneumatic | scheme | parts_catalog | certificate | other
    title: Mapped[str] = mapped_column(String(255), default="")
    language: Mapped[str] = mapped_column(String(8), default="ru")
    version: Mapped[str] = mapped_column(String(64), default="")
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(Integer, default=0)
    source_deal_id: Mapped[Optional[str]] = mapped_column(String(36))
    comment: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AcceptanceUnit(TimestampMixin, Base):
    """Приёмка одной машины на заводе: серийные номера, чек-лист, испытания, фото и видео, замечания, решение и акт."""
    __tablename__ = "acceptance_units"
    __table_args__ = (UniqueConstraint("position_id", "unit_no", name="uq_acceptance_unit"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), index=True)
    unit_no: Mapped[int] = mapped_column(Integer, default=1)
    serial: Mapped[str] = mapped_column(String(128), default="")                     # заводской номер машины
    engine_no: Mapped[str] = mapped_column(String(128), default="")
    frame_no: Mapped[str] = mapped_column(String(128), default="")
    components: Mapped[list[dict]] = mapped_column(JSONType, default=list)           # основные агрегаты: [{name, serial}]
    checklist: Mapped[list[dict]] = mapped_column(JSONType, default=list)            # [{id, group, item, expected, result: ""|ok|fail|na, comment}]
    tests: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # [{id, name, result: ""|ok|fail, value, note}]
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)                # фото, видео, документы: [{path, name, kind, at, by}]
    remarks: Mapped[list[dict]] = mapped_column(JSONType, default=list)              # [{id, text, responsible, due, fixed_on, fixed_note}]
    inspector: Mapped[str] = mapped_column(String(255), default="")                  # кто принимал (наш представитель)
    factory_rep: Mapped[str] = mapped_column(String(255), default="")                # представитель завода
    place: Mapped[str] = mapped_column(String(255), default="")
    inspected_on: Mapped[Optional[date]] = mapped_column(Date)
    decision: Mapped[str] = mapped_column(String(24), default="")                    # "" | accepted | accepted_remarks | rejected
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    act: Mapped[dict] = mapped_column(JSONType, default=dict)                        # {number, date, signed_factory_on, signed_ours_on, file: {path, name}}
    comment: Mapped[str] = mapped_column(Text, default="")
    machine_id: Mapped[Optional[str]] = mapped_column(String(36))                    # машина в парке (эксплуатация, сервис)
    mode: Mapped[str] = mapped_column(String(16), default="factory")                 # текущая приёмка: online — онлайн | factory — на заводе
    is_final: Mapped[bool] = mapped_column(Boolean, default=True)                    # приёмка окончательная (на заводе — всегда; онлайн — по отметке)
    round_no: Mapped[int] = mapped_column(Integer, default=1)                        # номер текущей приёмки машины
    rounds: Mapped[list[dict]] = mapped_column(JSONType, default=list)               # история предыдущих приёмок (полные снимки)


# ------------------------------------------------------------------ цены конкурентов (D-066): видят только КД и ЗГД
class Competitor(TimestampMixin, Base):
    """Конкурент — справочник, пополняется вводом названия в предложении конкурента."""
    __tablename__ = "competitors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255), unique=True)          # нормализованное название (регистр, пробелы, кавычки)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


class CompetitorOffer(TimestampMixin, Base):
    """Цена конкурента по сделке: дата сведений, сумма как введена (с НДС или без), валюта, курс ЦБ на дату; правки — в журнале действий."""
    __tablename__ = "competitor_offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    variant_id: Mapped[Optional[str]] = mapped_column(String(36))            # с каким нашим предложением сравнивается
    competitor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("competitors.id"), index=True)   # пусто — название неизвестно
    on: Mapped[date] = mapped_column(Date)
    equipment: Mapped[str] = mapped_column(String(500), default="")          # что предлагает конкурент (марка, модель, комплектация)
    qty: Mapped[float] = mapped_column(Float, default=1)
    amount: Mapped[float] = mapped_column(Money)                             # как введено
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    vat_included: Mapped[bool] = mapped_column(Boolean, default=False)
    vat_pct: Mapped[float] = mapped_column(Float, default=22.0)
    fx_rate: Mapped[float] = mapped_column(Money, default=1.0)               # RUB за единицу валюты на дату сведений
    fx_on: Mapped[Optional[date]] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(255), default="")             # откуда сведения (тендер, клиент, рынок…)
    delivery_term: Mapped[str] = mapped_column(String(255), default="")      # срок поставки конкурента (D-074)
    payment_terms: Mapped[str] = mapped_column(String(500), default="")      # условия оплаты конкурента (D-074)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[Optional[str]] = mapped_column(String(36))


# ------------------------------------------------------------------ раздел «Финансы» (вариант Б): docs/finance/
from .finance import (  # noqa: E402,F401
    FinanceAccount, FinanceAllocation, FinanceBalanceSnapshot, FinanceFxOperation, FinanceImportBatch, FinanceObligation, FinanceQuestion, FinanceRecognitionEvent, FinanceTransaction,
)



# ---------------------------------------------------------------- Канцелярия (D-128)

class OfficeSigner(TimestampMixin, Base):
    """Подписант документов юр. лица группы: ведёт ЗГД. Факсимиле и ЭЦП — отдельными этапами."""
    __tablename__ = "office_signers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    full_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[str] = mapped_column(String(255), default="")
    basis: Mapped[str] = mapped_column(String(255), default="")          # «Устав» / «Доверенность № … от …»
    poa_id: Mapped[Optional[str]] = mapped_column(String(36))             # доверенность юр. блока
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    methods: Mapped[list[str]] = mapped_column(JSONType, default=list)  # manual | facsimile | eds
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


class OfficeCounter(Base):
    """Счётчик номеров: юр. лицо × журнал × год; номер выдаётся под блокировкой строки и не повторяется."""
    __tablename__ = "office_counters"
    __table_args__ = (UniqueConstraint("entity_id", "journal", "year", name="uq_office_counter"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"))
    journal: Mapped[str] = mapped_column(String(16))     # ВХ | ИСХ | ПР-ОД | ПР-К | ПР-АХ | РП | СЗ | ВН
    year: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer, default=0)


class OfficeDoc(TimestampMixin, Base):
    """Документ Канцелярии: входящее и исходящее письмо, приказ, распоряжение, служебная записка, другой внутренний документ."""
    __tablename__ = "office_docs"
    __table_args__ = (Index("ix_office_docs_kind_status", "kind", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))                        # in | out | order | directive | memo | internal
    category: Mapped[str] = mapped_column(String(16), default="")      # приказ: od | hr | ah; внутренний: вид документа
    journal: Mapped[str] = mapped_column(String(16), default="")
    number: Mapped[str] = mapped_column(String(48), default="", index=True)
    seq: Mapped[Optional[int]] = mapped_column(Integer)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    reg_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="draft")   # draft | registered | assigned | in_work | signed | sent | in_force | closed | cancelled
    subject: Mapped[str] = mapped_column(String(512), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    letter_type: Mapped[str] = mapped_column(String(16), default="")   # request | claim | complaint | gov | notice | other
    channel: Mapped[str] = mapped_column(String(16), default="")       # email | post | courier | edo | portal | hand
    counterparty: Mapped[str] = mapped_column(String(255), default="")
    contact: Mapped[str] = mapped_column(String(255), default="")
    sender_number: Mapped[str] = mapped_column(String(64), default="")
    sender_date: Mapped[Optional[date]] = mapped_column(Date)
    due_on: Mapped[Optional[date]] = mapped_column(Date)
    executor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    co_executor_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    deal_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    deal_section: Mapped[str] = mapped_column(String(16), default="")
    contract_id: Mapped[Optional[str]] = mapped_column(String(36))
    claim_id: Mapped[Optional[str]] = mapped_column(String(36))
    reply_to_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    signer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("office_signers.id"))
    signed_on: Mapped[Optional[date]] = mapped_column(Date)
    sign_method: Mapped[str] = mapped_column(String(16), default="")  # manual | facsimile | eds
    sent_on: Mapped[Optional[date]] = mapped_column(Date)
    effective_on: Mapped[Optional[date]] = mapped_column(Date)          # приказ / распоряжение — дата вступления в силу
    closed_on: Mapped[Optional[date]] = mapped_column(Date)
    close_note: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # {id, name, size, sha256, kind: original | scan | attachment | draft, at, by}
    history: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))
    # D-129: согласование перед подписанием — {route_id, name, mode: seq | par, round, started_at, started_by, steps: [{id, user_id, role, title,
    # status: waiting | pending | approved | rejected, by, comment, at}]}
    approval: Mapped[dict] = mapped_column(JSONType, default=dict)
    # D-129: реквизиты и комментарии
    comments: Mapped[list[dict]] = mapped_column(JSONType, default=list)      # [{id, by, text, at}] — обсуждение документа
    note: Mapped[str] = mapped_column(Text, default="")                      # примечание делопроизводителя (видно в журнале)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    confidential: Mapped[bool] = mapped_column(Boolean, default=False)     # видят делопроизводитель, ЗГД, исполнитель, согласующие
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=True)       # входящее: требует ответа
    pages: Mapped[Optional[int]] = mapped_column(Integer)                    # листов в основном документе
    attachments_count: Mapped[Optional[int]] = mapped_column(Integer)        # приложений (листов / документов)
    address: Mapped[str] = mapped_column(String(512), default="")            # адрес или email адресата / отправителя
    tracking: Mapped[str] = mapped_column(String(128), default="")           # реквизит источника / отправки: трек, № накладной, № в ЭДО, e-mail, кто передал (D-131)
    delivered_on: Mapped[Optional[date]] = mapped_column(Date)               # исходящее: вручено адресату
    # D-130: рассмотрение — руководитель накладывает резолюцию (исполнитель, соисполнители, срок, указание) или «к сведению» / «в дело»
    reviewer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    resolutions: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # [{id, by, at, kind: execute | info | file, text, executor_id, co, due_on}]
    alerts: Mapped[dict] = mapped_column(JSONType, default=dict)            # D-133: отправленные напоминания о сроке {ключ: дата} — без повторов
    acks: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # D-140: лист ознакомления [{user_id, by, at, acked_at, reminded}]


class OfficeRoute(TimestampMixin, Base):
    """Типовая схема согласования (ведёт ЗГД): для каких документов, порядок, шаги (сотрудник или роль)."""
    __tablename__ = "office_routes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    kinds: Mapped[list[str]] = mapped_column(JSONType, default=list)        # out | order | directive | memo | internal
    categories: Mapped[list[str]] = mapped_column(JSONType, default=list)   # приказ: od | hr | ah; пусто — любые
    entity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("legal_entities.id"))   # пусто — все юр. лица
    mode: Mapped[str] = mapped_column(String(8), default="seq")              # seq — по очереди | par — одновременно
    steps: Mapped[list[dict]] = mapped_column(JSONType, default=list)       # [{user_id | role, title}]
    required: Mapped[bool] = mapped_column(Boolean, default=False)          # без согласования не подписать
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36))


# ------------------------------------------------------------------ D-154: оргструктура направлений и управленческие поручения

class OrgDirection(Base):
    """Направление (подразделение) рабочего места ЗГД и его руководитель. Назначает ЗГД; не назначен — так и показывается."""
    __tablename__ = "org_directions"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)             # sales | factory | logistics | finance | legal | service
    head_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[Optional[str]] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrgMember(Base):
    """D-157: явное закрепление сотрудника за направлением, если оно не совпадает с ролью (роль ≠ подразделение). Ведёт ЗГД."""
    __tablename__ = "org_members"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    direction_key: Mapped[str] = mapped_column(String(32))
    updated_by: Mapped[Optional[str]] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Assignment(TimestampMixin, Base):
    """Управленческое поручение ЗГД: руководителю подразделения или напрямую сотруднику (ТЗ v2 §5).
    Статусы: new → accepted → in_progress → blocked / review → rework → review → done; cancelled. Просрочка — по сроку, не статус."""
    __tablename__ = "assignments"
    __table_args__ = (Index("ix_assignments_status_due", "status", "due_on"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(24), index=True)               # П-2026-001
    seq: Mapped[int] = mapped_column(Integer)
    year: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="task")             # task — поручение | explain — запрос пояснения
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")           # D-156: какой результат принять (критерий приёмки)
    priority: Mapped[str] = mapped_column(String(16), default="normal")       # critical | high | normal
    mode: Mapped[str] = mapped_column(String(8), default="direct")            # head — руководителю подразделения | direct — сотруднику
    direction_key: Mapped[str] = mapped_column(String(32), default="")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    assignee_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)      # ответственный (руководитель или сотрудник)
    executor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))         # поручение руководителю: кому он передал выполнение
    head_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True) # прямое поручение: руководитель видит, но не принимает
    status: Mapped[str] = mapped_column(String(16), default="new")
    due_on: Mapped[date] = mapped_column(Date)
    first_due_on: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(String(300), default="")          # откуда поручение: сделка, проблема из очереди
    source_key: Mapped[str] = mapped_column(String(160), default="")          # ключ сигнала очереди «Требует вмешательства»
    source_title: Mapped[str] = mapped_column(String(255), default="")
    report: Mapped[str] = mapped_column(Text, default="")
    block_reason: Mapped[str] = mapped_column(Text, default="")
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[Optional[str]] = mapped_column(String(36))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    alerts: Mapped[dict] = mapped_column(JSONType, default=dict)            # D-155: отправленные напоминания {ключ: дата} — без повторов


class AssignmentFile(Base):
    """D-155: подтверждение к поручению (отчёт исполнителя, материалы). Файл прошёл общий вход загрузок (антивирус)."""
    __tablename__ = "assignment_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    scan_status: Mapped[str] = mapped_column(String(24), default="clean")
    scan_id: Mapped[Optional[str]] = mapped_column(String(36))
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[Optional[str]] = mapped_column(String(36))


class SignalLog(Base):
    """D-155: история сигналов «Требует вмешательства»: когда появился, когда ушёл (причина устранена), поручение по нему."""
    __tablename__ = "signal_log"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    title: Mapped[str] = mapped_column(String(512), default="")
    object_type: Mapped[str] = mapped_column(String(64), default="")
    object_number: Mapped[str] = mapped_column(String(64), default="")
    direction: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(String(300), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    times: Mapped[int] = mapped_column(Integer, default=1)             # сколько раз возникал (возврат после устранения)


class AssignmentEvent(Base):
    """История поручения: назначения, статусы, сроки, комментарии, решения. Только добавление."""
    __tablename__ = "assignment_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    by_id: Mapped[Optional[str]] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(24))
    from_status: Mapped[str] = mapped_column(String(16), default="")
    to_status: Mapped[str] = mapped_column(String(16), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)


# ───────────────────────── I-0 (D-191): сервисная служба — обращения, заказ-наряды, события, справочники ─────────────────────────

class ServiceWorkType(TimestampMixin, Base):
    """Вид работ с нормативом (I-0, решение 8: черновик готовит разработчик, утверждает руководитель сервиса).
    machine_kind пусто — для любой техники. Одна сущность для сервиса и каталога работ (J-d)."""
    __tablename__ = "service_work_types"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(24), default="repair")      # diagnostics | maintenance | repair | commissioning | other
    machine_kind: Mapped[str] = mapped_column(String(64), default="")
    norm_hours: Mapped[Optional[float]] = mapped_column(Float)                 # пусто — норматив не задан (не 0)
    crew_size: Mapped[int] = mapped_column(Integer, default=1)
    permit_codes: Mapped[list[str]] = mapped_column(JSONType, default=list)   # нужные допуски исполнителя
    checklist: Mapped[list[str]] = mapped_column(JSONType, default=list)      # шаблон пунктов чек-листа
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class ServicePermit(TimestampMixin, Base):
    """Допуск исполнителя (электробезопасность, высота, ГПМ, промбезопасность и т. п.) со сроком действия."""
    __tablename__ = "service_permits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    valid_months: Mapped[Optional[int]] = mapped_column(Integer)               # пусто — бессрочно
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class ServiceSlaLevel(TimestampMixin, Base):
    """Категория срока реакции: часы до реакции, выезда и восстановления. Пусто — срок не задан."""
    __tablename__ = "service_sla_levels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    reaction_hours: Mapped[Optional[float]] = mapped_column(Float)
    arrival_hours: Mapped[Optional[float]] = mapped_column(Float)
    restore_hours: Mapped[Optional[float]] = mapped_column(Float)
    calendar: Mapped[str] = mapped_column(String(16), default="24x7")         # 24x7 | business
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class ServiceRequest(TimestampMixin, Base):
    """Обращение клиента (I-0): машина — существующая ClientMachine (не дубль), площадка — ClientSite, контакт — ClientContact."""
    __tablename__ = "service_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    site_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_sites.id", ondelete="SET NULL"), index=True)
    machine_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_machines.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_contacts.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(16), default="phone")          # phone | email | visit | portal | other
    kind: Mapped[str] = mapped_column(String(24), default="repair")            # = категория вида работ
    sla_code: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new | accepted | rejected | done
    description: Mapped[str] = mapped_column(Text, default="")
    machine_down: Mapped[bool] = mapped_column(Boolean, default=False)          # машина стоит
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reject_reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class WorkOrder(TimestampMixin, Base):
    """Заказ-наряд (I-0). Статус меняется только событиями WorkOrderEvent через services/work_orders.apply —
    одинаково для веба и будущего приложения без сети; version растёт на каждое принятое событие."""
    __tablename__ = "work_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    request_id: Mapped[Optional[str]] = mapped_column(ForeignKey("service_requests.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    site_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_sites.id", ondelete="SET NULL"), index=True)
    machine_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_machines.id", ondelete="SET NULL"), index=True)
    work_type_id: Mapped[Optional[str]] = mapped_column(ForeignKey("service_work_types.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    engineer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    planned_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0)
    diagnosis: Mapped[str] = mapped_column(Text, default="")                   # I-2: заключение диагностики (событие diagnosis)
    billing: Mapped[str] = mapped_column(String(16), default="client")         # I-3: client | warranty | goodwill (событие billing)
    warranty_claim_id: Mapped[Optional[str]] = mapped_column(ForeignKey("warranty_claims.id", ondelete="SET NULL"))
    signed_by_name: Mapped[str] = mapped_column(String(255), default="")      # подпись представителя клиента при закрытии
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class WorkOrderEvent(Base):
    """Событие заказ-наряда — только добавление (триггер в базе). id задаёт отправитель (UUID): повторная отправка
    того же события не применяется второй раз, а возвращает прежний результат. result: applied | merged | conflict | rejected."""
    __tablename__ = "work_order_events"
    __table_args__ = (Index("ix_work_order_events_order_received", "work_order_id", "received_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id"), index=True)   # без каскада: история не удаляется
    kind: Mapped[str] = mapped_column(String(24))
    base_version: Mapped[int] = mapped_column(Integer)                          # версия наряда, которую видел отправитель
    new_version: Mapped[Optional[int]] = mapped_column(Integer)                 # версия после применения (пусто — не применено)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    actor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    device_id: Mapped[str] = mapped_column(String(64), default="")             # «web» или идентификатор устройства
    offline: Mapped[bool] = mapped_column(Boolean, default=False)              # событие создано без связи
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # время на устройстве (для истории, не для порядка)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    result: Mapped[str] = mapped_column(String(16), default="applied")
    reason: Mapped[str] = mapped_column(Text, default="")


class WorkOrderChecklistItem(TimestampMixin, Base):
    """Пункт чек-листа наряда. Отметки ставятся событием checklist; разные пункты от разных людей объединяются без конфликта."""
    __tablename__ = "work_order_checklist_items"
    __table_args__ = (UniqueConstraint("work_order_id", "position", name="uq_work_order_checklist_position"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(Text, default="")


# ───────────────────────── I-2 (D-195): работы наряда, допуски инженеров, фото ─────────────────────────

class EngineerPermit(TimestampMixin, Base):
    """Допуск сотрудника по справочнику service_permits: номер удостоверения и срок. Одна запись на допуск — продление
    меняет её (прежние значения — в журнале действий). Пустой срок — бессрочно."""
    __tablename__ = "engineer_permits"
    __table_args__ = (UniqueConstraint("user_id", "permit_code", name="uq_engineer_permit"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    permit_code: Mapped[str] = mapped_column(String(32))
    number: Mapped[str] = mapped_column(String(64), default="")
    issued_on: Mapped[Optional[date]] = mapped_column(Date)
    valid_until: Mapped[Optional[date]] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class WorkOrderWork(TimestampMixin, Base):
    """Работа в наряде: вид работ с нормативом, исполнитель, фактические часы. Меняется событиями work_add / work_update."""
    __tablename__ = "work_order_works"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    work_type_id: Mapped[Optional[str]] = mapped_column(ForeignKey("service_work_types.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    norm_hours: Mapped[Optional[float]] = mapped_column(Float)                 # из справочника на момент добавления
    actual_hours: Mapped[Optional[float]] = mapped_column(Float)               # пусто — не указано (не 0)
    performer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class WorkOrderPhoto(Base):
    """Фото наряда (решение владельца 27.11: хранить 10 лет, до 10 МБ, с геолокацией). Только добавление;
    файл — в хранилище, здесь — где и когда снято, кем загружено, до какой даты хранить."""
    __tablename__ = "work_order_photos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id"), index=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(36))
    storage_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    checklist_item_id: Mapped[Optional[str]] = mapped_column(ForeignKey("work_order_checklist_items.id", ondelete="SET NULL"))
    work_id: Mapped[Optional[str]] = mapped_column(ForeignKey("work_order_works.id", ondelete="SET NULL"))
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    accuracy_m: Mapped[Optional[float]] = mapped_column(Float)
    geo_source: Mapped[str] = mapped_column(String(8), default="")             # device | exif | пусто — нет координат
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retain_until: Mapped[date] = mapped_column(Date)


# ───────────────────────── I-3 (D-198): смета наряда с версиями и согласованием, документы-снимки ─────────────────────────

class ServiceEstimate(TimestampMixin, Base):
    """Смета наряда. Версии: черновик правится, отправленная и решённая — нет; новая версия делает прежнюю «заменённой».
    Решение клиента (согласовал / отказал) записывает руководитель сервиса — с ФИО и основанием (письмо, подпись)."""
    __tablename__ = "service_estimates"
    __table_args__ = (UniqueConstraint("work_order_id", "version", name="uq_service_estimate_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")          # draft | sent | approved | rejected | superseded
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    vat_pct: Mapped[float] = mapped_column(Float, default=22.0)
    hour_rate: Mapped[Optional[float]] = mapped_column(Float)                  # ставка нормо-часа без НДС; пусто — не задана
    note: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")                      # почему новая версия
    total_net: Mapped[Optional[float]] = mapped_column(Float)                  # фиксируются при отправке
    total_vat: Mapped[Optional[float]] = mapped_column(Float)
    total: Mapped[Optional[float]] = mapped_column(Float)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_channel: Mapped[str] = mapped_column(String(16), default="")         # email | messenger | paper | other
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    client_name: Mapped[str] = mapped_column(String(255), default="")         # кто у клиента согласовал / отказал
    decision_basis: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))  # кто записал решение
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))


class ServiceEstimateLine(Base):
    """Строка сметы: работа (часы × ставка), запчасть из каталога или прочее. Пустая цена — «не задана», смету не отправить."""
    __tablename__ = "service_estimate_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    estimate_id: Mapped[str] = mapped_column(ForeignKey("service_estimates.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(8), default="other")              # work | part | other
    part_id: Mapped[Optional[str]] = mapped_column(ForeignKey("parts.id", ondelete="SET NULL"))
    work_id: Mapped[Optional[str]] = mapped_column(ForeignKey("work_order_works.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(500))
    article: Mapped[str] = mapped_column(String(128), default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String(16), default="шт")
    price: Mapped[Optional[float]] = mapped_column(Float)                      # без НДС, в валюте сметы
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")


class ServiceDocument(Base):
    """Документ наряда — снимок на момент формирования: данные (snapshot) и файл. Не меняется; новый документ — новая запись.
    legal_checked = false — шаблон юристом не проверен, в файле пометка «черновик»."""
    __tablename__ = "service_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.id"), index=True)
    estimate_id: Mapped[Optional[str]] = mapped_column(ForeignKey("service_estimates.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))                               # defect_list | estimate | act
    number: Mapped[str] = mapped_column(String(40), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    storage_path: Mapped[str] = mapped_column(Text, default="")
    filename: Mapped[str] = mapped_column(String(255), default="")
    legal_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ───────────────────────── J-c (D-200): узлы и агрегаты ─────────────────────────

class EquipmentAssembly(TimestampMixin, Base):
    """Узел / агрегат машины (двигатель, гидросистема, трансмиссия…). model_id пусто — общий справочник для любой техники;
    иначе — узел конкретной модели ГШО. parent_id — вложенность (агрегат → узел), первый шаг — плоский список.
    Названия на трёх языках — для загрузки каталогов завода (J-i) и документов на китайском."""
    __tablename__ = "equipment_assemblies"
    __table_args__ = (UniqueConstraint("model_id", "name_ru", name="uq_equipment_assembly_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_assemblies.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(32), default="")
    name_ru: Mapped[str] = mapped_column(String(255))
    name_en: Mapped[str] = mapped_column(String(255), default="")
    name_cn: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
