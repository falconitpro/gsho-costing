export type RoleCode = "manager" | "logistician" | "service_head" | "cd" | "fd" | "gd" | "admin" | "observer" | "purchasing";

export interface Me {
  id: string;
  email: string;
  display_name: string;
  roles: { code: RoleCode; title: string }[];
  perms?: Record<string, number>;   // группа доступа → 0 не видит / 1 видит / 2 редактирует (задача 11)
}

/** Право по группе доступа (см. core/roles.py). */
export const can = (me: Me | null | undefined, group: string, level = 1) => !!me && (me.perms?.[group] ?? 0) >= level;

export const STATUS_TITLES: Record<string, string> = {
  draft: "Черновик", logistics_calc: "На расчёте логистики", service_calc: "На расчёте сервиса", calc_ready: "Расчёт сформирован",
  cd_approval: "На утверждении КД", fd_visa: "На визе ФД", gd_visa: "На визе ЗГД", rework: "На доработке",
  approved: "Утверждено", kp_sent: "КП отправлено", won: "Выиграна", lost: "Проиграна", cancelled: "Отменена",
  contract: "Заключение контракта", execution: "Исполнение контракта", closed: "Закрыта",
};

export const COST_ITEMS: [number, string, string][] = [
  [1, "factory_price", "Цена завода (с опциями)"], [2, "domestic_logistics", "Внутрикитайская логистика"],
  [3, "export_formalities", "Экспортные формальности КНР"], [4, "intl_freight", "Международная перевозка"],
  [5, "insurance", "Страхование"], [6, "customs_duty", "Таможенная пошлина"], [7, "import_vat", "НДС при ввозе"],
  [8, "broker_util", "Брокер, СВХ, утильсбор"], [9, "certification", "Сертификация и разрешения"], [10, "rf_delivery", "Доставка по РФ"],
  [11, "finance", "Финансовые расходы"], [12, "service", "Сервис до наработки"], [13, "warranty_reserve", "Гарантийные случаи (резерв)"],
  [14, "overhead", "Накладные компании"], [15, "local_completion", "Доукомплектование в РФ"],
  [16, "external_commission", "Внешняя комиссия (от цены клиента)"], [17, "manager_commission", "Комиссия менеджера"], [18, "cd_commission", "Комиссия КД"],
];

export const EVENT_TITLES: Record<string, string> = {
  contract: "подписание контракта", client_prepayment: "аванс клиента", lc_opened: "открытие аккредитива", factory_contract: "контракт с заводом",
  factory_prepayment: "предоплата заводу", readiness: "готовность на заводе", shipment: "отгрузка", border: "пересечение границы (CMR)",
  declaration: "выпуск ДТ", delivery: "поставка клиенту", commissioning: "ПНР", runtime: "наработка",
};
export const TERM_UNITS: Record<string, string> = { calendar_days: "календарных дней", working_days: "рабочих дней", weeks: "недель", date: "конкретная дата" };
export const PRODUCTION_START: Record<string, string> = { factory_contract: "с подписания контракта с заводом", factory_prepayment: "с предоплаты заводу" };
export const DELIVERY_START: Record<string, string> = { client_contract: "с подписания контракта", client_prepayment: "с аванса клиента", lc_opened: "с открытия аккредитива" };

export interface ProductionTerm { value?: number | null; unit?: string; fixed_date?: string | null; start_event?: string }
export interface DeliveryTerm { unit?: string; start_event?: string }

export interface TechParamRow {
  id?: string; no: number; code: string; name_ru: string; name_zh: string; unit: string; customer_requirement_ru: string; customer_requirement_zh?: string;
  value_ru: string; value_zh: string; comment_ru: string; comment_zh?: string; required: boolean; local_ru: boolean; source?: string; translation_status?: string;
  section?: string; req_status?: "match" | "mismatch" | "none";
}
export interface LocalOption {
  id: string; position_id: string; name: string; qty: number; supplier: string; cost: number; currency: string; supplier_lead_days: number; needs_install: boolean;
  install_stage: string; installer: string; install_days: number; install_cost_rub: number; status: string; comment: string; tech_param_id?: string | null;
}
export interface PositionImage {
  id: string; position_id: string; attachment_id: string; kind: string; source: string; caption: string; has_factory_logo: boolean; in_kp: boolean; kp_order: number;
  send_to_factory: boolean; file_name?: string; url?: string; is_image?: boolean;
}

export interface Position {
  id: string; factory_id: string; trademark?: string; model: string; machine_type: string; qty: number; unit_price: number; currency: string;
  options_price: number; options?: { code?: string; name?: string; value?: number | null }[]; basis: string; production_days: number; delivery_days: number; batch_id: string | null;
  production_term?: ProductionTerm; delivery_term?: DeliveryTerm; factory_payments?: PaymentLine[]; factory_payments_offer?: PaymentLine[];
  execution_id?: string | null; offer_id?: string | null;
  logistics?: Record<string, unknown>; service_calc?: Record<string, unknown>; price_reference?: Record<string, unknown>;
  logistics_params?: Record<string, { name: string; value: unknown; unit: string }>;
  tech_params?: Record<string, { name: string; value: unknown; unit: string }>;
  rfq_import_id?: string | null;
  tech_param_rows?: TechParamRow[]; local_options?: LocalOption[]; images?: PositionImage[];
}
export interface Variant {
  id: string; name: string; position_ids: string[]; price_rub?: number | null; service_in_kp: boolean; client_payments: PaymentLine[];
  overhead_pct_override?: number | null; is_default?: boolean; in_kp?: boolean;
  price_currency?: string | null; price_amount?: number | null; price_fx_rate?: number | null;
}
export interface PaymentLine { no: number; share_pct?: number; amount?: number; event: string; offset_days: number; offset_kind?: string; via?: string; guarantee?: string; guarantee_pct?: number; guarantee_months?: number }
export interface Batch { id: string; name: string; route: string; transport_units: number; transit_days: number; customs_days: number; rf_delivery_days?: number; border_planned?: string | null; border_actual?: string | null; allocation: string; items: Record<string, unknown> }

