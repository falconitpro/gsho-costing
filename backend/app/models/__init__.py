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
    JSON, Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.workflow import DealStatus
from ..db import Base

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
    observer_scope: Mapped[dict] = mapped_column(JSONType, default=dict)    # объём доступа наблюдателя
    visa_limit_rub: Mapped[Optional[float]] = mapped_column(Float)
    notify_channels: Mapped[list[str]] = mapped_column(JSONType, default=lambda: ["outlook", "wecom", "app"])
    wecom_user_id: Mapped[Optional[str]] = mapped_column(String(100))
    # карточка контактов исполнителя: {position, department, mobile, work_phone, wechat, telegram, whatsapp, max, extra_email, note}
    contacts: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


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
    registry: Mapped[dict] = mapped_column(JSONType, default=dict)             # ответ источника (сокращённо)
    registry_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    manager_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    default_payment_scheme_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_schemes.id"))


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
    unit_price: Mapped[float] = mapped_column(Float)
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
    unit_price_rub: Mapped[Optional[float]] = mapped_column(Float)
    total_rub: Mapped[Optional[float]] = mapped_column(Float)
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
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    per_unit: Mapped[Optional[float]] = mapped_column(Float)         # ставка за единицу транспорта
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
    operating_conditions: Mapped[dict] = mapped_column(JSONType, default=dict)   # 0.4–0.6
    service_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    target_hours: Mapped[Optional[float]] = mapped_column(Float)
    client_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)   # задаёт КД (задача 1); пусто = типовая схема
    client_payment_wishes: Mapped[str] = mapped_column(Text, default="")             # пожелания клиента по оплате (менеджер)
    bank_guarantee: Mapped[dict] = mapped_column(JSONType, default=dict)             # {mode: one_time_pct|annual_pct|fixed, value} — переопределение КД
    scenario_override: Mapped[dict] = mapped_column(JSONType, default=dict)    # ΔK/Δt сделки (КД, с комментарием)
    logistics_done: Mapped[bool] = mapped_column(Boolean, default=False)
    service_done: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    approved_version_id: Mapped[Optional[str]] = mapped_column(String(36))
    storage_folder: Mapped[str] = mapped_column(String(512), default="")
    outcome: Mapped[Optional[str]] = mapped_column(String(16))
    outcome_reason: Mapped[str] = mapped_column(Text, default="")
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
    execution_closed_at: Mapped[Optional[date]] = mapped_column(Date)
    contract_terms_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    in_execution: Mapped[bool] = mapped_column(Boolean, default=False)              # сделка на стадии контракта/исполнения (возврат после повторной визы)
    execution_started: Mapped[bool] = mapped_column(Boolean, default=False)         # контракт подписан, исполнение начато (повторная виза возвращает в исполнение)

    client = relationship("Client")
    positions = relationship("Position", back_populates="deal", cascade="all, delete-orphan")
    variants = relationship("Variant", back_populates="deal", cascade="all, delete-orphan")
    batches = relationship("Batch", back_populates="deal", cascade="all, delete-orphan")
    versions = relationship("DealVersion", back_populates="deal", order_by="DealVersion.version_no")


