import { Fragment, useEffect, useState } from "react";
import { api, fmtRub } from "../api";
import type { Me } from "../types";
import { UndoButton, useUndoHistory } from "../undo";

type Opt = { value: string; label: string };
type Col = { key: string; label: string; type?: string; options?: Opt[]; nullable?: boolean };
type Meta = { key: string; group: string; title: string; unit?: string; help?: string; type: string; nullable?: boolean; options?: Opt[];
  fields?: Col[]; columns?: Col[]; key_label?: string; editable?: boolean; editors?: string };
type TplField = { code: string; name: string; section_no?: number; section?: string; is_l?: boolean };
type ExpertRow = { cur: string; on: string; rate: string };
const toExpertRows = (v: unknown): ExpertRow[] => Object.entries((v || {}) as Record<string, unknown>).flatMap(([cur, pts]) =>
  (Array.isArray(pts) ? pts as { on: string; rate: number }[] : Object.entries((pts || {}) as Record<string, number>).map(([on, rate]) => ({ on, rate })))
    .map((p) => ({ cur, on: String(p.on).slice(0, 10), rate: String(p.rate) })));
const fromExpertRows = (rows: ExpertRow[]) => {
  const out: Record<string, { on: string; rate: number }[]> = {};
  for (const r of rows) { const c = r.cur.trim().toUpperCase(); if (!c) continue; (out[c] ||= []).push({ on: r.on, rate: Number(String(r.rate).replace(",", ".")) || 0 }); }
  return out;
};
type Hist = { value: unknown; valid_from: string; valid_to: string | null; comment: string; changed_by?: string };

const num = (v: unknown) => (typeof v === "number" ? (Math.abs(v) >= 10000 ? fmtRub(v) : String(v).replace(".", ",")) : v == null ? "—" : String(v));

