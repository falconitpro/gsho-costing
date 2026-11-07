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
  const inp = (i: number, k: keyof Zip, w: number, ph = "") => (
    <input style={{ width: w, minWidth: 0 }} placeholder={ph} disabled={!canEdit} value={String(rows[i][k] ?? "")} onChange={(e) => set(i, { [k]: e.target.value } as Partial<Zip>)} />);
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: "4px 0" }}>ЗИП</h3>
        {canEdit && <div className="row"><button className="btn add" onClick={() => { setRows([...rows, { ...EMPTY_ZIP }]); setDirty(true); }}>Строка</button>
          <button className="btn" disabled={!dirty} onClick={save}>Сохранить ЗИП</button></div>}
      </div>
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
