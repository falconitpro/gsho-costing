export type RoleCode = "manager" | "logistician" | "service_head" | "cd" | "fd" | "gd" | "admin" | "observer";

export interface Me {
  id: string;
  email: string;
  display_name: string;
  roles: { code: RoleCode; title: string }[];
}

export const STATUS_TITLES: Record<string, string> = {
  draft: "Черновик", logistics_calc: "На расчёте логистики", service_calc: "На расчёте сервиса", calc_ready: "Расчёт сформирован",
  cd_approval: "На утверждении КД", fd_visa: "На визе финдиректора", gd_visa: "На визе гендиректора", rework: "На доработке",
  approved: "Утверждено", kp_sent: "КП отправлено", won: "Выиграна", lost: "Проиграна", cancelled: "Отменена",
};

export const COST_ITEMS: [number, string, string][] = [
  [1, "factory_price", "Цена завода (с опциями)"], [2, "domestic_logistics", "Внутрикитайская логистика"],
  [3, "export_formalities", "Экспортные формальности КНР"], [4, "intl_freight", "Международная перевозка"],
  [5, "insurance", "Страхование"], [6, "customs_duty", "Таможенная пошлина"], [7, "import_vat", "НДС при ввозе"],
  [8, "broker_util", "Брокер, СВХ, утильсбор"], [9, "certification", "Сертификация и разрешения"], [10, "rf_delivery", "Доставка по РФ"],
  [11, "finance", "Финансовые расходы"], [12, "service", "Сервис до наработки"], [13, "warranty_reserve", "Гарантийный резерв"],
  [14, "overhead", "Накладные компании"],
];

export interface Position {
  id: string; factory_id: string; model: string; machine_type: string; qty: number; unit_price: number; currency: string;
  options_price: number; basis: string; production_days: number; delivery_days: number; batch_id: string | null;
  logistics?: Record<string, unknown>; service_calc?: Record<string, unknown>; price_reference?: Record<string, unknown>;
  logistics_params?: Record<string, { name: string; value: unknown; unit: string }>;
}
export interface Variant {
  id: string; name: string; position_ids: string[]; price_rub?: number | null; service_in_kp: boolean; client_payments: PaymentLine[];
  overhead_pct_override?: number | null;
}
export interface PaymentLine { no: number; share_pct?: number; amount?: number; event: string; offset_days: number; guarantee?: string; guarantee_pct?: number; guarantee_months?: number }
export interface Batch { id: string; name: string; route: string; transport_units: number; transit_days: number; customs_days: number; allocation: string; items: Record<string, unknown> }

export interface Deal {
  id: string; number: string; client_id: string; client_name?: string; contact: string; site: string; region: string; requested_at: string;
  kp_due: string | null; priority: string; status: string; manager_id: string; base_currency: string; service_flag: boolean;
  target_hours: number | null; client_payments: PaymentLine[]; positions: Position[]; variants: Variant[]; batches: Batch[];
  checks?: string[]; current_version_no: number; outcome?: string | null; logistics_done: boolean; service_done: boolean;
}

export interface CashGap { amount_rub: number; starts_on: string | null; days: number }
export interface CashflowPoint { on: string; inflow: number; outflow: number; balance: number }
export interface Sensitivity { margin_delta_pct_per_1pct_fx: number; margin_delta_rub_per_1pct_fx: number; breakeven_fx_factor: number | null; breakeven_rate_base_ccy: number | null }
export interface ScenarioResult {
  variant_id: string; scenario: string; items_rub: Record<string, number>; cost_total_rub: number; cost_total_base_ccy: number;
  price_rub: number | null; margin_pct: number | null; markup_pct: number | null; profit_rub: number | null; profit_before_finance_rub: number | null;
  cash_gap: CashGap; cashflow: CashflowPoint[]; finance_breakdown: Record<string, number>; delivery_days: number; sensitivity?: Sensitivity | null;
}
export interface FxComponent {
  payment_ref: string; currency: string; on: string; t_days: number; k_cbr: number; comp_a_forward: number; comp_b_trend: number; comp_c_season: number;
  season_spread: [number, number]; comp_g_expert: number | null; weights: [number, number, number]; forecast: number; dk_pct: Record<string, number>;
  rate_by_scenario: Record<string, number>; fixed: boolean;
}
export interface CalcResponse {
  input: { base_currency: string; fx: { as_of: string; rates: Record<string, number>; source: string }; thresholds: { margin_threshold_pct: number } };
  result: { variants: Record<string, Record<string, ScenarioResult>>; fx_components: FxComponent[]; approval: { financial: boolean; general: boolean; reasons: string[] }; warnings: string[] };
  approval_flags: Record<string, boolean>; threshold_pct: number; warnings: string[]; comments: { from: string; item: string; ref: string; comment: string }[];
  factory_price_history: { model: string; unit_price: number; currency: string; on: string }[];
  client_kp_history: { deal: string; variant: string; price_rub: number; status: string }[]; status: string;
}
export interface Task { id: string; deal_id: string; deal_number: string; title: string; role: string; block: string; due: string; overdue: boolean; deal_status: string }
