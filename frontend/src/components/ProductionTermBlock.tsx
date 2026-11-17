import { useState } from "react";
import { api, fmtDate } from "../api";
import { PRODUCTION_START, TERM_UNITS } from "../types";

export type ProductionInfo = {
  model: string; text: string; missing: boolean; source: string; source_title: string; set_by_name: string; set_at: string | null;
  comment: string; fixed: boolean; calendar_days: number;
  term: { value?: number; unit?: string; fixed_date?: string | null; start_event?: string };
};
type Hist = { at: string; user: string; action: string; before: string; after: string; comment: string };
type Ev = { readiness?: string; border?: string; delivery?: string };

/** Срок производства по позициям (D-065): какое значение использует расчёт, откуда оно, правка КД / ЗГД с причиной и история. */
export default function ProductionTermBlock({ dealId, production, events, canEdit, onSaved }:
  { dealId: string; production: Record<string, ProductionInfo>; events: Record<string, Ev>; canEdit: boolean; onSaved: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [form, setForm] = useState({ value: "", unit: "calendar_days", fixed_date: "", start_event: "factory_contract", comment: "" });
  const [hist, setHist] = useState<Record<string, Hist[]>>({});
  const [err, setErr] = useState("");

  const start = (pid: string) => {
    const t = production[pid].term || {};
    setForm({ value: t.value != null ? String(t.value) : "", unit: t.unit || "calendar_days", fixed_date: t.fixed_date || "", start_event: t.start_event || "factory_contract", comment: "" });
    setEdit(pid); setErr("");
  };
  async function save(pid: string) {
    try {
      await api.put(`/api/deals/${dealId}/positions/${pid}/production-term`, {
        value: form.unit === "date" ? null : Number(form.value.replace(",", ".")), unit: form.unit, fixed_date: form.unit === "date" ? form.fixed_date || null : null,
        start_event: form.start_event, comment: form.comment });
      setEdit(null); setErr(""); setHist((h) => { const n = { ...h }; delete n[pid]; return n; }); onSaved();
    } catch (e) { setErr((e as Error).message); }
  }
  const toggleHist = (pid: string) => {
    if (hist[pid]) { const n = { ...hist }; delete n[pid]; setHist(n); return; }
    api.get<{ history: Hist[] }>(`/api/deals/${dealId}/positions/${pid}/production-term/history`).then((r) => setHist((h) => ({ ...h, [pid]: r.history }))).catch((e) => setErr(e.message));
  };

  return (
    <div style={{ marginTop: 10 }}>
      <b>Срок производства</b> <span className="muted">— от него считаются готовность, отгрузка, граница, таможня, доставка, ПНР и все платежи по этим событиям</span>
      {err && <div className="alert error">{err}</div>}
      <table style={{ marginTop: 4 }}>
        <thead><tr><th>Позиция</th><th>Срок в расчёте</th><th className="num">≈ кал. дн.</th><th>Откуда</th><th>Готовность</th><th>CMR</th><th>Поставка</th><th /></tr></thead>
        <tbody>{Object.entries(production).map(([pid, p]) => (
          <tr key={pid}>
            <td>{p.model}</td>
            <td>{p.missing ? <span className="badge warn">{p.text}</span> : <b>{p.text}</b>}{p.fixed && <span className="badge ok" style={{ marginLeft: 6 }}>зафиксирован</span>}
              {edit === pid && (
                <div className="row" style={{ marginTop: 6, flexWrap: "wrap", gap: 6 }}>
                  {form.unit !== "date"
                    ? <input type="number" min="0" step="any" value={form.value} style={{ width: 80 }} onChange={(e) => setForm({ ...form, value: e.target.value })} />
                    : <input type="date" value={form.fixed_date} onChange={(e) => setForm({ ...form, fixed_date: e.target.value })} />}
                  <select value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}>{Object.entries(TERM_UNITS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                  {form.unit !== "date" && <select value={form.start_event} onChange={(e) => setForm({ ...form, start_event: e.target.value })}>{Object.entries(PRODUCTION_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>}
                  <input value={form.comment} placeholder="причина изменения (обязательно)" style={{ minWidth: 240 }} onChange={(e) => setForm({ ...form, comment: e.target.value })} />
                  <button className="btn" disabled={!form.comment.trim()} onClick={() => save(pid)}>Сохранить</button>
                  <button className="btn ghost" onClick={() => setEdit(null)}>Отмена</button>
                </div>)}
              {hist[pid] && (
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{hist[pid].length === 0 ? "Изменений не было." : hist[pid].map((h, i) => (
                  <div key={i}>{fmtDate(h.at)} {h.at.slice(11, 16)} · {h.user || "система"}: {h.action === "production_term_kept"
                    ? <>{h.comment}</> : <>{h.before || "—"} → <b>{h.after}</b>{h.comment ? ` (${h.comment})` : ""}</>}</div>))}</div>)}
            </td>
            <td className="num">{p.calendar_days}</td>
            <td className="muted">{p.source_title || "—"}{p.set_by_name ? `, ${p.set_by_name}` : ""}{p.set_at ? `, ${fmtDate(p.set_at)}` : ""}{p.comment ? <div>{p.comment}</div> : null}</td>
            <td>{fmtDate(events[pid]?.readiness)}</td><td>{fmtDate(events[pid]?.border)}</td><td>{fmtDate(events[pid]?.delivery)}</td>
            <td style={{ whiteSpace: "nowrap" }}>{canEdit && edit !== pid && <button className="btn ghost" onClick={() => start(pid)}>изменить</button>}
              <button className="btn ghost" onClick={() => toggleHist(pid)}>{hist[pid] ? "скрыть историю" : "история"}</button></td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}
