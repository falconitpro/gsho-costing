// Клиент API. На тестовом стенде переключение учётной записи — заголовком X-Dev-User на один запрос (переключатель в шапке); сессию дальше несёт cookie.

const DEV_USER_KEY = "gsho.devUser";

export function getDevUser(): string {
  return localStorage.getItem(DEV_USER_KEY) || "";
}
export function setDevUser(email: string) {
  localStorage.setItem(DEV_USER_KEY, email);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { message?: string })?.message || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** Подтверждение опасного действия (step-up, docs/operations/04): диалог регистрирует обработчик; запрос повторяется один раз после успеха. */
let stepUpHandler: (() => Promise<boolean>) | null = null;
export function setStepUpHandler(h: (() => Promise<boolean>) | null) { stepUpHandler = h; }
const detailCode = (d: unknown) => (d && typeof d === "object" ? (d as { code?: string }).code : undefined);

async function request<T>(method: string, url: string, body?: unknown, form?: FormData, extra?: Record<string, string>, retried = false): Promise<T> {
  const headers: Record<string, string> = { ...(extra || {}) };
  const dev = getDevUser();
  if (dev) headers["X-Dev-User"] = dev;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined), credentials: "include" });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (res.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new CustomEvent("gsho:unauthorized"));   // истёкшая сессия → страница входа
  if (res.status === 403 && !retried) {
    const code = detailCode(data?.detail);
    if (code === "step_up_required" && stepUpHandler && (await stepUpHandler())) return request<T>(method, url, body, form, extra, true);
    if (code === "password_change_required" || code === "mfa_setup_required") window.dispatchEvent(new CustomEvent("gsho:security-gate"));
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  /** Фоновый опрос (колокольчик, курсы, прогресс): сервер не считает его активностью, иначе открытая вкладка не давала бы выйти по бездействию (D-041). */
  bg: <T>(url: string) => request<T>("GET", url, undefined, undefined, { "X-Background-Request": "1" }),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
  /** POST с ключом идемпотентности (раздел «Финансы»: повтор запроса с тем же ключом не создаёт дубль). */
  postIdem: <T>(url: string, body: unknown, key: string) => request<T>("POST", url, body, undefined, { "Idempotency-Key": key }),
  upload: <T>(url: string, file: File, extra?: Record<string, string>) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    return request<T>("POST", url, undefined, fd);
  },
  /** Скачать файл (экспорт XLSX): те же заголовки, что у запросов API; ошибка сервера — ApiError с его текстом. */
  download: async (url: string, fallbackName = "export.xlsx") => {
    const headers: Record<string, string> = {};
    const dev = getDevUser();
    if (dev) headers["X-Dev-User"] = dev;
    const res = await fetch(url, { headers, credentials: "include" });
    if (!res.ok) { const d = await res.json().catch(() => null); throw new ApiError(res.status, d?.detail ?? d); }
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
    const name = m ? decodeURIComponent(m[1]) : fallbackName;
    const href = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    return name;
  },
};

/** Обозначение оборудования: торговая марка + модель (SHILI FL-0.5(C)); марка не дублируется, если уже входит в модель. */
export const designation = (p: { trademark?: string | null; model?: string | null }) => {
  const tm = (p.trademark || "").trim(), m = (p.model || "").trim();
  return !tm || m.toLowerCase().startsWith(tm.toLowerCase()) ? m || tm : `${tm} ${m}`;
};

/** Только русская часть подписи: «1. Общие данные / 总体参数» → «1. Общие данные»; чисто китайский текст — пусто. */
const CJK = /[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]/;
export const ru = (t: unknown) => {
  const s = String(t ?? "").trim();
  if (!CJK.test(s)) return s;
  return s.split(/\s*[/|]\s*/).filter((x) => x && !CJK.test(x)).join(" / ").replace(/[\u3000-\u303f\u3400-\u9fff\uff00-\uffef（）]+/g, "").trim();
};

export const fmtRub = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(v);
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2).replace(".", ",")} %`);
export const fmtDate = (s: string | null | undefined) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");

/** Сумма в исходной валюте из строки-decimal без потери точности: «1 250 000,50 CNY»; null → «—». */
export const fmtMoney = (amount: string | number | null | undefined, currency?: string) => {
  if (amount == null || amount === "") return "—";
  const raw = String(amount);
  const neg = raw.startsWith("-");
  const [int, frac = ""] = raw.replace("-", "").split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  return `${neg ? "−" : ""}${grouped},${(frac + "00").slice(0, 2)}${currency ? `\u00a0${currency}` : ""}`;
};
/** Ключ идемпотентности для одной попытки действия. */
export const newKey = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`);