class Position(TimestampMixin, Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"))
    equipment_model_id: Mapped[Optional[str]] = mapped_column(ForeignKey("equipment_models.id"))
    machine_type: Mapped[str] = mapped_column(String(255), default="")
    trademark: Mapped[str] = mapped_column(String(128), default="")            # торговая марка оборудования (по заводу)
    model: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1)                       # 13.4
    unit_price: Mapped[float] = mapped_column(Float)                           # 13.5
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    options: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # 12.x
    options_price: Mapped[float] = mapped_column(Float, default=0)
    basis: Mapped[str] = mapped_column(String(32), default="FCA")             # 13.2
    delivery_point: Mapped[str] = mapped_column(String(255), default="")       # 13.3
    production_days: Mapped[int] = mapped_column(Integer, default=90)          # 14.1 (календарные дни, производное от срока ниже)
    delivery_days: Mapped[int] = mapped_column(Integer, default=10)            # 14.2
    # задача 3: срок производства с единицами и точкой отсчёта; срок поставки клиенту — единица и точка отсчёта
    production_term: Mapped[dict] = mapped_column(JSONType, default=dict)      # {value, unit: calendar_days|working_days|weeks|date, date, start_event: factory_contract|factory_prepayment}
    delivery_term: Mapped[dict] = mapped_column(JSONType, default=dict)        # {unit: calendar_days|working_days|weeks, start_event: client_contract|client_prepayment|lc_opened}
    warranty_months: Mapped[Optional[int]] = mapped_column(Integer)            # 14.4
    warranty_hours: Mapped[Optional[float]] = mapped_column(Float)             # 14.5
    zip_price: Mapped[Optional[float]] = mapped_column(Float)                  # 13.8
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
    cost: Mapped[float] = mapped_column(Float, default=0.0)                   # за единицу
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    supplier_lead_days: Mapped[int] = mapped_column(Integer, default=30)
    needs_install: Mapped[bool] = mapped_column(Boolean, default=True)
    install_stage: Mapped[str] = mapped_column(String(32), default="during_commissioning")   # before_handover | during_commissioning
    installer: Mapped[str] = mapped_column(String(32), default="service")    # service | contractor
    install_days: Mapped[int] = mapped_column(Integer, default=0)
    install_cost_rub: Mapped[float] = mapped_column(Float, default=0.0)
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
    unit_price: Mapped[Optional[float]] = mapped_column(Float)
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
    responsible_roles: Mapped[list[str]] = mapped_column(JSONType, default=list)
    event: Mapped[str] = mapped_column(String(32), default="")                # событие расчёта, которое закрывает этап
    done_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")


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
    price_rub: Mapped[Optional[float]] = mapped_column(Float)              # без НДС, назначает КД
    price_mode: Mapped[str] = mapped_column(String(16), default="amount")  # amount | markup | margin
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
    price_before_rub: Mapped[Optional[float]] = mapped_column(Float)
    price_rub: Mapped[Optional[float]] = mapped_column(Float)                     # итог КП без НДС после изменений
    margin_pct: Mapped[Optional[float]] = mapped_column(Float)                    # маржа базового сценария по итоговой цене (ориентир)
    prices: Mapped[dict] = mapped_column(JSONType, default=dict)                  # {variant_id: итог без НДС}
    kp_path: Mapped[str] = mapped_column(String(1024), default="")
    pdf_path: Mapped[str] = mapped_column(String(1024), default="")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


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
    entity: Mapped[str] = mapped_column(String(32), default="")   # batch | position | version | message
    entity_id: Mapped[Optional[str]] = mapped_column(String(36))
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    storage_url: Mapped[str] = mapped_column(String(1024), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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


class PlanFactEntry(Base):
    """План vs факт (раздел 12): фактические суммы по статьям и платежам."""
    __tablename__ = "plan_fact_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    position_id: Mapped[Optional[str]] = mapped_column(ForeignKey("positions.id"))
    kind: Mapped[str] = mapped_column(String(16))        # cost_item | payment_in | payment_out | delivery | fx
    cost_item_code: Mapped[Optional[str]] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    fx_rate: Mapped[Optional[float]] = mapped_column(Float)
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


class ImportMapping(TimestampMixin, Base):
    """Задача 4: запомненное сопоставление столбцов импорта таблицы для клиента / завода (повторные загрузки)."""
    __tablename__ = "import_mappings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(80), index=True)          # client:<id> | factory:<id>
    target: Mapped[str] = mapped_column(String(32))                     # requirements | factory_answer
    header_sig: Mapped[list] = mapped_column(JSONType, default=list)   # нормализованные заголовки — по ним узнаём «тот же формат»
    mapping: Mapped[dict] = mapped_column(JSONType, default=dict)      # {header_row, first_row, columns}
    updated_by: Mapped[Optional[str]] = mapped_column(String(36))


class IntegrationLog(Base):
    """Журнал обменов с внешними системами (Graph, WeCom, ЦБ)."""
    __tablename__ = "integration_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    system: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