/** Короткая понятная запись значения для списка. */
function summary(m: Meta, v: unknown): string {
  if (m.type === "l_codes" && v == null) return "как в шаблоне RFQ";
  if (v == null || v === "") return m.nullable ? "не задано" : "—";
  switch (m.type) {
    case "bool": return v ? "да" : "нет";
    case "number": return `${num(v)}${m.unit ? " " + m.unit : ""}`;
    case "select": return m.options?.find((o) => o.value === v)?.label || String(v);
    case "object": {
      const o = v as Record<string, unknown>;
      return (m.fields || []).map((f) => {
        const x = o[f.key];
        const shown = f.type === "bool" ? (x ? "да" : "нет") : f.type === "select" ? f.options?.find((op) => op.value === x)?.label || String(x ?? "—") : num(x);
        return `${f.label}: ${shown}${!f.type && m.unit ? " " + m.unit : ""}`;
      }).join(" · ");
    }
    case "dict": return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k === "default" ? "по умолчанию" : k}: ${num(x)}${m.unit ? " " + m.unit : ""}`).join(" · ") || "—";
    case "table": {
      const rows = v as Record<string, unknown>[];
      if (m.key === "overhead_scale") return rows.map((r) => `${r.up_to_rub == null ? "свыше" : "до " + fmtRub(r.up_to_rub as number) + " ₽"} — ${num(r.pct)} %`).join("; ");
      if (m.key === "approval_ladder") {
        const role = (x: unknown) => m.columns?.find((c) => c.key === "role")?.options?.find((o) => o.value === x)?.label || String(x ?? "—");
        return rows.map((r) => `${r.name} — ${role(r.role)}, ${r.sla_days ?? "—"} раб. дн.`).join("; ");
      }
      return rows.map((r) => (m.columns || []).filter((c) => c.key !== "condition").map((c) => {
        const x = r[c.key];
        if (x == null || x === "" || x === 0 && c.key === "offset_days") return "";
        return c.type === "select" ? c.options?.find((o) => o.value === x)?.label || String(x) : c.key === "share_pct" ? `${x} %` : c.key === "offset_days" ? `+${x} дн.` : String(x);
      }).filter(Boolean).join(" ")).join("; ");
    }
    case "dates": return (v as string[]).length ? (v as string[]).join(", ") : "нет";
    case "dict_table": return Object.entries(v as Record<string, Record<string, unknown>>).map(([k, row]) =>
      `${k === "default" ? "остальные регионы" : k}: ${(m.columns || []).map((c) => `${c.label.split(",")[0].toLowerCase()} ${num(row?.[c.key])}`).join(", ")}`).join("; ") || "—";
    case "expert_points": { const rows = toExpertRows(v); return rows.length ? rows.map((r) => `${r.cur} на ${r.on}: ${r.rate.replace(".", ",")}`).join("; ") : "не заданы"; }
    case "l_codes": return Array.isArray(v) ? `${v.length} полей: ${(v as string[]).slice(0, 12).join(", ")}${v.length > 12 ? " …" : ""}` : "как в шаблоне RFQ";
    default: { const s = JSON.stringify(v); return s.length > 90 ? s.slice(0, 90) + "…" : s; }
  }
}

function Field({ col, value, onChange }: { col: Col; value: unknown; onChange: (v: unknown) => void }) {
  if (col.type === "bool") return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  if (col.type === "select") return <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}><option value="">—</option>{col.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>;
  if (col.type === "text") return <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  return <input type="number" step="any" value={value == null ? "" : String(value)} style={{ width: 120 }} placeholder={col.nullable ? "пусто — без ограничения" : ""}
    onChange={(e) => onChange(e.target.value === "" ? (col.nullable ? null : 0) : Number(e.target.value))} />;
}

/** Поля опросника для логиста (метка L): отметки по разделам шаблона RFQ. */
function LCodesEditor({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const [fields, setFields] = useState<TplField[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { api.get<{ fields: TplField[] }>("/api/deals/rfq/template").then((r) => {
    setFields(r.fields);
    if (!Array.isArray(value)) onChange(r.fields.filter((f) => f.is_l).map((f) => f.code));   // старт — отметки шаблона
  }); }, []);
  const sel = new Set(Array.isArray(value) ? value as string[] : []);
  const toggle = (code: string) => { const n = new Set(sel); if (n.has(code)) n.delete(code); else n.add(code); onChange([...n]); };
  const bySec = new Map<number, TplField[]>();
  for (const f of fields) { if (q && !`${f.code} ${f.name}`.toLowerCase().includes(q.toLowerCase())) continue; const n = f.section_no ?? 0; if (!bySec.has(n)) bySec.set(n, []); bySec.get(n)!.push(f); }
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}><input placeholder="поиск поля" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
        <span className="muted">отмечено {sel.size} из {fields.length}</span></div>
      <div style={{ maxHeight: 420, overflowY: "auto", border: "1px solid #eee", borderRadius: 4 }}>
        <table><tbody>{[...bySec.entries()].sort((a, b) => a[0] - b[0]).map(([no, fs]) => (
          <Fragment key={no}>
            <tr><td colSpan={2} style={{ background: "var(--accent-soft)", fontWeight: 600 }}>{no}. {(fs[0].section || "").split(" / ")[0]}
              <button className="btn ghost" style={{ marginLeft: 8, padding: "0 6px" }} onClick={() => { const n = new Set(sel); const all = fs.every((f) => n.has(f.code)); fs.forEach((f) => (all ? n.delete(f.code) : n.add(f.code))); onChange([...n]); }}>
                {fs.every((f) => sel.has(f.code)) ? "снять все" : "отметить все"}</button></td></tr>
            {fs.map((f) => <tr key={f.code} style={{ cursor: "pointer" }} onClick={() => toggle(f.code)}>
              <td style={{ width: 30 }}><input type="checkbox" checked={sel.has(f.code)} readOnly /></td>
              <td>{f.code} {f.name} {sel.has(f.code) && <span className="badge">L</span>}</td></tr>)}
          </Fragment>))}</tbody></table>
      </div>
    </div>);
}

/** Редактор значения по типу настройки. */
function Editor({ m, value, onChange }: { m: Meta; value: unknown; onChange: (v: unknown) => void }) {
  const [raw, setRaw] = useState(JSON.stringify(value, null, 2));
  const [bad, setBad] = useState(false);
  switch (m.type) {
    case "number": return <div className="row"><Field col={{ key: m.key, label: m.title, nullable: m.nullable }} value={value} onChange={onChange} />{m.unit && <span className="muted">{m.unit}</span>}</div>;
    case "bool": return <label className="row"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> {value ? "да" : "нет"}</label>;
    case "select": return <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>{m.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>;
    case "object": {
      const o = (value || {}) as Record<string, unknown>;
      return <table><tbody>{(m.fields || []).map((f) => <tr key={f.key}><td>{f.label}</td><td><Field col={f} value={o[f.key]} onChange={(x) => onChange({ ...o, [f.key]: x })} /> {!f.type && m.unit && <span className="muted">{m.unit}</span>}</td></tr>)}</tbody></table>;
    }
    case "dict": {
      const entries = Object.entries((value || {}) as Record<string, unknown>);
      const put = (list: [string, unknown][]) => onChange(Object.fromEntries(list.filter(([k]) => k !== "")));
      return <div><table><thead><tr><th>{m.key_label || "Ключ"}</th><th>Значение{m.unit ? `, ${m.unit}` : ""}</th><th /></tr></thead><tbody>
        {entries.map(([k, x], i) => <tr key={i}><td><input value={k} onChange={(e) => put(entries.map((en, j) => (j === i ? [e.target.value, en[1]] : en)))} /></td>
          <td><Field col={{ key: k, label: k }} value={x} onChange={(nv) => put(entries.map((en, j) => (j === i ? [en[0], nv] : en)))} /></td>
          <td><button className="btn ghost" onClick={() => put(entries.filter((_, j) => j !== i))}>×</button></td></tr>)}</tbody></table>
        <button className="btn ghost" onClick={() => put([...entries, [`новый_${entries.length + 1}`, 0]])}>+ строка</button></div>;
    }
    case "table": {
      const rows = ((value || []) as Record<string, unknown>[]);
      const put = (list: Record<string, unknown>[]) => onChange(list.map((r, i) => ("no" in (rows[0] || {}) || m.key.includes("scheme") ? { ...r, no: i + 1 } : r)));
      return <div style={{ overflowX: "auto" }}><table><thead><tr>{(m.columns || []).map((c) => <th key={c.key}>{c.label}</th>)}<th /></tr></thead><tbody>
        {rows.map((r, i) => <tr key={i}>{(m.columns || []).map((c) => <td key={c.key}><Field col={c} value={r[c.key]} onChange={(x) => put(rows.map((rr, j) => (j === i ? { ...rr, [c.key]: x } : rr)))} /></td>)}
          <td><button className="btn ghost" onClick={() => put(rows.filter((_, j) => j !== i))}>×</button></td></tr>)}</tbody></table>
        <button className="btn ghost" onClick={() => put([...rows, {}])}>+ строка</button></div>;
    }
    case "dict_table": {
      const entries = Object.entries((value || {}) as Record<string, Record<string, unknown>>);
      const put = (list: [string, Record<string, unknown>][]) => onChange(Object.fromEntries(list.filter(([k]) => k !== "")));
      return <div style={{ overflowX: "auto" }}><table><thead><tr><th>{m.key_label || "Ключ"}</th>{(m.columns || []).map((c) => <th key={c.key}>{c.label}</th>)}<th /></tr></thead><tbody>
        {entries.map(([k, row], i) => <tr key={i}><td><input value={k} placeholder="регион" onChange={(e) => put(entries.map((en, j) => (j === i ? [e.target.value, en[1]] : en)))} />
          {k === "default" && <div className="muted" style={{ fontSize: 11 }}>для остальных регионов</div>}</td>
          {(m.columns || []).map((c) => <td key={c.key}><Field col={c} value={row?.[c.key]} onChange={(x) => put(entries.map((en, j) => (j === i ? [en[0], { ...en[1], [c.key]: x }] : en)))} /></td>)}
          <td>{k !== "default" && <button className="btn ghost" onClick={() => put(entries.filter((_, j) => j !== i))}>×</button>}</td></tr>)}</tbody></table>
        <button className="btn ghost" onClick={() => put([...entries, [`Регион ${entries.length}`, { ...(entries.find(([k]) => k === "default")?.[1] || {}) }]])}>+ регион</button></div>;
    }
    case "expert_points": {
      const rows = toExpertRows(value);
      const put = (list: ExpertRow[]) => onChange(fromExpertRows(list));
      return <div><table><thead><tr><th>Валюта</th><th>Дата</th><th>Ожидаемый курс, ₽ за единицу</th><th /></tr></thead><tbody>
        {rows.map((r, i) => <tr key={i}>
          <td><select value={r.cur} onChange={(e) => put(rows.map((x, j) => (j === i ? { ...x, cur: e.target.value } : x)))}>{["CNY", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select></td>
          <td><input type="date" value={r.on} onChange={(e) => put(rows.map((x, j) => (j === i ? { ...x, on: e.target.value } : x)))} /></td>
          <td><input inputMode="decimal" value={r.rate} style={{ width: 120 }} onChange={(e) => put(rows.map((x, j) => (j === i ? { ...x, rate: e.target.value } : x)))} /></td>
          <td><button className="btn ghost" onClick={() => put(rows.filter((_, j) => j !== i))}>×</button></td></tr>)}</tbody></table>
        <button className="btn ghost" onClick={() => put([...rows, { cur: "CNY", on: new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10), rate: "" }])}>+ точка</button></div>;
    }
    case "l_codes": return <LCodesEditor value={value} onChange={onChange} />;
    case "dates": return <textarea rows={5} style={{ width: "100%" }} value={((value || []) as string[]).join("\n")} placeholder="2027-01-02"
      onChange={(e) => onChange(e.target.value.split(/\s+/).map((x) => x.trim()).filter(Boolean))} />;
    default: return <div><textarea rows={8} style={{ width: "100%", fontFamily: "monospace", fontSize: 12, borderColor: bad ? "var(--bad)" : undefined }} value={raw}
      onChange={(e) => { setRaw(e.target.value); try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); } }} />
      {bad && <div className="badge bad">ошибка в формате JSON — значение не сохранится</div>}</div>;
  }
}

/** Ставки и нормативы: группы, понятные названия и пояснения, поля по типу значения; изменения — с комментарием и историей. */
export default function SettingsPage({ me }: { me: Me }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [meta, setMeta] = useState<{ groups: string[]; items: Record<string, Meta> } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [draft, setDraft] = useState<unknown>(null);
  const [comment, setComment] = useState("");
  const [history, setHistory] = useState<Hist[]>([]);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const undoH = useUndoHistory({ draft, comment }, (st) => { setDraft(st.draft); setComment(st.comment); }, open);
  const load = () => api.get<Record<string, unknown>>("/api/admin/settings").then(setValues);
  useEffect(() => { load(); api.get<typeof meta>("/api/admin/settings/meta").then(setMeta); }, []);
  if (!meta) return <div className="muted">Загрузка…</div>;
  function start(key: string) {
    if (open === key) { setOpen(null); return; }
    setOpen(key); setDraft(values[key]); setComment(""); setMsg("");
    api.get<Hist[]>(`/api/admin/settings/${key}/history`).then(setHistory).catch(() => setHistory([]));
  }
  async function save(key: string) {
    try { await api.put(`/api/admin/settings/${key}`, { value: draft, comment }); setMsg(`Сохранено: ${meta!.items[key].title}`); setOpen(null); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const match = (m: Meta) => !q || `${m.title} ${m.help || ""} ${m.group}`.toLowerCase().includes(q.toLowerCase());
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Настройки: ставки и нормативы</h1>
        <input placeholder="поиск настройки" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
      </div>
      <p className="muted">Значения применяются к новым расчётам; каждое изменение сохраняется в истории с комментарием. ФД меняет пороги, сценарии и стоимость денег; КД — внешнюю комиссию, комиссию менеджера и поля для логиста; остальное — ГД или администратор. Ваши роли: {me.roles.map((r) => r.title).join(", ")}.</p>
      {msg && <div className="alert info">{msg}</div>}
      {meta.groups.map((g) => {
        const items = Object.values(meta.items).filter((m) => m.group === g && match(m));
        if (!items.length) return null;
        return (
          <div className="card" key={g}>
            <h2 style={{ marginTop: 0 }}>{g}</h2>
            <table><tbody>{items.map((m) => (
              <Fragment key={m.key}>
                <tr style={{ cursor: m.editable === false ? "default" : "pointer", background: open === m.key ? "var(--accent-soft)" : undefined }} onClick={() => m.editable !== false && start(m.key)}>
                  <td style={{ width: "38%" }}><b>{m.title}</b>{m.help && <div className="muted" style={{ fontSize: 12 }}>{m.help}</div>}</td>
                  <td>{summary(m, values[m.key])}</td>
                  <td style={{ width: 110, textAlign: "right" }}>{m.editable === false
                    ? <span className="muted" style={{ fontSize: 11 }} title="кто может изменить">меняет {m.editors}</span>
                    : <button className="btn ghost" onClick={(e) => { e.stopPropagation(); start(m.key); }}>{open === m.key ? "закрыть" : "изменить"}</button>}</td>
                </tr>
                {open === m.key && (
                  <tr><td colSpan={3} style={{ background: "#fafafa" }}>
                    <Editor m={m} value={draft} onChange={setDraft} />
                    <div className="row" style={{ marginTop: 8 }}>
                      <input style={{ flex: 1 }} placeholder="Комментарий к изменению (почему меняем)" value={comment} onChange={(e) => setComment(e.target.value)} />
                      <button className="btn" onClick={() => save(m.key)}>Сохранить</button><UndoButton h={undoH} /><button className="btn secondary" onClick={() => setOpen(null)}>Закрыть без сохранения</button>
                    </div>
                    {history.length > 0 && (
                      <details style={{ marginTop: 8 }}><summary className="muted" style={{ cursor: "pointer" }}>История изменений ({history.length})</summary>
                        <table><tbody>{history.map((h, i) => <tr key={i}><td className="muted">{new Date(h.valid_from).toLocaleString("ru-RU")}</td><td>{summary(m, h.value)}</td><td className="muted">{h.comment}</td></tr>)}</tbody></table></details>)}
                  </td></tr>)}
              </Fragment>
            ))}</tbody></table>
          </div>
        );
      })}
    </>
  );
}
