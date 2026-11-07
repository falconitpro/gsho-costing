import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// ЗИП позиции сделки и документы модели (руководства, чертежи, схемы, каталог запчастей) — в сделке и в каталоге.

type Zip = { assembly: string; article: string; name: string; unit: string; qty: number | string; price: number | string | null; currency: string;
  interval_hours: number | string | null; kind: string; lead_days: number | string | null; comment: string };
const EMPTY_ZIP: Zip = { assembly: "", article: "", name: "", unit: "шт", qty: 1, price: "", currency: "CNY", interval_hours: "", kind: "supply", lead_days: "", comment: "" };
const num = (v: unknown) => (v === "" || v == null ? null : Number(String(v).replace(",", ".")));

export function ZipBlock({ dealId, positionId, canEdit }: { dealId: string; positionId: string; canEdit: boolean }) {
  const base = `/api/deals/${dealId}/positions/${positionId}/zip`;
  const [rows, setRows] = useState<Zip[]>([]);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ items: Zip[] }>(base).then((r) => { setRows(r.items); setDirty(false); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [positionId]);
  const set = (i: number, p: Partial<Zip>) => { setRows(rows.map((x, j) => (j === i ? { ...x, ...p } : x))); setDirty(true); };
  async function save() {
    try {
      await api.put(base, { items: rows.map((r) => ({ ...r, qty: num(r.qty) ?? 1, price: num(r.price), interval_hours: num(r.interval_hours), lead_days: num(r.lead_days) })) });
      setMsg("ЗИП сохранён"); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  // Excel: шаблон, загрузка с предпросмотром, выгрузка перечня на заданную наработку
  const [preview, setPreview] = useState<{ items: Zip[]; errors: string[]; current: number; file: File } | null>(null);
  const [hours, setHours] = useState("2000");
  const [forHours, setForHours] = useState<{ items: (Zip & { replacements: number; qty_period: number; amount: number | null })[]; totals: Record<string, number> } | null>(null);
  async function pick(file: File | null) {
    if (!file) return;
    try { const r = await api.upload<{ items: Zip[]; errors: string[]; current: number }>(`${base}/import`, file, { mode: "preview" }); setPreview({ ...r, file }); setMsg(""); }
    catch (e) { setMsg((e as Error).message); }
  }
  async function applyImport(mode: "replace" | "append") {
    if (!preview) return;
    try { await api.upload(`${base}/import`, preview.file, { mode }); setPreview(null); setMsg(`Загружено из Excel: ${preview.items.length} строк (${mode === "replace" ? "заменили" : "добавили"})`); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const showHours = () => api.get<typeof forHours>(`${base}/for-hours?hours=${num(hours) || 0}`).then(setForHours).catch((e) => setMsg((e as Error).message));
  const inp = (i: number, k: keyof Zip, w: number, ph = "") => (
    <input style={{ width: w, minWidth: 0 }} placeholder={ph} disabled={!canEdit} value={String(rows[i][k] ?? "")} onChange={(e) => set(i, { [k]: e.target.value } as Partial<Zip>)} />);
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: "4px 0" }}>ЗИП</h3>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <a className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} href={`${base}/template.xlsx`} title="шаблон Excel с нужными колонками (и текущим ЗИП, если он есть)">Шаблон Excel</a>
          {canEdit && <label className="btn ghost" style={{ padding: "4px 10px", fontSize: 12, cursor: "pointer" }}>Загрузить из Excel
            <input type="file" accept=".xlsx" style={{ display: "none" }} onChange={(e) => { pick(e.target.files?.[0] || null); e.target.value = ""; }} /></label>}
          {canEdit && <><button className="btn add" onClick={() => { setRows([...rows, { ...EMPTY_ZIP }]); setDirty(true); }}>Строка</button>
            <button className="btn" disabled={!dirty} onClick={save}>Сохранить ЗИП</button></>}
        </div>
      </div>
      {preview && (
        <div className="card" style={{ margin: "6px 0", borderLeft: "4px solid var(--info)" }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <b>Из файла {preview.file.name}: {preview.items.length} строк{preview.current ? ` · сейчас в ЗИП ${preview.current}` : ""}</b>
            <div className="row" style={{ gap: 6 }}>
              <button className="btn" disabled={preview.errors.length > 0} onClick={() => applyImport("replace")}>Заменить ЗИП</button>
              <button className="btn secondary" disabled={preview.errors.length > 0} onClick={() => applyImport("append")}>Добавить к текущему</button>
              <button className="btn ghost" onClick={() => setPreview(null)}>Отмена</button></div>
          </div>
          {preview.errors.length > 0 && <div className="alert error" style={{ fontSize: 12 }}>{preview.errors.map((e, i) => <div key={i}>{e}</div>)}</div>}
          <table style={{ fontSize: 12, marginTop: 6 }}><thead><tr><th>Вид</th><th>Узел</th><th>Артикул</th><th>Наименование</th><th>Ед.</th><th className="num">Кол-во</th><th className="num">Цена</th><th>Валюта</th><th className="num">Замена, м·ч</th><th className="num">Срок, дн</th><th>Комментарий</th></tr></thead>
            <tbody>{preview.items.map((r, i) => <tr key={i}><td>{r.kind === "to" ? "ЗИП на ТО" : "в комплекте"}</td><td>{r.assembly}</td><td>{r.article}</td><td>{r.name}</td><td>{r.unit}</td><td className="num">{r.qty}</td><td className="num">{r.price ?? "—"}</td><td>{r.currency}</td><td className="num">{r.interval_hours ?? "—"}</td><td className="num">{r.lead_days ?? "—"}</td><td>{r.comment}</td></tr>)}</tbody></table>
        </div>)}
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
      {rows.length === 0 ? <div className="muted">ЗИП не задан{canEdit ? " — добавьте строки: что входит в комплект поставки и что нужно на ТО" : ""}.</div> : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ fontSize: 12 }}>
            <thead><tr><th>Вид</th><th>Узел / система</th><th>Артикул</th><th>Наименование</th><th>Ед.</th><th className="num">Кол-во</th><th className="num">Цена</th><th>Валюта</th>
              <th className="num" title="периодичность замены">Замена, м·ч</th><th className="num">Срок поставки, дн</th><th>Комментарий</th>{canEdit && <th />}</tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i}>
                <td><select value={r.kind} disabled={!canEdit} onChange={(e) => set(i, { kind: e.target.value })}><option value="supply">в комплекте поставки</option><option value="to">ЗИП на ТО</option></select></td>
                <td>{inp(i, "assembly", 110)}</td><td>{inp(i, "article", 100)}</td><td>{inp(i, "name", 180)}</td><td>{inp(i, "unit", 45)}</td><td>{inp(i, "qty", 55)}</td>
                <td>{inp(i, "price", 75)}</td>
                <td><select value={r.currency} disabled={!canEdit} onChange={(e) => set(i, { currency: e.target.value })}>{["CNY", "USD", "EUR", "RUB"].map((c) => <option key={c}>{c}</option>)}</select></td>
                <td>{inp(i, "interval_hours", 70, r.kind === "to" ? "м·ч" : "")}</td><td>{inp(i, "lead_days", 60)}</td><td>{inp(i, "comment", 140)}</td>
                {canEdit && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => { setRows(rows.filter((_, j) => j !== i)); setDirty(true); }}>×</button></td>}
              </tr>))}</tbody>
          </table>
        </div>)}
      {rows.some((r) => r.kind === "to") && (
        <div style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
            <b>Перечень ЗИП на наработку</b>
            <input style={{ width: 80 }} value={hours} onChange={(e) => setHours(e.target.value)} /> м·ч
            <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={showHours}>Показать</button>
            <a className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} href={`${base}/for-hours.xlsx?hours=${num(hours) || 0}`}>Выгрузить Excel</a>
            <span className="muted" style={{ fontSize: 11 }}>число замен = наработка ÷ периодичность (целое вниз); комплект поставки — один раз</span>
          </div>
          {forHours && (forHours.items.length === 0 ? <div className="muted" style={{ fontSize: 12 }}>На {hours} м·ч замен не требуется.</div> : (
            <table style={{ fontSize: 12, marginTop: 6 }}><thead><tr><th>Вид</th><th>Артикул</th><th>Наименование</th><th className="num">Замен</th><th className="num">Кол-во на период</th><th className="num">Сумма</th><th>Валюта</th></tr></thead>
              <tbody>{forHours.items.map((r, i) => <tr key={i}><td>{r.kind === "to" ? "ЗИП на ТО" : "в комплекте"}</td><td>{r.article}</td><td>{r.name}</td><td className="num">{r.replacements}</td><td className="num">{r.qty_period} {r.unit}</td><td className="num">{r.amount ?? "—"}</td><td>{r.currency}</td></tr>)}
                {Object.entries(forHours.totals).map(([c, v]) => <tr key={c} className="total"><td colSpan={5}>Итого</td><td className="num">{v}</td><td>{c}</td></tr>)}</tbody></table>))}
        </div>)}
      <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>«В комплекте поставки» попадает в чек-лист приёмки на заводе; «ЗИП на ТО» с периодичностью замены — в ТО-комплекты парка машин.</div>
    </div>
  );
}