export interface Deal {
  id: string; number: string; client_id: string; client_name?: string; contact: string; site: string; region: string; requested_at: string; created_at?: string;
  kp_due: string | null; priority: string; status: string; manager_id: string; base_currency: string; service_flag: boolean;
  target_hours: number | null; client_payments: PaymentLine[]; client_payment_wishes?: string; bank_guarantee?: Record<string, unknown>;
  positions: Position[]; variants: Variant[]; batches: Batch[]; offer?: Variant | null; payment_terms_set?: boolean;
  checks?: string[]; check_items?: { group: string; tab: string; position_id: string | null; fields: string[]; tech: string[] }[]; report_date?: string | null; current_version_no: number; outcome?: string | null; outcome_reason?: string; status_detail?: string; archived_at?: string | null; archive_reason?: string; legal_review?: { status?: string; comment?: string; by?: string; at?: string } | null; contract_steps?: Record<string, unknown>; logistics_done: boolean; service_done: boolean;
  has_alternatives?: boolean; won_variant_id?: string | null; parent_deal_id?: string | null; request_group_id?: string | null;
  kp_brand?: string; kp_images_mode?: string; contract_number?: string; contract_signed_at?: string | null; factory_contract_signed_at?: string | null;
  in_execution?: boolean;
}

export interface CashGap { amount_rub: number; starts_on: string | null; days: number }
export interface CashflowPoint { on: string; inflow: number; outflow: number; balance: number }
export interface Sensitivity { margin_delta_pct_per_1pct_fx: number; margin_delta_rub_per_1pct_fx: number; breakeven_fx_factor: number | null; breakeven_rate_base_ccy: number | null }
export interface DeliveryTermResult { start_event: string; start_on: string; delivery_on: string; calc_days: number; unit: string; reserve_pct: number; with_reserve: number; promised: number; text: string; install_days: number }
export interface ScenarioResult {
  variant_id: string; scenario: string; items_rub: Record<string, number>; cost_total_rub: number; cost_total_base_ccy: number;
  price_rub: number | null; margin_pct: number | null; markup_pct: number | null; profit_rub: number | null; profit_before_finance_rub: number | null;
  cash_gap: CashGap; cashflow: CashflowPoint[]; finance_breakdown: Record<string, number>; delivery_days: number; sensitivity?: Sensitivity | null;
  payment_dates?: { no: number; event: string; on: string; amount_rub: number; via: string; default: boolean }[]; guarantee_cost_rub?: number;
  defaults_used?: Record<string, boolean>;
}
export interface FxComponent {
  payment_ref: string; currency: string; on: string; t_days: number; k_cbr: number; comp_a_forward: number; comp_b_trend: number; comp_c_season: number;
  season_spread: [number, number]; comp_g_expert: number | null; weights: [number, number, number]; forecast: number; dk_pct: Record<string, number>;
  rate_by_scenario: Record<string, number>; fixed: boolean;
}
export interface CalcResponse {
  fd_declined?: { comment: string; at: string | null; user: string } | null;
  input: { base_currency: string; calc_date?: string; fx: { as_of: string; rates: Record<string, number>; source: string; note?: string }; thresholds: { margin_threshold_pct: number } };
  result: { variants: Record<string, Record<string, ScenarioResult>>; fx_components: FxComponent[]; approval: { financial: boolean; general: boolean; reasons: string[] }; warnings: string[] };
  approval_flags: Record<string, boolean>; threshold_pct: number; warnings: string[]; comments: { from: string; item: string; ref: string; comment: string }[];
  factory_price_history?: { model: string; unit_price: number; currency: string; on: string }[];
  client_kp_history?: { deal: string; variant: string; price_rub: number; status: string; version_no?: number; on?: string }[]; status: string;
  cd_checks?: string[];
  terms?: Record<string, { position_id: string; model: string; delivery_term: DeliveryTermResult | null; events: Record<string, string> }[]>;
  payment_terms?: { client_payments: PaymentLine[]; client_payment_wishes: string; bank_guarantee: Record<string, unknown>; default_client: PaymentLine[]; default_factory: PaymentLine[];
    advance_guarantee_default: { enabled: boolean; mode: string; value: number };
    positions: { id: string; model: string; factory_payments: PaymentLine[]; factory_payments_offer: PaymentLine[]; delivery_term: DeliveryTerm; production_term: ProductionTerm }[] };
  events?: Record<string, string>; kp_brand?: string; kp_images_mode?: string;
}
export interface Task { id: string; deal_id: string; deal_number: string; title: string; role: string; block: string; due: string; overdue: boolean; deal_status: string }

export interface Stage {
  id: string; code: string; title: string; planned_on: string | null; actual_on: string | null; amount?: number | null; currency: string; actual_amount?: number | null;
  responsible_roles: string[]; kind: string; position_id: string | null; position: string | null; batch_id: string | null; batch: string | null; note: string; done: boolean; overdue: boolean; can_set: boolean;
  baseline_on?: string | null; refined_on?: string | null; refined_note?: string; deviation_days?: number | null;
}
