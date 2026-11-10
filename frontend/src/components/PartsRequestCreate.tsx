import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";

// Заведение заявки на запасные части (D-171…D-174): 1) клиент и машина; 2) позиции — из каталога, из файла заказа клиента
// или вставкой из письма/Excel; каждая строка сразу подбирается по каталогу и показывает наличие по складам; 3) создать.

type Meta = { channels: Record<string, string> };
type Ref = { id: string; name: string };
type Wh = { warehouse_id: string; warehouse: string; available: number; reserved: number };
type PartInfo = { id: string; article: string; name_ru: string; unit: string; manufacturer: string; available: number | null; warehouses: Wh[] };
type Draft = { key: string; raw_article: string; raw_name: string; qty: number; unit: string; part_id?: string | null; part: PartInfo | null; match_kind: string;
  candidates: string[]; source: "catalog" | "file" | "text" };
type CatalogItem = { id: string; article: string; name_ru: string; unit: string; manufacturer: string; oem_number: string; stock_total: number };
type Machine = { id: string; manufacturer: string; model: string; serial: string };

const plural = (n: number, one: string, few: string, many: string) => (n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many);
const SRC: Record<Draft["source"], string> = { catalog: "каталог", file: "файл", text: "вставка" };
let seq = 0;
const key = () => `l${++seq}`;
const errText = (e: unknown) => (e instanceof ApiError && typeof e.detail === "string" ? e.detail : (e as Error).message);

function StockCell({ d }: { d: Draft }) {
  if (!d.part) return <span className="muted" style={{ fontSize: 12 }}>—</span>;
  const total = d.part.available ?? 0;
  const tone = total >= d.qty && d.qty > 0 ? "ok" : total > 0 ? "warn" : "bad";
  return (
    <div style={{ fontSize: 12 }}>
      <span className={`badge ${tone}`}>{total >= d.qty && d.qty > 0 ? "есть" : total > 0 ? `есть ${total} из ${d.qty}` : "нет на складах"}</span>
      {d.part.warehouses.filter((w) => w.available > 0 || w.reserved > 0).map((w) => (
        <div key={w.warehouse_id} className="muted">{w.warehouse}: <b style={{ color: "var(--ink)" }}>{w.available}</b>{w.reserved ? ` (в резерве ${w.reserved})` : ""}</div>))}
    </div>);
}

