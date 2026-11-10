import { useState } from "react";
import { api, ApiError } from "../api";

/** «Выгрузить в Excel»: тот же отчёт, что на экране, с текущими фильтрами (D-145). Права проверяет сервер. */
export default function FinanceExportButton({ kind, params = {}, label = "Excel" }: { kind: string; params?: Record<string, string | null | undefined>; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function go() {
    setBusy(true); setErr("");
    const q = new URLSearchParams({ kind });
    Object.entries(params).forEach(([k, v]) => { if (v) q.set(k, v); });
    try { await api.download(`/api/finance/export?${q}`); }
    catch (e) { setErr(e instanceof ApiError && typeof e.detail === "string" ? e.detail : (e as Error).message); }
    finally { setBusy(false); }
  }
  return <span className="row" style={{ gap: 6 }}>
    <button className="btn secondary" disabled={busy} onClick={go} title="Выгрузка XLSX с текущими фильтрами, датой и пояснениями">{busy ? "Готовлю…" : `⇩ ${label}`}</button>
    {err && <span className="muted" style={{ color: "var(--bad)", fontSize: 12 }}>{err}</span>}
  </span>;
}
