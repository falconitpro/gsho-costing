import { useEffect, useState } from "react";
import { UndoButton, useUndoHistory } from "../undo";
import { api, designation } from "../api";
import { TERM_UNITS } from "../types";

type Row = { no?: number; code: string; name_ru: string; unit: string; value_ru: string; section: string; required?: boolean };
type Factory = { id: string; name: string; trademarks?: string[] };
type RfqPreview = { kind: "rfq"; item: Record<string, unknown> & { tech_params: Row[] }; messages: { severity: string; message: string }[]; filled: number; total: number };
type ListPreview = { kind: "list"; sheets: { name: string; rows: unknown[][]; total_rows: number; mapping: { header_row: number; first_row: number; columns: Record<string, number> } }[];
  columns: { key: string; title: string }[] };

const EMPTY = { factory_id: "", factory_name: "", factory_address: "", machine_type: "", trademark: "", model: "", execution_name: "", brand_name: "",
  unit_price: "", currency: "CNY", basis: "FCA", term_value: "", term_unit: "calendar_days", comment: "" };

/** Задача: оборудование прямо в каталог — вручную или из файла (опросник RFQ одной машины или список моделей).
 *  Вносят менеджер, КД и ГД; запись менеджера — на подтверждение КД или ГД. */
export default function CatalogAdd({ editor, onAdded }: { editor: boolean; onAdded: (msg: string) => void }) {
  const [form, setForm] = useState({ ...EMPTY });
  const [rows, setRows] = useState<Row[]>([]);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [types, setTypes] = useState<{ id: string; name: string }[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [list, setList] = useState<ListPreview | null>(null);
  const [sheet, setSheet] = useState(0);
  const [mapping, setMapping] = useState<{ header_row: number; first_row: number; columns: Record<string, string> }>({ header_row: 0, first_row: 1, columns: {} });
  const [sel, setSel] = useState<Set<number> | null>(null);
  const [listFactory, setListFactory] = useState("");
  const [notes, setNotes] = useState<{ severity: string; message: string }[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const history = useUndoHistory({ form, rows }, (st) => { setForm(st.form); setRows(st.rows); });

  const loadTemplate = (mtId = "") => api.get<{ rows: { no: number; code: string; name_ru: string; unit: string; section: string; required: boolean }[] }>(`/api/refs/tech-param-templates?machine_type_id=${mtId}`)
    .then((r) => { history.accept(); setRows(r.rows.map((t) => ({ ...t, value_ru: "", section: t.section && !/^\d+\./.test(t.section) ? `${t.code.split(".")[0]}. ${t.section}` : t.section || "Дополнительные параметры" }))); });
  useEffect(() => {
    api.get<Factory[]>("/api/refs/factories").then(setFactories);
    api.get<typeof types>("/api/refs/machine-types").then(setTypes);
    loadTemplate();
  }, []);
  const set = (p: Partial<typeof EMPTY>) => setForm((f) => ({ ...f, ...p }));

  async function preview() {
    if (!file) return;
    setBusy(true); setErr(""); setList(null); setNotes([]);
    try {
      const p = await api.upload<RfqPreview | ListPreview>("/api/catalog/equipment/import/preview", file);
      if (p.kind === "rfq") {
        const it = p.item as Record<string, unknown>;
        const term = (it.production_term || {}) as { value?: number; unit?: string };
        const fid = factories.find((f) => f.name === it.factory_name)?.id || "";
        setForm({ ...EMPTY, factory_id: fid, factory_name: fid ? "" : String(it.factory_name || ""), factory_address: String(it.factory_address || ""),
          machine_type: String(it.machine_type || ""), trademark: factories.find((f) => f.id === fid)?.trademarks?.[0] || "", model: String(it.model || ""), unit_price: it.unit_price != null ? String(it.unit_price) : "",
          currency: String(it.currency || "CNY"), basis: String(it.basis || "FCA"), term_value: term.value != null ? String(term.value) : "", term_unit: term.unit || "calendar_days" });
        setRows(p.item.tech_params);
        setNotes(p.messages.filter((m) => m.severity !== "info"));
        setErr(""); onAdded(`Файл распознан как опросник RFQ: заполнено ${p.filled} из ${p.total} параметров. Проверьте данные и нажмите «Добавить в каталог».`);
      } else {
        setList(p); setSheet(0); const m = p.sheets[0].mapping;
        setMapping({ header_row: m.header_row, first_row: m.first_row, columns: Object.fromEntries(Object.entries(m.columns).map(([k, v]) => [k, String(v)])) }); setSel(null);
      }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function applyList() {
    if (!file || !list) return;
    setBusy(true);
    try {
      const mp = { sheet: list.sheets[sheet].name, header_row: mapping.header_row, first_row: mapping.first_row, selected_rows: sel ? [...sel] : undefined,
        columns: Object.fromEntries(Object.entries(mapping.columns).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])) };
      const r = await api.upload<{ message: string; errors: { row: number; message: string }[] }>("/api/catalog/equipment/import/apply", file,
        { mapping: JSON.stringify(mp), default_factory_id: listFactory });
      onAdded(r.message + (r.errors.length ? `. Ошибки в строках: ${r.errors.map((e) => `${e.row} — ${e.message}`).join("; ")}` : ""));
      setList(null); setFile(null);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function add() {
    setBusy(true); setErr("");
    try {
      const r = await api.post<{ message: string }>("/api/catalog/equipment", {
        factory_id: form.factory_id || null, factory_name: form.factory_name, factory_address: form.factory_address, machine_type: form.machine_type, trademark: form.trademark, model: form.model,
        execution_name: form.execution_name, brand_name: form.brand_name, unit_price: form.unit_price ? Number(form.unit_price.replace(/\s/g, "").replace(",", ".")) : null,
        currency: form.currency, basis: form.basis, comment: form.comment,
        production_term: form.term_value ? { value: Number(form.term_value), unit: form.term_unit } : {},
        tech_params: rows.filter((r) => r.name_ru.trim()).map((r, i) => ({ ...r, no: i + 1 })),
      });
      onAdded(`${designation(form)}: ${r.message}`); setForm({ ...EMPTY }); loadTemplate(); setNotes([]); setFile(null);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const updRow = (i: number, p: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const sh = list?.sheets[sheet];
  const sections = [...new Set(rows.map((r) => r.section || "Дополнительные параметры"))];

  return (
    <div>
      <div className="card">
        <b>Из файла</b> <span className="muted">— опросник RFQ одной машины (заполнит форму ниже) или таблица со списком моделей (завод, тип, модель, исполнение, цена…)</span>
        <div className="row" style={{ marginTop: 6 }}>
          <input type="file" accept=".xlsx,.xlsm,.csv" onChange={(e) => { setFile((e.target as HTMLInputElement).files?.[0] || null); setList(null); }} />
          <button className="btn secondary" disabled={!file || busy} onClick={preview}>Загрузить</button>
        </div>
        {notes.length > 0 && <div className="alert warn" style={{ marginTop: 6 }}>{notes.slice(0, 6).map((n, i) => <div key={i}>{n.message}</div>)}{notes.length > 6 && <div>… и ещё {notes.length - 6}</div>}</div>}
        {list && sh && (
          <div style={{ marginTop: 8 }}>
            <div className="row" style={{ flexWrap: "wrap" }}>
              {list.sheets.length > 1 && <label className="f">Лист<select value={sheet} onChange={(e) => { const i = Number(e.target.value); setSheet(i); const m = list.sheets[i].mapping; setMapping({ header_row: m.header_row, first_row: m.first_row, columns: Object.fromEntries(Object.entries(m.columns).map(([k, v]) => [k, String(v)])) }); }}>{list.sheets.map((x, i) => <option key={x.name} value={i}>{x.name}</option>)}</select></label>}
              <label className="f">Строка заголовков<input type="number" style={{ width: 70 }} value={mapping.header_row} onChange={(e) => setMapping({ ...mapping, header_row: Number(e.target.value) })} /></label>
              <label className="f">Первая строка данных<input type="number" style={{ width: 70 }} value={mapping.first_row} onChange={(e) => setMapping({ ...mapping, first_row: Number(e.target.value) })} /></label>
              {list.columns.map((c) => (
                <label key={c.key} className="f">{c.title}{c.key === "model" && " *"}
                  <select value={mapping.columns[c.key] ?? ""} onChange={(e) => setMapping({ ...mapping, columns: { ...mapping.columns, [c.key]: e.target.value } })}>
                    <option value="">—</option>{(sh.rows[mapping.header_row] || []).map((h, j) => <option key={j} value={j}>{j + 1}: {String(h ?? "").slice(0, 20) || "(пусто)"}</option>)}</select></label>))}
              {!mapping.columns.factory && <label className="f">Завод для всего списка<select value={listFactory} onChange={(e) => setListFactory(e.target.value)}><option value="">—</option>{factories.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>}
            </div>
            <div style={{ maxHeight: 260, overflow: "auto", marginTop: 6 }}>
              <table><thead><tr><th><input type="checkbox" checked={!sel} onChange={(e) => setSel(e.target.checked ? null : new Set())} /></th>{(sh.rows[mapping.header_row] || []).map((h, j) => <th key={j}>{String(h ?? "")}</th>)}</tr></thead>
                <tbody>{sh.rows.map((r, i) => i >= mapping.first_row && (
                  <tr key={i} style={{ opacity: sel && !sel.has(i) ? 0.45 : 1 }}><td><input type="checkbox" checked={!sel || sel.has(i)} onChange={(e) => { const s = new Set(sel || sh.rows.map((_, k) => k).filter((k) => k >= mapping.first_row)); e.target.checked ? s.add(i) : s.delete(i); setSel(s); }} /></td>
                    {r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}</tr>))}</tbody></table>
            </div>
            <div className="row" style={{ marginTop: 6 }}><button className="btn" disabled={busy || !mapping.columns.model || (!mapping.columns.factory && !listFactory)} onClick={applyList}>Добавить модели из списка</button>
              <span className="muted">{editor ? "попадут в каталог сразу" : "уйдут на подтверждение КД/ГД"}</span></div>
          </div>)}
      </div>

      <div className="card">
        <b>Вручную</b> <span className="muted">— {editor ? "запись появится в каталоге сразу" : "запись уйдёт на подтверждение КД или ГД"}; одинаковое исполнение (тот же набор параметров) не дублируется</span>
        <div className="grid cols-3" style={{ marginTop: 6 }}>
          <label className="f">Завод<select value={form.factory_id} onChange={(e) => set({ factory_id: e.target.value, trademark: factories.find((f) => f.id === e.target.value)?.trademarks?.[0] || "" })}><option value="">— новый завод —</option>{factories.map((f) => <option key={f.id} value={f.id}>{f.name}{f.trademarks?.length ? ` (${f.trademarks.join(", ")})` : ""}</option>)}</select></label>
          {!form.factory_id && <label className="f">Новый завод: полное наименование<input value={form.factory_name} onChange={(e) => set({ factory_name: e.target.value })} /></label>}
          <label className="f">Тип машины<input list="mt-list" value={form.machine_type} onChange={(e) => { set({ machine_type: e.target.value }); const t = types.find((x) => x.name === e.target.value); if (t && rows.every((r) => !r.value_ru)) loadTemplate(t.id); }} placeholder="выберите или введите новый" />
            <datalist id="mt-list">{types.map((t) => <option key={t.id} value={t.name} />)}</datalist></label>
          <label className="f">Торговая марка<input list="ca-marks" value={form.trademark} placeholder="по заводу, напр. SHILI" onChange={(e) => set({ trademark: e.target.value })} />
            <datalist id="ca-marks">{(factories.find((f) => f.id === form.factory_id)?.trademarks || []).map((m) => <option key={m} value={m} />)}</datalist></label>
          <label className="f">Модель *<input value={form.model} onChange={(e) => set({ model: e.target.value })} />
            {form.model && <span className="muted" style={{ fontSize: 11 }}>обозначение: {designation(form)}</span>}</label>
          <label className="f">Исполнение<input value={form.execution_name} placeholder={form.model ? `${form.model} базовое` : ""} onChange={(e) => set({ execution_name: e.target.value })} /></label>
          {editor && <label className="f">Наименование под брендом компании<input value={form.brand_name} onChange={(e) => set({ brand_name: e.target.value })} /></label>}
          <label className="f">Цена завода<div className="row"><input value={form.unit_price} inputMode="decimal" onChange={(e) => set({ unit_price: e.target.value })} />
            <select value={form.currency} onChange={(e) => set({ currency: e.target.value })}>{["CNY", "USD", "EUR", "RUB"].map((c) => <option key={c}>{c}</option>)}</select>
            <select value={form.basis} onChange={(e) => set({ basis: e.target.value })}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select></div></label>
          <label className="f">Срок производства<div className="row"><input type="number" value={form.term_value} style={{ width: 80 }} onChange={(e) => set({ term_value: e.target.value })} />
            <select value={form.term_unit} onChange={(e) => set({ term_unit: e.target.value })}>{["calendar_days", "working_days", "weeks"].map((u) => <option key={u} value={u}>{TERM_UNITS[u]}</option>)}</select></div></label>
          <label className="f">Комментарий<input value={form.comment} onChange={(e) => set({ comment: e.target.value })} /></label>
        </div>
        <div style={{ marginTop: 8, maxHeight: 480, overflow: "auto" }}>
          <table><thead><tr><th style={{ width: 60 }}>Код</th><th>Параметр</th><th style={{ width: 80 }}>Ед.</th><th>Значение</th><th /></tr></thead>
            <tbody>{sections.map((sec) => [
              <tr key={"h" + sec} style={{ background: "#f1f1f1" }}><td colSpan={5} style={{ fontWeight: 700 }}>{sec} <span className="muted" style={{ fontWeight: 400 }}>· заполнено {rows.filter((r) => (r.section || "Дополнительные параметры") === sec && r.value_ru).length}</span></td></tr>,
              ...rows.map((r, i) => (r.section || "Дополнительные параметры") !== sec ? null : (
                <tr key={i} style={{ background: r.value_ru ? undefined : "#fffbe8" }}>
                  <td className="muted">{r.code}</td>
                  <td><input value={r.name_ru} style={{ width: "100%", padding: 3 }} onChange={(e) => updRow(i, { name_ru: e.target.value })} /></td>
                  <td><input value={r.unit} style={{ width: "100%", padding: 3 }} onChange={(e) => updRow(i, { unit: e.target.value })} /></td>
                  <td><input value={r.value_ru} style={{ width: "100%", padding: 3 }} onChange={(e) => updRow(i, { value_ru: e.target.value })} /></td>
                  <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button></td></tr>)),
            ])}</tbody></table>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn ghost" onClick={() => setRows([...rows, { code: "", name_ru: "", unit: "", value_ru: "", section: "Дополнительные параметры" }])}>+ параметр</button>
          <button className="btn" disabled={busy || !form.model || (!form.factory_id && !form.factory_name)} onClick={add}>Добавить в каталог</button><UndoButton h={history} />
          <span className="muted">заполнено параметров: {rows.filter((r) => r.value_ru).length} из {rows.length}</span>
        </div>
        {err && <div className="alert error">{err}</div>}
      </div>
    </div>
  );
}
