// D-117: ошибки страницы в браузере уходят в «Ошибки системы» (администратор и ЗГД получают уведомление).
// Отправляются тип, сообщение, стек (только файлы приложения разбирает сервер) и адрес страницы без параметров.
// Одна и та же ошибка за сеанс — один раз; не больше 20 за сеанс; на странице ответа перевозчика (/q/…) не работает — там нет входа.
const sent = new Set<string>();
let total = 0;
const RELEASE = (import.meta.env.VITE_RELEASE as string | undefined) || "";

export function reportError(err: unknown, origin = "") {
  try {
    if (window.location.pathname.startsWith("/q/") || window.location.pathname.startsWith("/login")) return;
    const e = err instanceof Error ? err : new Error(typeof err === "string" ? err : JSON.stringify(err ?? "неизвестная ошибка").slice(0, 300));
    const msg = (origin ? origin + ": " : "") + (e.message || String(e));
    const key = `${e.name}|${msg.slice(0, 200)}`;
    if (sent.has(key) || total >= 20) return;
    sent.add(key); total += 1;
    void fetch("/api/admin/client-errors", {
      method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, keepalive: true,
      body: JSON.stringify({ kind: e.name || "Error", message: msg.slice(0, 2000), stack: (e.stack || "").slice(0, 8000),
        url: window.location.pathname.slice(0, 500), release: RELEASE }),
    }).catch(() => undefined);
  } catch { /* сбор ошибок не должен создавать новых */ }
}

export function installErrorReporter() {
  window.addEventListener("error", (ev) => {
    if (ev.error) reportError(ev.error);
    else if (ev.message && !/ResizeObserver loop/.test(ev.message)) reportError(new Error(ev.message));   // ResizeObserver — шум браузера
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const r = ev.reason;
    if (r && typeof r === "object" && "status" in r && typeof (r as { status: unknown }).status === "number") return;   // ответы API (4xx/5xx): 5xx записывает сервер
    reportError(r, "необработанный отказ");
  });
}
