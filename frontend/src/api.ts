// Клиент API. В dev-режиме пользователь передаётся заголовком X-Dev-User (переключатель ролей в шапке).

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

async function request<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  const dev = getDevUser();
  if (dev) headers["X-Dev-User"] = dev;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { method, headers, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined), credentials: "include" });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
  upload: <T>(url: string, file: File, extra?: Record<string, string>) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    return request<T>("POST", url, undefined, fd);
  },
};

/** Обозначение оборудования: торговая марка + модель (SHILI FL-0.5(C)); марка не дублируется, если уже входит в модель. */
export const designation = (p: { trademark?: string | null; model?: string | null }) => {
  const tm = (p.trademark || "").trim(), m = (p.model || "").trim();
  return !tm || m.toLowerCase().startsWith(tm.toLowerCase()) ? m || tm : `${tm} ${m}`;
};

export const fmtRub = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits }).format(v);
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2).replace(".", ",")} %`);
export const fmtDate = (s: string | null | undefined) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");
