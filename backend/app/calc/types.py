"""Типы входных и выходных данных расчётного ядра.

Ядро не зависит от БД и веб-слоя: на вход — pydantic-модели, на выход — pydantic-модели.
Все денежные величины хранятся в валюте ввода (раздел 8 ТЗ) и пересчитываются в RUB по курсу сценария.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


Currency = Literal["CNY", "USD", "EUR", "RUB"]


class Money(BaseModel):
    amount: float = 0.0
    currency: Currency = "CNY"


class PaymentEvent(str, Enum):
    """События-основания платежей (раздел 9 ТЗ; задачи 1–3)."""

    CONTRACT = "contract"                        # подписание контракта с клиентом
    CLIENT_PREPAYMENT = "client_prepayment"      # поступление аванса клиента
    LC_OPENED = "lc_opened"                      # открытие аккредитива клиентом
    FACTORY_CONTRACT = "factory_contract"        # подписание контракта с заводом
    FACTORY_PREPAYMENT = "factory_prepayment"    # предоплата заводу
    READINESS = "readiness"                      # уведомление о готовности на заводе
    SHIPMENT = "shipment"                        # отгрузка
    BORDER = "border"                            # пересечение границы КНР–РФ (CMR с отметкой таможни)
    DECLARATION = "declaration"                  # выпуск декларации (пошлина, НДС, брокер)
    DELIVERY = "delivery"                        # поставка клиенту (на объект)
    COMMISSIONING = "commissioning"              # ввод в эксплуатацию (ПНР)
    RUNTIME = "runtime"                          # наработка N м·ч


EVENT_TITLES = {
    PaymentEvent.CONTRACT: "подписание контракта", PaymentEvent.CLIENT_PREPAYMENT: "аванс клиента",
    PaymentEvent.LC_OPENED: "открытие аккредитива", PaymentEvent.FACTORY_CONTRACT: "контракт с заводом",
    PaymentEvent.FACTORY_PREPAYMENT: "предоплата заводу", PaymentEvent.READINESS: "готовность на заводе",
    PaymentEvent.SHIPMENT: "отгрузка", PaymentEvent.BORDER: "пересечение границы (CMR)",
    PaymentEvent.DECLARATION: "выпуск ДТ", PaymentEvent.DELIVERY: "поставка клиенту",
    PaymentEvent.COMMISSIONING: "ПНР", PaymentEvent.RUNTIME: "наработка",
}


class GuaranteeKind(str, Enum):
    NONE = "none"
    ADVANCE_RETURN = "advance_return"   # гарантия возврата аванса
    PERFORMANCE = "performance"         # гарантия исполнения


class PaymentLine(BaseModel):
    """Строка графика платежей (клиента, завода или перевозчика)."""

    no: int = 1
    share_pct: Optional[float] = None       # доля, % — либо share_pct, либо amount
    amount: Optional[float] = None          # сумма в валюте графика
    event: PaymentEvent = PaymentEvent.CONTRACT
    offset_days: int = 0
    offset_kind: Literal["calendar", "working"] = "calendar"   # смещение в календарных или рабочих днях (задача 1)
    via: Literal["transfer", "lc"] = "transfer"                 # аккредитив: раскрытие после подачи документов
    runtime_hours: Optional[float] = None   # для события RUNTIME
    guarantee: GuaranteeKind = GuaranteeKind.NONE
    guarantee_pct: float = 0.0              # % от суммы платежа, покрываемый гарантией (явная гарантия, если задана)
    guarantee_months: int = 0
    fixed_rate: Optional[float] = None      # «курс зафиксирован» (раздел 8) — RUB за единицу валюты


# Типовые схемы (задача 1): применяются, пока КД не задал условия явно
DEFAULT_CLIENT_PAYMENTS = [
    {"no": 1, "share_pct": 30, "event": "contract", "offset_days": 0},
    {"no": 2, "share_pct": 70, "event": "border", "offset_days": 5, "offset_kind": "working", "via": "lc"},
]
DEFAULT_FACTORY_PAYMENTS = [
    {"no": 1, "share_pct": 30, "event": "factory_contract", "offset_days": 14, "offset_kind": "working"},
    {"no": 2, "share_pct": 70, "event": "readiness", "offset_days": 14, "offset_kind": "working"},
]


class AdvanceGuarantee(BaseModel):
    """Банковская гарантия на аванс (задача 1): учитывается, если в условиях клиента есть предоплата."""

    enabled: bool = True
    mode: Literal["one_time_pct", "annual_pct", "fixed"] = "annual_pct"
    value: float = 3.0               # % (разовый или годовой) либо сумма в RUB


class ProductionTerm(BaseModel):
    """Срок производства (задача 3): значение + единица + точка отсчёта, либо конкретная дата."""

    value: Optional[float] = None
    unit: Literal["calendar_days", "working_days", "weeks", "date"] = "calendar_days"
    fixed_date: Optional[date] = None
    start_event: Literal["factory_contract", "factory_prepayment"] = "factory_contract"


class DeliveryTerm(BaseModel):
    """Срок поставки клиенту (задача 3): единица и точка отсчёта; значение рассчитывается с резервом и округлением."""

    unit: Literal["calendar_days", "working_days", "weeks"] = "calendar_days"
    start_event: Literal["client_contract", "client_prepayment", "lc_opened"] = "client_contract"


class LocalOptionInput(BaseModel):
    """Опция, реализуемая в России (задача 6)."""

    id: str
    name: str
    qty: int = 1
    cost: float = 0.0
    currency: Currency = "RUB"
    supplier_lead_days: int = 30
    needs_install: bool = True
    install_stage: Literal["before_handover", "during_commissioning"] = "during_commissioning"
    install_days: int = 0
    install_cost_rub: float = 0.0


class ScenarioName(str, Enum):
    PESSIMISTIC = "pessimistic"
    BASE = "base"
    OPTIMISTIC = "optimistic"


class ScenarioParams(BaseModel):
    """ΔK и Δt сценария. dk_pct со знаком: +5 → курс дороже на 5 %."""

    name: ScenarioName
    dk_pct: Optional[float] = None   # None → берётся из исторической волатильности
    dt_days: int = 0


DEFAULT_SCENARIOS = [
    ScenarioParams(name=ScenarioName.PESSIMISTIC, dk_pct=5.0, dt_days=30),
    ScenarioParams(name=ScenarioName.BASE, dk_pct=0.0, dt_days=0),
    ScenarioParams(name=ScenarioName.OPTIMISTIC, dk_pct=-3.0, dt_days=-10),
]


class FxRates(BaseModel):
    """Курсы ЦБ РФ на дату расчёта, RUB за единицу валюты. Фиксируются в версии расчёта."""

    as_of: date
    rates: dict[str, float] = Field(default_factory=lambda: {"RUB": 1.0})
    source: str = "cbr"      # cbr | manual | cbr_stale
    note: str = ""

    def rate(self, cur: str) -> float:
        if cur == "RUB":
            return 1.0
        return self.rates[cur]


class ExpertPoint(BaseModel):
    on: date
    rate: float


class FxForecastSettings(BaseModel):
    """Параметры прогнозного курса (раздел 8)."""

    key_rate_pct: float = 16.0          # ключевая ставка ЦБ РФ, обновляет ФД
    pboc_rate_pct: float = 3.1          # базовая ставка НБК (LPR 1Y)
    foreign_rate_pct: dict[str, float] = Field(
        default_factory=lambda: {"CNY": 3.1, "USD": 4.5, "EUR": 2.5}
    )
    history_years: int = 3               # N
    weight_forward: float = 0.4         # w_A
    weight_trend: float = 0.3           # w_B (применяется к Б×В)
    weight_expert: float = 0.3          # w_G
    expert_points: dict[str, list[ExpertPoint]] = Field(default_factory=dict)  # по валютам
    use_historical_volatility: bool = True
    vol_multiplier_pessimistic: float = 1.0
    vol_multiplier_optimistic: float = 0.6
    source_note: str = ""


class FinanceSettings(BaseModel):
    """Стоимость денег и комиссии (раздел 9)."""

    financing_rate_pct: float = 18.0        # ключевая + маржа банка
    deposit_rate_pct: Optional[float] = None  # доход от размещения, если ФД включил
    conversion_fee_pct: float = 0.5          # % от суммы каждого валютного платежа
    transfer_fee_rub: float = 3000.0         # фикс за перевод
    transfer_fee_pct: float = 0.0            # % от суммы перевода
    transfer_fee_mode: str = "fixed"         # fixed | pct | both — фиксированная сумма, процент или и то и другое
    guarantee_rate_pct: float = 3.0          # % годовых от суммы гарантии (явные гарантии в строках графика)
    advance_guarantee: AdvanceGuarantee = Field(default_factory=AdvanceGuarantee)   # БГ на аванс (задача 1)
    vat_offset_days: int = 90                # срок до зачёта ввозного НДС
    import_vat_in_cost: bool = False         # включать ли сумму ввозного НДС в статью 7 (иначе — только стоимость денег до зачёта через ст. 11)
    cashflow_bucket_days: int = 7            # недельные корзины
    local_options_order_reserve_days: int = 14   # запас при заказе опций РФ (задача 6)


class OverheadStep(BaseModel):
    up_to_rub: Optional[float]   # None = свыше
    pct: float


DEFAULT_OVERHEAD_SCALE = [
    OverheadStep(up_to_rub=50_000_000, pct=5.0),
    OverheadStep(up_to_rub=200_000_000, pct=4.0),
    OverheadStep(up_to_rub=None, pct=3.0),
]


class Thresholds(BaseModel):
    margin_threshold_pct: float = 3.0
    cash_gap_limit_rub: Optional[float] = None   # лимит ФД
    gd_deal_limit_rub: Optional[float] = None    # лимит суммы сделки для визы ЗГД
    factory_price_deviation_pct: float = 10.0
    logistics_deviation_pct: float = 15.0
    plan_fact_deviation_pct: float = 10.0


class ServiceSummary(BaseModel):
    """Итог блока сервиса, попадает в статью 12."""

    cost_fx: Money = Field(default_factory=lambda: Money(amount=0.0, currency="CNY"))  # ЗИП, валютная часть
    cost_rub: float = 0.0   # работы, выезды, обучение, резервы — рублёвая часть
    show_in_kp: bool = False


class PositionInput(BaseModel):
    id: str
    factory: str
    model: str
    machine_type: str = ""
    qty: int = 1
    unit_price: float
    options_price: float = 0.0            # за всё количество
    currency: Currency = "CNY"
    production_days: int = 90             # 14.1 (календарные дни; при заданном production_term — пересчитывается от него)
    production_term: Optional[ProductionTerm] = None     # задача 3
    delivery_term: DeliveryTerm = Field(default_factory=DeliveryTerm)
    delivery_days: int = 10               # 14.2 — до пункта поставки (базиса)
    factory_payments: list[PaymentLine] = Field(
        default_factory=lambda: [PaymentLine(**l) for l in DEFAULT_FACTORY_PAYMENTS]
    )
    factory_payments_default: bool = False   # график заводу взят из типовой схемы (КД не задал)
    local_options: list[LocalOptionInput] = Field(default_factory=list)   # задача 6
    actual_events: dict[str, date] = Field(default_factory=dict)          # фактические даты событий позиции (задача 2)
    # Логистика (уровень позиции)
    domestic_logistics: Money = Field(default_factory=lambda: Money(amount=0, currency="CNY"))
    export_formalities: Money = Field(default_factory=lambda: Money(amount=0, currency="CNY"))
    # Таможня
    hs_code: str = ""
    duty_pct: float = 0.0
    vat_pct: float = 22.0
    util_fee_rub: float = 0.0
    certification_rub: float = 0.0
    # Для распределения общих статей партии
    weight_kg: float = 0.0
    volume_m3: float = 0.0
    # Системные проценты
    warranty_reserve_pct: float = 2.0
    service: Optional[ServiceSummary] = None
    batch_id: Optional[str] = None


class AllocationMethod(str, Enum):
    COST = "cost"
    WEIGHT = "weight"
    VOLUME = "volume"


class BatchInput(BaseModel):
    id: str
    route: str = "завод → Маньчжурия → Забайкальск → пункт поставки"
    transport_units: int = 1
    intl_freight: Money = Field(default_factory=lambda: Money(amount=0, currency="CNY"))
    insurance_pct: Optional[float] = None       # % от стоимости (п.1) либо
    insurance_amount: Money = Field(default_factory=lambda: Money(amount=0, currency="CNY"))
    broker_rub: float = 0.0
    rf_delivery_rub: float = 0.0
    door_to_door: Optional[Money] = None      # единая стоимость доставки завод → клиент (вместо перевозки до границы и доставки по РФ)
    door_to_door_border_pct: float = 10.0     # доля до границы РФ (остальное — от границы до клиента)
    allocation: AllocationMethod = AllocationMethod.COST
    transit_days: int = 14
    customs_days: int = 5
    rf_delivery_days: int = 5             # доставка по РФ после выпуска ДТ (задача 3)
    border_planned: Optional[date] = None   # плановая дата CMR, если логист задал (иначе — расчётная)
    actual_events: dict[str, date] = Field(default_factory=dict)   # shipment | border | declaration | delivery (задача 2)


class VariantInput(BaseModel):
    id: str
    name: str
    position_ids: list[str]
    price_rub: Optional[float] = None          # цена продажи без НДС, назначает КД
    service_in_kp: bool = False
    client_payments: list[PaymentLine] = Field(
        default_factory=lambda: [PaymentLine(**l) for l in DEFAULT_CLIENT_PAYMENTS]
    )
    client_payments_default: bool = False   # условия клиента взяты из типовой схемы (КД не задал)
    overhead_pct_override: Optional[float] = None   # КД переопределил с комментарием → виза ЗГД
    overhead_override_comment: str = ""


class DealCalcInput(BaseModel):
    deal_no: str
    calc_date: date
    contract_date: date                        # плановая (или фактическая) дата подписания контракта с клиентом
    factory_contract_date: Optional[date] = None   # контракт с заводом: факт или contract_date + factory_contract_lag_days
    factory_contract_lag_days: int = 10
    lc_opening_days: int = 14                  # плановый срок открытия аккредитива после подписания (календарные дни)
    delivery_reserve_pct: float = 10.0         # резерв к сроку поставки клиенту (задача 3)
    deal_actual_events: dict[str, date] = Field(default_factory=dict)   # contract | factory_contract | client_prepayment | lc_opened
    rf_holidays: list[date] = Field(default_factory=list)              # дополнительные праздники/переносы
    base_currency: Currency = "CNY"
    client_vat_pct: float = 22.0
    fx: FxRates
    fx_history: dict[str, list[tuple[date, float]]] = Field(default_factory=dict)   # история ЦБ по валютам
    fx_settings: FxForecastSettings = Field(default_factory=FxForecastSettings)
    finance: FinanceSettings = Field(default_factory=FinanceSettings)
    overhead_scale: list[OverheadStep] = Field(default_factory=lambda: list(DEFAULT_OVERHEAD_SCALE))
    thresholds: Thresholds = Field(default_factory=Thresholds)
    scenarios: list[ScenarioParams] = Field(default_factory=lambda: list(DEFAULT_SCENARIOS))
    positions: list[PositionInput]
    batches: list[BatchInput] = Field(default_factory=list)
    variants: list[VariantInput]
    commissioning_days: int = 30
    runtime_hours_per_month: float = 400.0     # интенсивность эксплуатации для события RUNTIME
    external_commission_pct: float = 0.0       # % от цены клиента (выплата вместе с оплатами клиента)
    manager_commission_pct: float = 0.0        # % от себестоимости (статьи 1–15)
    cd_commission_pct: float = 0.0             # % от себестоимости (статьи 1–15)


# ----------------------------------------------------------------- результаты

COST_ITEMS: list[tuple[int, str, str]] = [
    (1, "factory_price", "Цена завода (с опциями)"),
    (2, "domestic_logistics", "Внутрикитайская логистика до границы"),
    (3, "export_formalities", "Экспортные формальности КНР"),
    (4, "intl_freight", "Международная перевозка"),
    (5, "insurance", "Страхование"),
    (6, "customs_duty", "Таможенная пошлина"),
    (7, "import_vat", "НДС при ввозе"),
    (8, "broker_util", "Брокер, СВХ, утильсбор"),
    (9, "certification", "Сертификация и разрешения"),
    (10, "rf_delivery", "Доставка по РФ"),
    (11, "finance", "Финансовые расходы"),
    (12, "service", "Сервис до наработки"),
    (13, "warranty_reserve", "Гарантийные случаи (резерв)"),
    (14, "overhead", "Накладные компании"),
    (15, "local_completion", "Доукомплектование в РФ"),
    (16, "external_commission", "Внешняя комиссия (от цены клиента)"),
    (17, "manager_commission", "Комиссия менеджера (от себестоимости)"),
    (18, "cd_commission", "Комиссия КД (от себестоимости)"),
]
COMMISSION_KEYS = ("external_commission", "manager_commission", "cd_commission")
COST_KEYS = [k for _, k, _ in COST_ITEMS]


class FxComponent(BaseModel):
    """Строка листа «Курсы»: один платёж."""

    payment_ref: str
    currency: str
    on: date
    t_days: int
    k_cbr: float
    comp_a_forward: float
    comp_b_trend: float
    comp_c_season: float
    season_spread: tuple[float, float] = (1.0, 1.0)
    comp_g_expert: Optional[float] = None
    weights: tuple[float, float, float] = (0.4, 0.3, 0.3)
    forecast: float
    dk_pct: dict[str, float] = Field(default_factory=dict)   # ΔK по сценариям
    rate_by_scenario: dict[str, float] = Field(default_factory=dict)
    fixed: bool = False


class DeliveryTermResult(BaseModel):
    """Срок поставки клиенту по позиции (задача 3): расчётные дни, резерв, округление до целой недели."""

    start_event: str
    start_on: date
    delivery_on: date
    calc_days: int                       # расчётное время в единицах срока (без резерва)
    unit: str
    reserve_pct: float
    with_reserve: float
    promised: int                        # после резерва и округления вверх до целой недели
    text: str                            # «105 календарных дней с даты подписания контракта»
    install_days: int = 0                # добавлено за установку опций РФ до передачи клиенту


class PositionCost(BaseModel):
    position_id: str
    scenario: ScenarioName
    items_rub: dict[str, float]          # 15 статей в RUB
    items_fx: dict[str, Money]           # статьи в валюте ввода (1–5, 12)
    customs_value_rub: float
    total_rub: float
    price_rub: Optional[float] = None    # распределённая цена
    events: dict[str, date] = Field(default_factory=dict)            # плановые даты событий позиции
    delivery_term: Optional[DeliveryTermResult] = None
    factory_payment_dates: list[dict] = Field(default_factory=list)  # график заводу: no, event, on, amount, currency, amount_rub


class CashGap(BaseModel):
    amount_rub: float = 0.0        # отрицательное число либо 0
    starts_on: Optional[date] = None
    days: int = 0


class CashflowPoint(BaseModel):
    on: date
    inflow: float
    outflow: float
    balance: float


class Sensitivity(BaseModel):
    margin_delta_pct_per_1pct_fx: float    # на сколько п.п. меняется маржа при курсе +1 %
    margin_delta_rub_per_1pct_fx: float
    breakeven_fx_factor: Optional[float]   # множитель курса, при котором маржа = порог
    breakeven_rate_base_ccy: Optional[float]


class VariantScenarioResult(BaseModel):
    variant_id: str
    scenario: ScenarioName
    items_rub: dict[str, float]
    cost_total_rub: float
    cost_total_base_ccy: float
    price_rub: Optional[float]
    margin_pct: Optional[float]
    markup_pct: Optional[float]
    profit_rub: Optional[float]
    profit_before_finance_rub: Optional[float]
    cash_gap: CashGap
    cashflow: list[CashflowPoint]
    finance_breakdown: dict[str, float]
    delivery_days: int
    positions: list[PositionCost]
    sensitivity: Optional[Sensitivity] = None
    payment_dates: list[dict] = Field(default_factory=list)   # график клиента: no, event, on, amount_rub, via
    guarantee_cost_rub: float = 0.0
    defaults_used: dict[str, bool] = Field(default_factory=dict)   # client_payments / factory_payments по типовой схеме


class ApprovalNeed(BaseModel):
    commercial: bool = True
    financial: bool = False
    general: bool = False
    reasons: list[str] = Field(default_factory=list)


class DealCalcResult(BaseModel):
    deal_no: str
    calc_date: date
    base_currency: str
    fx: FxRates
    fx_components: list[FxComponent]
    scenarios: list[ScenarioParams]
    variants: dict[str, dict[str, VariantScenarioResult]]   # variant_id → scenario → result
    approval: ApprovalNeed
    warnings: list[str] = Field(default_factory=list)