type Doc = { id: string; kind: string; kind_title: string; title: string; language: string; language_title: string; version: string; file_name: string; size: number;
  uploaded_by_name: string; uploaded_at: string; url: string; comment: string };
export const DOC_KINDS: Record<string, string> = { manual: "Руководство по эксплуатации", parts_catalog: "Каталог запасных частей", drawing: "Чертежи",
  hydraulic: "Гидравлическая схема", electric: "Электрическая схема", pneumatic: "Пневматическая схема", scheme: "Прочие схемы", certificate: "Сертификаты и декларации", other: "Прочее" };
const LANGS: Record<string, string> = { ru: "RU", en: "EN", zh: "中文", de: "DE", other: "другой" };

/** Документы модели: в сделке (dealId + positionId) или в каталоге (modelId). Хранятся у модели — видны во всех сделках с ней. */
export function ModelDocsBlock({ dealId, positionId, modelId, canUpload, canRemove = false }: { dealId?: string; positionId?: string; modelId?: string; canUpload: boolean; canRemove?: boolean }) {
  const url = dealId && positionId ? `/api/deals/${dealId}/positions/${positionId}/documents` : `/api/catalog/models/${modelId}/documents`;
  const [docs, setDocs] = useState<Doc[]>([]);
  const [model, setModel] = useState("");
  const [f, setF] = useState({ kind: "manual", language: "ru", version: "", title: "" });
  const [file, setFile] = useState<File | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Doc[] | { model: string; documents: Doc[] }>(url).then((r) => {
    if (Array.isArray(r)) setDocs(r); else { setDocs(r.documents); setModel(r.model); } }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [url]);
  async function upload() {
    if (!file) return;
    try { await api.upload(url, file, f); setMsg(`Загружено: ${file.name}`); setFile(null); setF({ ...f, version: "", title: "" }); load(); } catch (e) { setMsg((e as Error).message); }
  }
  const groups = Object.keys(DOC_KINDS).filter((k) => docs.some((d) => d.kind === k));
  return (
    <div>
      <h3 style={{ margin: "4px 0" }}>Документы модели{model && <span className="muted" style={{ fontWeight: 400 }}> — {model}, общие для каталога и всех сделок</span>}</h3>
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
      {docs.length === 0 && <div className="muted">Документов нет.</div>}
      {groups.map((k) => (
        <div key={k} style={{ marginBottom: 4 }}><b>{DOC_KINDS[k]}</b>
          {docs.filter((d) => d.kind === k).map((d) => (
            <div key={d.id} className="row" style={{ fontSize: 13, marginLeft: 10 }}>
              <a href={d.url} target="_blank" rel="noreferrer">{d.title && d.title !== DOC_KINDS[k] ? d.title : d.file_name}</a>
              <span className="badge">{LANGS[d.language] || d.language}</span>{d.version && <span className="badge">версия {d.version}</span>}
              <span className="muted" style={{ fontSize: 11 }}>{fmtDate(d.uploaded_at)} · {d.uploaded_by_name}</span>
              {canRemove && <a href="#" style={{ fontSize: 11 }} onClick={(e) => { e.preventDefault(); if (confirm("Убрать документ из модели?")) api.del(`/api/catalog/documents/${d.id}`).then(load).catch((x) => setMsg((x as Error).message)); }}>убрать</a>}
            </div>))}
        </div>))}
      {canUpload && (
        <div className="row" style={{ flexWrap: "wrap", marginTop: 4 }}>
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(DOC_KINDS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <select value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}>{Object.entries(LANGS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <input placeholder="версия / редакция" style={{ width: 120 }} value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })} />
          <input placeholder="название (необязательно)" style={{ width: 180 }} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button className="btn secondary" disabled={!file} onClick={upload}>Загрузить</button>
        </div>)}
    </div>
  );
}