export default function PartsRequestCreate({ meta, clients, onCreated, onCancel }: { meta: Meta; clients: Ref[]; onCreated: (r: { id: string }) => void; onCancel: () => void }) {
  const [f, setF] = useState({ client_id: "", contact: "", channel: "email", machine: "", machine_id: "", urgent: false, warranty: false, comment: "" });
  const [machines, setMachines] = useState<Machine[]>([]);
  const [lines, setLines] = useState<Draft[]>([]);
  const [mode, setMode] = useState<"catalog" | "file" | "text">("catalog");
  const [q, setQ] = useState("");
  const [found, setFound] = useState<CatalogItem[]>([]);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setMachines([]);
    if (f.client_id) api.get<Machine[]>(`/api/parts/fleet/machines?client_id=${f.client_id}`).then(setMachines).catch(() => setMachines([]));
  }, [f.client_id]);
  useEffect(() => {
    if (mode !== "catalog" || q.trim().length < 2) { setFound([]); return; }
    const t = setTimeout(() => api.get<{ items: CatalogItem[] }>(`/api/parts/catalog?q=${encodeURIComponent(q.trim())}&limit=15`).then((r) => setFound(r.items)).catch(() => setFound([])), 250);
    return () => clearTimeout(t);
  }, [q, mode]);

  async function preview(items: { raw_article?: string; raw_name?: string; qty?: number; unit?: string; part_id?: string }[], source: Draft["source"], lines_text = "") {
    const r = await api.post<{ lines: Omit<Draft, "key" | "source">[] }>("/api/parts/requests/preview-lines", { lines: items, lines_text });
    return r.lines.map((l) => ({ ...l, qty: Number(l.qty) || 1, unit: l.unit || "шт", key: key(), source }) as Draft);
  }
  function addMerged(add: Draft[]) {     // та же карточка уже в заявке — количество складывается
    const next = [...lines];
    for (const d of add) {
      const same = d.part ? next.find((x) => x.part?.id === d.part?.id) : undefined;
      if (same) same.qty = Math.round((same.qty + d.qty) * 1000) / 1000;
      else next.push(d);
    }
    setLines(next.map((x) => ({ ...x })));
  }
  async function addFromCatalog(it: CatalogItem) {
    try { addMerged(await preview([{ part_id: it.id, qty: 1 }], "catalog")); setMsg(`Добавлено: ${it.article} ${it.name_ru}`); }
    catch (e) { setMsg(errText(e)); }
  }
  async function addFromFile(file: File) {
    setBusy(true);
    try {
      const r = await api.upload<{ lines: Omit<Draft, "key" | "source">[]; columns: Record<string, string | null>; header_row: number | null; skipped: { row: number; reason: string }[] }>("/api/parts/requests/parse-file", file);
      addMerged(r.lines.map((l) => ({ ...l, qty: Number(l.qty) || 1, unit: l.unit || "шт", key: key(), source: "file" }) as Draft));
      const cols = Object.entries(r.columns).filter(([, v]) => v).map(([k, v]) => `${({ article: "номер", name: "наименование", qty: "количество", unit: "ед." } as Record<string, string>)[k]} — «${v}»`).join(", ");
      setMsg(`Из файла «${file.name}»: строк ${r.lines.length}${cols ? `; колонки: ${cols}` : ""}${r.skipped.length ? `; без количества (поставлено 1): строки ${r.skipped.map((x) => x.row).join(", ")}` : ""}. Проверьте строки ниже.`);
    } catch (e) { setMsg(errText(e)); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function addFromText() {
    try { const add = await preview([], "text", text); addMerged(add); setText(""); setMsg(`Разобрано строк: ${add.length}`); }
    catch (e) { setMsg(errText(e)); }
  }
  async function pickCandidate(d: Draft, partId: string) {
    const [x] = await preview([{ part_id: partId, qty: d.qty, raw_article: d.raw_article }], d.source);
    setLines(lines.map((l) => (l.key === d.key ? { ...x, key: d.key, raw_name: d.raw_name || x.raw_name } : l)));
  }
  const set = (k: string, p: Partial<Draft>) => setLines(lines.map((l) => (l.key === k ? { ...l, ...p } : l)));
  const matched = lines.filter((l) => l.part).length;
  const inStock = lines.filter((l) => l.part && (l.part.available ?? 0) >= l.qty).length;
  async function save() {
    setBusy(true);
    try {
      const r = await api.post<{ id: string }>("/api/parts/requests", { ...f, machine_id: f.machine_id || null,
        lines: lines.map((l) => ({ raw_article: l.raw_article, raw_name: l.raw_name, qty: l.qty, unit: l.unit, part_id: l.part?.id || null })) });
      onCreated(r);
    } catch (e) { setMsg(errText(e)); } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Новая заявка на запасные части</h2>
      <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>1 — клиент и машина · 2 — позиции (каталог, файл заказа или вставка) · 3 — создать. Подбор по каталогу и наличие на складах — сразу по каждой строке.</div>

      <h3 style={{ margin: "6px 0" }}>1. Клиент и машина</h3>
      <div className="grid cols-3">
        <label className="f">Клиент *<select value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value, machine_id: "" })}>
          <option value="">— выберите —</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="f">Машина клиента{machines.length > 0 ? " — из парка" : ""}
          {machines.length > 0 ? <select value={f.machine_id} onChange={(e) => setF({ ...f, machine_id: e.target.value })}>
            <option value="">— не из парка / не указана —</option>{machines.map((m) => <option key={m.id} value={m.id}>{[m.manufacturer, m.model, m.serial && `зав. № ${m.serial}`].filter(Boolean).join(" ")}</option>)}</select>
            : <input value={f.machine} onChange={(e) => setF({ ...f, machine: e.target.value })} placeholder="напр. Sandvik LH410, зав. № …" />}</label>
        <label className="f">Контакт клиента<input value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} placeholder="ФИО, телефон" /></label>
        <label className="f">Откуда заявка<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{Object.entries(meta.channels).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Срочность<div className="row"><input type="checkbox" checked={f.urgent} onChange={(e) => setF({ ...f, urgent: e.target.checked })} /> машина стоит — аварийная заявка</div></label>
        <label className="f">Гарантия<div className="row"><input type="checkbox" checked={f.warranty} onChange={(e) => setF({ ...f, warranty: e.target.checked })} /> гарантийная замена</div></label>
      </div>

      <h3 style={{ margin: "14px 0 6px" }}>2. Позиции</h3>
      <div className="tabs" style={{ margin: 0 }}>
        <button className={mode === "catalog" ? "active" : ""} onClick={() => setMode("catalog")}>Из каталога</button>
        <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>Из файла заказа клиента</button>
        <button className={mode === "text" ? "active" : ""} onClick={() => setMode("text")}>Вставить из письма / Excel</button>
      </div>
      <div style={{ border: "1px solid var(--line)", borderTop: 0, padding: 10, borderRadius: "0 0 8px 8px", marginBottom: 8 }}>
        {mode === "catalog" && <>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="🔍 каталожный номер, OEM, номер поставщика или наименование (от 2 символов)" style={{ width: "100%" }} />
          {found.length > 0 && <table style={{ marginTop: 6 }}><tbody>{found.map((it) => (
            <tr key={it.id}><td><b>{it.article}</b>{it.oem_number && <div className="muted" style={{ fontSize: 11 }}>OEM {it.oem_number}</div>}</td><td>{it.name_ru}<div className="muted" style={{ fontSize: 11 }}>{it.manufacturer}</div></td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>{it.stock_total > 0 ? <span className="badge ok">на складах {it.stock_total}</span> : <span className="muted" style={{ fontSize: 12 }}>нет на складах</span>}</td>
              <td style={{ width: 90 }}><button className="btn ghost" onClick={() => addFromCatalog(it)}>добавить</button></td></tr>))}</tbody></table>}
          {q.trim().length >= 2 && found.length === 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>В каталоге не найдено — добавьте строку вставкой: она попадёт в заявку, карточку подберут позже.</div>}
        </>}
        {mode === "file" && <div>
          <div className="row" style={{ gap: 8 }}><input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv,.txt" disabled={busy} onChange={(e) => { const x = e.target.files?.[0]; if (x) addFromFile(x); }} />
            {busy && <span className="muted">разбираю файл…</span>}</div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Excel (.xlsx) или CSV. Колонки находятся сами по заголовкам «Артикул / каталожный номер», «Наименование», «Количество», «Ед.»;
            без заголовка — первая колонка номер, последняя числовая — количество. Старый .xls сохраните как .xlsx; PDF и скан — вставьте строки текстом.</div>
        </div>}
        {mode === "text" && <div>
          <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} placeholder={"554831\tВедущий мост\t1\n522608; Топливный фильтр; 12"} />
          <div className="row" style={{ marginTop: 4 }}><button className="btn ghost" disabled={!text.trim()} onClick={addFromText}>Разобрать и добавить</button>
            <span className="muted" style={{ fontSize: 12 }}>каждая строка: номер, наименование, количество — через Tab или «;»</span></div>
        </div>}
      </div>
      {msg && <div className="alert info" style={{ fontSize: 13 }} onClick={() => setMsg("")}>{msg}</div>}

      {lines.length > 0 && <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>№</th><th>Номер</th><th>Наименование</th><th className="num">Кол-во</th><th>Ед.</th><th>Каталог</th><th>Наличие на складах</th><th></th></tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={l.key}>
              <td>{i + 1}<div className="muted" style={{ fontSize: 10 }}>{SRC[l.source]}</div></td>
              <td><input value={l.raw_article} style={{ width: 130 }} disabled={!!l.part && l.source === "catalog"} onChange={(e) => set(l.key, { raw_article: e.target.value })} /></td>
              <td><input value={l.raw_name} style={{ width: "100%", minWidth: 160 }} onChange={(e) => set(l.key, { raw_name: e.target.value })} /></td>
              <td className="num"><input type="number" min={0} step="any" value={l.qty} style={{ width: 70 }} onChange={(e) => set(l.key, { qty: Number(e.target.value) })} /></td>
              <td style={{ fontSize: 12 }}>{l.unit}</td>
              <td style={{ fontSize: 12, maxWidth: 240 }}>{l.part ? <><span className="badge ok">найдено</span> <b>{l.part.article}</b> {l.part.name_ru}{l.part.manufacturer && <span className="muted"> · {l.part.manufacturer}</span>}</>
                : l.candidates.length ? <><span className="badge warn">несколько карточек</span>
                  <select style={{ fontSize: 11, maxWidth: 200 }} value="" onChange={(e) => e.target.value && pickCandidate(l, e.target.value)}><option value="">— выбрать —</option>
                    {l.candidates.map((c) => <option key={c} value={c}>{c.slice(0, 8)}…</option>)}</select></>
                : <span className="badge">нет в каталоге</span>}</td>
              <td><StockCell d={l} /></td>
              <td><button className="btn ghost" style={{ padding: "0 8px" }} title="убрать строку" onClick={() => setLines(lines.filter((x) => x.key !== l.key))}>×</button></td>
            </tr>))}</tbody>
        </table>
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Строк: {lines.length} · найдено в каталоге: {matched} · есть на складах в нужном количестве: {inStock}. Строки без карточки попадут в заявку — их подберут в заявке или запросят у поставщиков.</div>
      </div>}

      <label className="f" style={{ marginTop: 8 }}>Комментарий<input value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} /></label>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" onClick={save} disabled={busy || !f.client_id || lines.length === 0 || lines.some((l) => !(l.qty > 0))}>3. Создать заявку{lines.length ? ` (${lines.length} ${plural(lines.length, "строка", "строки", "строк")})` : ""}</button>
        <button className="btn ghost" onClick={onCancel}>отмена</button>
        {!f.client_id && <span className="muted" style={{ fontSize: 12 }}>выберите клиента</span>}
        {f.client_id && !lines.length && <span className="muted" style={{ fontSize: 12 }}>добавьте позиции</span>}
      </div>
    </div>);
}
