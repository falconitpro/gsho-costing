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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ справочники

class Client(TimestampMixin, Base):
    __tablename__ = "clients"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12), unique=True)                # дубли по ИНН запрещены
    address: Mapped[str] = mapped_column(Text, default="")
    sites: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # объекты / месторождения
    contacts: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    manager_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    default_payment_scheme_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_schemes.id"))


class Factory(TimestampMixin, Base):
    __tablename__ = "factories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)              # 0.7
    name_zh: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(Text, default="")                   # 0.8
    country: Mapped[str] = mapped_column(String(64), default="CN")
    contacts: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    wecom_external_ids: Mapped[list[str]] = mapped_column(JSONType, default=list)
    payment_terms: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    typical_production_days: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[float]] = mapped_column(Float)


class EquipmentModel(TimestampMixin, Base):
    """Каталог оборудования: единый источник для логиста, сервиса и КП."""
    __tablename__ = "equipment_models"
    __table_args__ = (UniqueConstraint("factory_id", "model", name="uq_factory_model"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"))
    machine_type: Mapped[str] = mapped_column(String(255))                   # 1.1
    model: Mapped[str] = mapped_column(String(255))                          # 1.2
    names: Mapped[dict] = mapped_column(JSONType, default=dict)              # {"ru":..., "en":..., "zh":...}
    tech_params: Mapped[dict] = mapped_column(JSONType, default=dict)        # разделы RFQ 1–11 по кодам
    logistics_params: Mapped[dict] = mapped_column(JSONType, default=dict)   # поля с меткой L
    default_hs_code: Mapped[Optional[str]] = mapped_column(String(10))
    maintenance_regulations: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    default_hours_per_month: Mapped[Optional[float]] = mapped_column(Float)


class FactoryPriceHistory(Base):
    __tablename__ = "factory_price_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    factory_id: Mapped[str] = mapped_column(ForeignKey("factories.id"), index=True)
    model: Mapped[str] = mapped_column(String(255), index=True)
    configuration: Mapped[str] = mapped_column(Text, default="")
    unit_price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    basis: Mapped[str] = mapped_column(String(32), default="")
    on: Mapped[date] = mapped_column(Date)
    deal_id: Mapped[Optional[str]] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(16), default="rfq")   # rfq | fact | price_list


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
    vat_pct: Mapped[float] = mapped_column(Float, default=20)
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
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[DealStatus] = mapped_column(SAEnum(DealStatus, name="deal_status"), default=DealStatus.DRAFT, index=True)
    manager_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    logistician_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    service_head_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    base_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    operating_conditions: Mapped[dict] = mapped_column(JSONType, default=dict)   # 0.4–0.6
    service_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    target_hours: Mapped[Optional[float]] = mapped_column(Float)
    client_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    bank_guarantee: Mapped[dict] = mapped_column(JSONType, default=dict)
    scenario_override: Mapped[dict] = mapped_column(JSONType, default=dict)    # ΔK/Δt сделки (КД, с комментарием)
    logistics_done: Mapped[bool] = mapped_column(Boolean, default=False)
    service_done: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    approved_version_id: Mapped[Optional[str]] = mapped_column(String(36))
    storage_folder: Mapped[str] = mapped_column(String(512), default="")
    outcome: Mapped[Optional[str]] = mapped_column(String(16))
    outcome_reason: Mapped[str] = mapped_column(Text, default="")
    plan_fact_closed: Mapped[bool] = mapped_column(Boolean, default=False)

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
    model: Mapped[str] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1)                       # 13.4
    unit_price: Mapped[float] = mapped_column(Float)                           # 13.5
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    options: Mapped[list[dict]] = mapped_column(JSONType, default=list)        # 12.x
    options_price: Mapped[float] = mapped_column(Float, default=0)
    basis: Mapped[str] = mapped_column(String(32), default="FCA")             # 13.2
    delivery_point: Mapped[str] = mapped_column(String(255), default="")       # 13.3
    production_days: Mapped[int] = mapped_column(Integer, default=90)          # 14.1
    delivery_days: Mapped[int] = mapped_column(Integer, default=10)            # 14.2
    warranty_months: Mapped[Optional[int]] = mapped_column(Integer)            # 14.4
    warranty_hours: Mapped[Optional[float]] = mapped_column(Float)             # 14.5
    zip_price: Mapped[Optional[float]] = mapped_column(Float)                  # 13.8
    factory_payments: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    tech_params: Mapped[dict] = mapped_column(JSONType, default=dict)          # все поля RFQ позиции (по кодам)
    logistics_params: Mapped[dict] = mapped_column(JSONType, default=dict)     # метка L
    price_reference: Mapped[dict] = mapped_column(JSONType, default=dict)      # «Справка по цене»: last, on, deviation, comment
    rfq_import_id: Mapped[Optional[str]] = mapped_column(ForeignKey("rfq_imports.id"))
    # блок логиста (уровень позиции)
    logistics: Mapped[dict] = mapped_column(JSONType, default=dict)            # domestic, export, hs_code, duty, vat, util, cert + comments
    batch_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batches.id"))
    # блок сервиса
    service_calc: Mapped[dict] = mapped_column(JSONType, default=dict)         # ServiceInput + ServiceResult
    warranty_reserve_pct: Mapped[Optional[float]] = mapped_column(Float)

    deal = relationship("Deal", back_populates="positions")


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

    deal = relationship("Deal", back_populates="variants")


class DealVersion(Base):
    """Версия расчёта: создаётся при каждом возврате на доработку и каждом утверждении. Неизменяема."""
    __tablename__ = "deal_versions"
    __table_args__ = (UniqueConstraint("deal_id", "version_no", name="uq_deal_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))                 # approval | rework | visa_reset
    calc_input: Mapped[dict] = mapped_column(JSONType)              # DealCalcInput (включая курс ЦБ на дату)
    calc_result: Mapped[dict] = mapped_column(JSONType)             # DealCalcResult
    excel_path: Mapped[str] = mapped_column(String(512), default="")
    excel_manager_path: Mapped[str] = mapped_column(String(512), default="")
    kp_path: Mapped[str] = mapped_column(String(512), default="")
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
    block: Mapped[str] = mapped_column(String(32), default="")
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
    entity_id: Mapped[str] = mapped_column(String(36))
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


class IntegrationLog(Base):
    """Журнал обменов с внешними системами (Graph, WeCom, ЦБ)."""
    __tablename__ = "integration_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    system: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
