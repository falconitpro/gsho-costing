import { useEffect, useRef, useState } from "react";
import { useNewUiFlag } from "../uiMode";
import { useTableLabels } from "./useTableLabels";
import { Loading } from "./States";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Me } from "../types";
import { askConfirm } from "./Dialogs";

// Каталог запасных частей: поиск, карточка (утверждена 05.11.2026), проверка дублей, перенос из МойСклад.

type Cross = { kind: string; number: string; supplier_id: string | null; manufacturer: string; comment: string };
type Appl = { id?: string; equipment_model_id: string | null; other_manufacturer: string; other_model: string; assembly: string; model_title?: string;
  status?: string; status_title?: string; serial_from?: string; serial_to?: string; basis_kind?: string; basis?: string; verified_by?: string | null;
  verified_by_name?: string; verified_at?: string | null; execution_id?: string | null; execution_name?: string };
const APPL_STATUS: Record<string, string> = { confirmed: "Подтверждено", probable: "Вероятно", unverified: "Не подтверждено", not_applicable: "Не подходит", replaced: "Заменена" };
const APPL_BASIS: Record<string, string> = { manual: "Каталог / руководство завода", supplier: "Поставщик", service: "Опыт сервиса", analog: "По аналогии", comment: "Комментарий" };

/** D-196 (J-b): статус проверки связи в карточке детали — только новый вид. */
function ApplStatus({ a, edit, onChange }: { a: Appl; edit: boolean; onChange: (p: Partial<Appl>) => void }) {
  const st = a.status || "unverified";
  const tone = { confirmed: "ok", probable: "info", unverified: "warn", not_applicable: "bad", replaced: "" }[st] || "";
  if (!edit) return (
    <div className="pc-appl-st"><span className={`badge ${tone}`}>{APPL_STATUS[st]}</span>
      {(a.serial_from || a.serial_to) && <span className="pc-sub"> · номера {a.serial_from || "…"} — {a.serial_to || "…"}</span>}
      {(a.basis || a.basis_kind) && <span className="pc-sub"> · {[APPL_BASIS[a.basis_kind || ""], a.basis].filter(Boolean).join(": ")}</span>}
      {a.verified_by_name && <span className="pc-sub"> · {a.verified_by_name}</span>}</div>);
  return (
    <div className="pc-appl-st edit">
      <select value={st} aria-label="Статус применимости" onChange={(e) => onChange({ status: e.target.value })}>
        {Object.entries(APPL_STATUS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
      <select value={a.basis_kind || ""} aria-label="Основание" onChange={(e) => onChange({ basis_kind: e.target.value })}>
        <option value="">основание…</option>{Object.entries(APPL_BASIS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
      <input value={a.basis || ""} placeholder="документ, страница, комментарий" aria-label="Основание — текст" onChange={(e) => onChange({ basis: e.target.value })} />
      <input value={a.serial_from || ""} placeholder="зав. № с" aria-label="Серийные номера с" onChange={(e) => onChange({ serial_from: e.target.value })} />
      <input value={a.serial_to || ""} placeholder="по" aria-label="Серийные номера по" onChange={(e) => onChange({ serial_to: e.target.value })} />
    </div>);
}
type ClientPrice = { client_id: string | null; client_name: string; price: number; currency: string; valid_from: string | null; valid_to: string | null };
type Stock = { warehouse_id: string; warehouse?: string; warehouse_kind?: string; qty?: number; reserved?: number; in_transit?: number; available?: number; min_qty: number | null };
type Part = {
  id?: string; article: string; name_ru: string; unit: string; part_group: string; status: string; names: { en?: string; cn?: string; de?: string };
  description: string; manufacturer: string; oem_number: string; supplier_id: string | null; route_id: string | null;
  purchase_price?: number | null; purchase_currency?: string; purchase_on?: string | null; lead_days: number | null; hs_code: string; origin_country: string;
  moq: number | null; multiple: number | null; weight_kg: number | null; length_mm: number | null; width_mm: number | null; height_mm: number | null; volume_m3: number | null;
  sale_price: number | null; sale_currency: string; min_price: number | null; min_price_currency: string; photos: string[]; photo_files?: { name: string; ms_image_id?: string }[]; spec_no: string; addendum_no: string;
  cross_refs: Cross[]; applicability: Appl[]; client_prices: ClientPrice[]; stock?: Stock[]; stock_total?: number; source?: string;
};
type Row = { id: string; article: string; name_ru: string; unit: string; part_group: string; manufacturer: string; oem_number: string; status: string;
  stock_total: number; sale_price: number | null; sale_currency: string; has_client_prices: boolean;
  stock_reserved?: number; stock_available?: number; stock_in_transit?: number; below_min_count?: number };
type Meta = { cross_kinds: Record<string, string>; status: Record<string, string>; groups: string[]; can_edit: boolean; can_import: boolean };
type Ref = { id: string; name: string; short_name?: string };
type Dup = { id: string; article: string; name_ru: string; manufacturer: string; oem_number: string };

const CUR = ["EUR", "CNY", "USD", "RUB", "KGS"];
const EMPTY: Part = { article: "", name_ru: "", unit: "шт", part_group: "", status: "active", names: {}, description: "", manufacturer: "", oem_number: "",
  supplier_id: null, route_id: null, purchase_price: null, purchase_currency: "EUR", purchase_on: null, lead_days: null, hs_code: "", origin_country: "",
  moq: null, multiple: null, weight_kg: null, length_mm: null, width_mm: null, height_mm: null, volume_m3: null, sale_price: null, sale_currency: "EUR",
  min_price: null, min_price_currency: "EUR", photos: [], spec_no: "", addendum_no: "", cross_refs: [], applicability: [], client_prices: [], stock: [] };
const n = (v: string) => (v === "" ? null : Number(v.replace(",", ".")));
const SECTIONS: [string, string][] = [["pc-main", "Основное"], ["pc-nums", "Номера и аналоги"], ["pc-appl", "Применимость"], ["pc-buy", "Закупка"], ["pc-log", "Логистика"],
  ["pc-stock", "Склад"], ["pc-price", "Цены"], ["pc-files", "Фото и документы"]];

/** Новый вид: главное о запчасти одной строкой — наличие по всем складам (остаток, резерв, доступно, в пути — как отдаёт сервер),
 *  ниже минимума, цена, срок поставки. Ничего не пересчитывает, кроме сумм по складам. */
function PartSummary({ cur }: { cur: Part }) {
  const st = cur.stock || [];
  const sum = (k: "qty" | "reserved" | "in_transit" | "available") => st.reduce((a, s) => a + (k === "available" ? (s.available ?? (s.qty || 0)) : (s[k] || 0)), 0);
  const low = st.filter((s) => s.min_qty != null && (s.available ?? (s.qty || 0)) < (s.min_qty || 0));
  const u = cur.unit || "шт";
  return (
    <div className="pc-sum">
      <div className={"pc-sum-k" + (sum("available") > 0 ? " ok" : " none")}><b>{sum("available") > 0 ? `${sum("available")} ${u}` : "нет в наличии"}</b><span>доступно{sum("reserved") ? ` · резерв ${sum("reserved")}` : ""}{sum("qty") !== sum("available") ? ` · остаток ${sum("qty")}` : ""}</span></div>
      {sum("in_transit") > 0 && <div className="pc-sum-k"><b>{sum("in_transit")} {u}</b><span>в пути</span></div>}
      {low.length > 0 && <div className="pc-sum-k warn"><b>ниже минимума</b><span>{low.map((s) => `${s.warehouse || "склад"}: ${s.available ?? s.qty ?? 0} из ${s.min_qty}`).join("; ")}</span></div>}
      <div className="pc-sum-k"><b>{cur.sale_price ? `${fmtRub(cur.sale_price, 2)} ${cur.sale_currency}` : "—"}</b><span>цена продажи</span></div>
      <div className="pc-sum-k"><b>{cur.lead_days != null ? `${cur.lead_days} дн.` : "—"}</b><span>срок поставки</span></div>
    </div>);
}
const PAGE = 50;

export default function PartsCatalog({ me, openId }: { me: Me; openId?: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("");
  const [status, setStatus] = useState("active");
  const [inStock, setInStock] = useState(false);
  const [belowMin, setBelowMin] = useState(false);
  const [page, setPage] = useState(0);
  const [list, setList] = useState<{ total: number; items: Row[] }>({ total: 0, items: [] });
  const [cur, setCur] = useState<Part | null>(null);
  const [dups, setDups] = useState<Dup[]>([]);
  const [msg, setMsg] = useState("");
  const [suppliers, setSuppliers] = useState<Ref[]>([]);
  const [routes, setRoutes] = useState<Ref[]>([]);
  const [clients, setClients] = useState<Ref[]>([]);
  const [warehouses, setWarehouses] = useState<{ id: string; name: string; kind: string }[]>([]);
  const [showImport, setShowImport] = useState(false);
  const seesPurchase = can(me, "factory_price_history");
  // «Новый вид» (D-169): список и карточка рядом, группы — метками, наличие — понятной меткой, карточка — разделы с навигацией
  const nv = useNewUiFlag();
  const [loaded, setLoaded] = useState(false);
  const [snap, setSnap] = useState("");                       // сохранённое состояние карточки — для «есть несохранённые изменения»
  const [models, setModels] = useState<{ id: string; title: string }[]>([]);
  const tblRef = useRef<HTMLTableElement>(null);
  useTableLabels(tblRef, nv);

  const load = (p = page) => {
    const qs = new URLSearchParams({ q, group, status, in_stock: String(inStock), limit: String(PAGE), offset: String(p * PAGE) });
    if (belowMin) qs.set("below_min", "true");
    api.get<typeof list>(`/api/parts/catalog?${qs}`).then(setList).catch((e) => setMsg((e as Error).message)).finally(() => setLoaded(true));
  };
  useEffect(() => {
    api.get<Meta>("/api/parts/meta/catalog").then(setMeta);
    api.get<Ref[]>("/api/refs/factories").then(setSuppliers).catch(() => setSuppliers([]));
    api.get<Ref[]>("/api/parts/routes").then(setRoutes).catch(() => setRoutes([]));
    api.get<Ref[]>("/api/refs/clients").then(setClients).catch(() => setClients([]));
    api.get<typeof warehouses>("/api/parts/warehouses").then(setWarehouses).catch(() => setWarehouses([]));
  }, []);
  useEffect(() => { const t = setTimeout(() => { setPage(0); load(0); }, 300); return () => clearTimeout(t); }, [q, group, status, inStock, belowMin]);
  useEffect(() => { if (openId) open(openId); }, [openId]);
  useEffect(() => {      // новый вид: модели каталога ГШО для применимости (выбор из списка, а не текстом)
    if (!nv) return;
    api.get<{ factory: string; types: { machine_type: string; models: { id: string; model: string; trademark?: string }[] }[] }[]>("/api/catalog/tree")
      .then((t) => setModels(t.flatMap((f) => f.types.flatMap((ty) => ty.models.map((m) => ({ id: m.id, title: `${ty.machine_type} ${m.trademark ? m.trademark + " " : ""}${m.model}` }))))))
      .catch(() => setModels([]));
  }, [nv]);
  useEffect(() => {      // Esc закрывает карточку (новый вид)
    if (!nv || !cur) return;
    const f = (e: KeyboardEvent) => { if (e.key === "Escape" && !(e.target as HTMLElement)?.closest?.("input,textarea,select")) setCur(null); };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [nv, cur]);

  async function open(id: string) {
    const p = await api.get<Part>(`/api/parts/catalog/${id}`);
    const c = { ...EMPTY, ...p, names: p.names || {} };
    setCur(c); setSnap(JSON.stringify(c)); setDups([]); setMsg("");
    if (nv) { const u = new URL(window.location.href); u.searchParams.set("s", "parts"); u.searchParams.set("part", id); window.history.replaceState(window.history.state, "", u); }
  }
  async function save(force = false) {
    if (!cur) return;
    const { id, stock, stock_total, source, ...rest } = cur;
    void stock_total; void source;
    const body = { ...rest, applicability: rest.applicability.map(({ model_title, status_title, verified_by_name, execution_name, verified_by, verified_at, ...a }) => {
      void model_title; void status_title; void verified_by_name; void execution_name; void verified_by; void verified_at; return a; }),
      min_stock: (stock || []).map((s) => ({ warehouse_id: s.warehouse_id, min_qty: s.min_qty })) };
    try {
      const r = id ? await api.put<Part>(`/api/parts/catalog/${id}`, body) : await api.post<Part>(`/api/parts/catalog${force ? "?force=true" : ""}`, body);
      setMsg("Карточка сохранена"); setDups([]); open(r.id!); load();
    } catch (e) {
      const d = (e as { detail?: { duplicates?: Dup[]; message?: string } }).detail;
      if (d && typeof d === "object" && d.duplicates) { setDups(d.duplicates); setMsg(d.message || "Есть похожие карточки"); }
      else setMsg((e as Error).message);
    }
  }
  const set = (p: Partial<Part>) => cur && setCur({ ...cur, ...p });
  const edit = !!meta?.can_edit;
  const txt = (label: string, key: keyof Part, ph = "") => (
    <label className="f">{label}<input value={String(cur?.[key] ?? "")} placeholder={ph} disabled={!edit} onChange={(e) => set({ [key]: e.target.value } as Partial<Part>)} /></label>);
  const numF = (label: string, key: keyof Part) => (
    <label className="f">{label}<input type="number" value={(cur?.[key] as number | null) ?? ""} disabled={!edit} onChange={(e) => set({ [key]: n(e.target.value) } as Partial<Part>)} /></label>);
  const curSel = (key: keyof Part) => (
    <select value={String(cur?.[key] || "EUR")} disabled={!edit} onChange={(e) => set({ [key]: e.target.value } as Partial<Part>)}>{CUR.map((c) => <option key={c}>{c}</option>)}</select>);

  const listEl = (
      <div className="card">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input style={{ minWidth: 280 }} placeholder="артикул, наименование, OEM или номер поставщика" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={group} onChange={(e) => setGroup(e.target.value)}><option value="">все группы</option>{meta?.groups.map((g) => <option key={g}>{g}</option>)}</select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="active">активные</option><option value="archive">архив</option><option value="">все</option></select>
          <label className="row"><input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} /> только в наличии</label>
          <div className="push" style={{ marginLeft: "auto" }}>
            {meta?.can_import && <button className="btn ghost" onClick={() => setShowImport(!showImport)}>Перенос из МойСклад</button>}
            {edit && <button className="btn add" onClick={() => { setCur({ ...EMPTY }); setSnap(""); setDups([]); setMsg(""); }}>Запчасть</button>}
          </div>
        </div>
        {showImport && <ImportPanel warehouses={warehouses} onDone={() => { load(); api.get<Meta>("/api/parts/meta/catalog").then(setMeta); api.get<typeof warehouses>("/api/parts/warehouses").then(setWarehouses); }} />}
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Артикул</th><th>Наименование</th><th>Группа</th><th>Производитель / OEM</th><th className="num">Остаток</th><th className="num">Цена продажи</th></tr></thead>
          <tbody>{list.items.map((r) => (
            <tr key={r.id} style={{ cursor: "pointer", background: cur?.id === r.id ? "var(--accent-soft)" : undefined, opacity: r.status === "archive" ? 0.5 : 1 }} onClick={() => open(r.id)}>
              <td><b>{r.article}</b></td><td>{r.name_ru}{r.has_client_prices && <span className="badge" style={{ marginLeft: 4 }}>договорная цена</span>}</td><td>{r.part_group}</td>
              <td>{[r.manufacturer, r.oem_number].filter(Boolean).join(" · ")}</td><td className="num">{r.stock_total ? `${r.stock_total} ${r.unit}` : "—"}</td>
              <td className="num">{r.sale_price ? `${fmtRub(r.sale_price, 2)} ${r.sale_currency}` : "—"}</td></tr>))}
            {list.items.length === 0 && <tr><td colSpan={6} className="muted">Ничего не найдено</td></tr>}</tbody>
        </table>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="muted">{list.total ? `${page * PAGE + 1}–${Math.min((page + 1) * PAGE, list.total)} из ${list.total}` : ""}</span>
          <button className="btn ghost" disabled={page === 0} onClick={() => { setPage(page - 1); load(page - 1); }}>назад</button>
          <button className="btn ghost" disabled={(page + 1) * PAGE >= list.total} onClick={() => { setPage(page + 1); load(page + 1); }}>вперёд</button>
        </div>
      </div>
  );
  const cardEl = cur ? (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>{cur.id ? `${cur.article} — ${cur.name_ru}` : "Новая запчасть"} {cur.source === "moysklad" && <span className="badge">из МойСклад</span>}</h2>
          {dups.length > 0 && (
            <div className="alert info">Похожие карточки: {dups.map((d) => <a key={d.id} href="#" style={{ marginRight: 8 }} onClick={(e) => { e.preventDefault(); open(d.id); }}>{d.article} {d.name_ru}{d.manufacturer && ` (${d.manufacturer})`}</a>)}
              <button className="btn ghost" onClick={() => save(true)}>всё равно сохранить новую</button></div>)}

          {nv && cur.id && <PartSummary cur={cur} />}
          {nv && <nav className="pc-secnav" aria-label="Разделы карточки">{SECTIONS.map(([id, t]) => <a key={id} href={`#${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>{t}</a>)}</nav>}
          <h3 id="pc-main">Основное</h3>
          <div className="grid cols-3">
            {txt("Артикул (основной номер) *", "article")}{txt("Наименование *", "name_ru")}{txt("Единица измерения *", "unit", "шт")}
            <label className="f">Группа товара<input list="part-groups" value={cur.part_group} disabled={!edit} onChange={(e) => set({ part_group: e.target.value })} />
              <datalist id="part-groups">{meta?.groups.map((g) => <option key={g} value={g} />)}</datalist></label>
            <label className="f">Статус<select value={cur.status} disabled={!edit} onChange={(e) => set({ status: e.target.value })}>{Object.entries(meta?.status || {}).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
            {(["en", "cn", "de"] as const).map((l) => (
              <label key={l} className="f">Наименование {l.toUpperCase()}<input value={cur.names[l] || ""} disabled={!edit} onChange={(e) => set({ names: { ...cur.names, [l]: e.target.value } })} /></label>))}
          </div>
          <label className="f" style={{ marginTop: 6 }}>Описание, примечание<textarea rows={2} value={cur.description} disabled={!edit} onChange={(e) => set({ description: e.target.value })} /></label>

          <h3 id="pc-nums">Номера</h3>
          <div className="grid cols-3">{txt("Производитель", "manufacturer")}{txt("Артикул производителя (OEM)", "oem_number")}</div>
          {cur.cross_refs.map((c, i) => (
            <div key={i} className="row" style={{ marginBottom: 4 }}>
              <select value={c.kind} disabled={!edit} onChange={(e) => set({ cross_refs: cur.cross_refs.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)) })}>{Object.entries(meta?.cross_kinds || {}).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
              <input placeholder="номер" value={c.number} disabled={!edit} onChange={(e) => set({ cross_refs: cur.cross_refs.map((x, j) => (j === i ? { ...x, number: e.target.value } : x)) })} />
              {c.kind === "supplier" ? <select value={c.supplier_id || ""} disabled={!edit} onChange={(e) => set({ cross_refs: cur.cross_refs.map((x, j) => (j === i ? { ...x, supplier_id: e.target.value || null } : x)) })}><option value="">— поставщик —</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select>
                : <input placeholder="производитель аналога" value={c.manufacturer} disabled={!edit} onChange={(e) => set({ cross_refs: cur.cross_refs.map((x, j) => (j === i ? { ...x, manufacturer: e.target.value } : x)) })} />}
              {edit && <button className="btn ghost" onClick={() => set({ cross_refs: cur.cross_refs.filter((_, j) => j !== i) })}>удалить</button>}
            </div>))}
          {edit && <button className="btn add" onClick={() => set({ cross_refs: [...cur.cross_refs, { kind: "supplier", number: "", supplier_id: null, manufacturer: "", comment: "" }] })}>Номер или аналог</button>}

          <h3 id="pc-appl">Применимость</h3>
          {cur.applicability.map((a, i) => (
            <div key={a.id || i} className={nv ? "pc-appl" : "row"} style={nv ? undefined : { marginBottom: 4 }}>
              {nv && <ApplStatus a={a} edit={edit} onChange={(patch) => set({ applicability: cur.applicability.map((x, j) => (j === i ? { ...x, ...patch } : x)) })} />}
              {a.model_title ? <span className="badge">{a.model_title}</span> : a.equipment_model_id ? <span className="badge">{models.find((m) => m.id === a.equipment_model_id)?.title || "модель каталога"}</span> : <>
                <input placeholder="производитель машины" value={a.other_manufacturer} disabled={!edit} onChange={(e) => set({ applicability: cur.applicability.map((x, j) => (j === i ? { ...x, other_manufacturer: e.target.value } : x)) })} />
                <input placeholder="модель" value={a.other_model} disabled={!edit} onChange={(e) => set({ applicability: cur.applicability.map((x, j) => (j === i ? { ...x, other_model: e.target.value } : x)) })} /></>}
              <input placeholder="узел / агрегат" value={a.assembly} disabled={!edit} onChange={(e) => set({ applicability: cur.applicability.map((x, j) => (j === i ? { ...x, assembly: e.target.value } : x)) })} />
              {edit && <button className="btn ghost" onClick={() => set({ applicability: cur.applicability.filter((_, j) => j !== i) })}>удалить</button>}
            </div>))}
          {edit && nv && models.length > 0 && <select className="pc-addmodel" value="" aria-label="Добавить модель из каталога ГШО" onChange={(e) => { if (e.target.value) set({ applicability: [...cur.applicability, { equipment_model_id: e.target.value, other_manufacturer: "", other_model: "", assembly: "" }] }); }}>
            <option value="">+ модель из каталога ГШО…</option>{models.filter((m) => !cur.applicability.some((a) => a.equipment_model_id === m.id)).map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</select>}
          {edit && <button className="btn add" onClick={() => set({ applicability: [...cur.applicability, { equipment_model_id: null, other_manufacturer: "", other_model: "", assembly: "" }] })}>{nv ? "Модель другого производителя" : "Модель машины"}</button>}

          <h3 id="pc-buy">Закупка</h3>
          <div className="grid cols-3">
            <label className="f">Основной поставщик<select value={cur.supplier_id || ""} disabled={!edit} onChange={(e) => set({ supplier_id: e.target.value || null })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select></label>
            <label className="f">Маршрут по умолчанию<select value={cur.route_id || ""} disabled={!edit} onChange={(e) => set({ route_id: e.target.value || null })}><option value="">—</option>{routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
            {seesPurchase && <label className="f">Последняя закупочная цена<div className="row"><input type="number" value={cur.purchase_price ?? ""} disabled={!edit} onChange={(e) => set({ purchase_price: n(e.target.value) })} />{curSel("purchase_currency")}
              <input type="date" value={cur.purchase_on || ""} disabled={!edit} onChange={(e) => set({ purchase_on: e.target.value || null })} /></div></label>}
            {numF("Срок поставки поставщика, дн", "lead_days")}{txt("Код ТН ВЭД", "hs_code")}{txt("Страна происхождения", "origin_country")}
            {numF("Минимальная партия", "moq")}{numF("Кратность", "multiple")}
          </div>

          <h3 id="pc-log">Логистика</h3>
          <div className="grid cols-3">{numF("Вес, кг", "weight_kg")}{numF("Длина, мм", "length_mm")}{numF("Ширина, мм", "width_mm")}{numF("Высота, мм", "height_mm")}{numF("Объём, м³ (если не заданы габариты)", "volume_m3")}</div>

          <h3 id="pc-stock">Склад</h3>
          <table>
            <thead><tr><th>Склад</th><th className="num">Остаток</th><th className="num">Резерв</th><th className="num">В пути</th><th className="num">Доступно</th><th className="num">Минимальный остаток</th></tr></thead>
            <tbody>{warehouses.map((w) => {
              const s = (cur.stock || []).find((x) => x.warehouse_id === w.id);
              return (
                <tr key={w.id}><td>{w.name}{w.kind === "virtual" && <span className="badge" style={{ marginLeft: 4 }}>виртуальный</span>}</td>
                  <td className="num">{s?.qty || 0}</td><td className="num">{s?.reserved || 0}</td><td className="num">{s?.in_transit || 0}</td><td className="num">{s?.available ?? (s?.qty || 0)}</td>
                  <td className="num"><input type="number" style={{ width: 80 }} value={s?.min_qty ?? ""} disabled={!edit} onChange={(e) => {
                    const v = n(e.target.value), rest = (cur.stock || []).filter((x) => x.warehouse_id !== w.id);
                    set({ stock: [...rest, { ...(s || { warehouse_id: w.id }), min_qty: v }] });
                  }} /></td></tr>);
            })}{warehouses.length === 0 && <tr><td colSpan={6} className="muted">Склады появятся после переноса из МойСклад</td></tr>}</tbody>
          </table>

          <h3 id="pc-price">Цены</h3>
          <div className="grid cols-3">
            <label className="f">Цена продажи<div className="row"><input type="number" value={cur.sale_price ?? ""} disabled={!edit} onChange={(e) => set({ sale_price: n(e.target.value) })} />{curSel("sale_currency")}</div></label>
            <label className="f">Минимальная цена продажи<div className="row"><input type="number" value={cur.min_price ?? ""} disabled={!edit} onChange={(e) => set({ min_price: n(e.target.value) })} />{curSel("min_price_currency")}</div></label>
          </div>
          <p className="muted">Себестоимость считается модулем по маршруту и курсам ЦБ — её видят КД, ФД и ЗГД.</p>
          {cur.client_prices.map((c, i) => (
            <div key={i} className="row" style={{ marginBottom: 4 }}>
              <select value={c.client_id || ""} disabled={!edit} onChange={(e) => set({ client_prices: cur.client_prices.map((x, j) => (j === i ? { ...x, client_id: e.target.value || null } : x)) })}>
                <option value="">{c.client_name ? `${c.client_name} (не сопоставлен)` : "— клиент —"}</option>{clients.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}</select>
              <input type="number" style={{ width: 110 }} value={c.price} disabled={!edit} onChange={(e) => set({ client_prices: cur.client_prices.map((x, j) => (j === i ? { ...x, price: Number(e.target.value) } : x)) })} />
              <select value={c.currency} disabled={!edit} onChange={(e) => set({ client_prices: cur.client_prices.map((x, j) => (j === i ? { ...x, currency: e.target.value } : x)) })}>{CUR.map((x) => <option key={x}>{x}</option>)}</select>
              <input type="date" value={c.valid_from || ""} disabled={!edit} onChange={(e) => set({ client_prices: cur.client_prices.map((x, j) => (j === i ? { ...x, valid_from: e.target.value || null } : x)) })} />
              <input type="date" value={c.valid_to || ""} disabled={!edit} onChange={(e) => set({ client_prices: cur.client_prices.map((x, j) => (j === i ? { ...x, valid_to: e.target.value || null } : x)) })} />
              {edit && <button className="btn ghost" onClick={() => set({ client_prices: cur.client_prices.filter((_, j) => j !== i) })}>удалить</button>}
            </div>))}
          {edit && <button className="btn add" onClick={() => set({ client_prices: [...cur.client_prices, { client_id: null, client_name: "", price: 0, currency: "EUR", valid_from: null, valid_to: null }] })}>Договорная цена клиента</button>}

          <h3 id="pc-files">Файлы и сделки</h3>
          {cur.id && (cur.photo_files?.length ?? 0) > 0 && (
            <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 6 }}>
              {cur.photo_files!.map((f, i) => (
                <div key={`${cur.id}-${i}-${f.name}`} style={{ textAlign: "center" }}>
                  <a href={`/api/parts/catalog/${cur.id}/photo/${i}`} target="_blank" rel="noreferrer">
                    <img src={`/api/parts/catalog/${cur.id}/photo/${i}`} alt={f.name} loading="lazy" style={{ width: 120, height: 120, objectFit: "cover", borderRadius: 6, border: "1px solid var(--line)" }} /></a>
                  {edit && <div><a href="#" style={{ fontSize: 11 }} onClick={async (e) => { e.preventDefault(); if ((await askConfirm("Убрать фото из карточки?"))) api.del<{ photo_files: { name: string }[] }>(`/api/parts/catalog/${cur.id}/photo/${i}`).then((x) => set({ photo_files: x.photo_files })); }}>убрать</a></div>}
                </div>))}
            </div>)}
          <div className="grid cols-3">
            <label className="f">Фото (ссылки, через запятую)<input value={cur.photos.join(", ")} disabled={!edit} onChange={(e) => set({ photos: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
            {txt("Спецификация, №", "spec_no")}{txt("Доп. соглашение, №", "addendum_no")}
          </div>

          <div className={"row pc-footrow" + (nv ? " pc-foot" : "")}>
            {nv && edit && <span className={"pc-dirty" + (JSON.stringify(cur) !== snap ? " on" : "")}>{!cur.id ? "новая карточка — не сохранена" : JSON.stringify(cur) !== snap ? "есть несохранённые изменения" : "изменений нет"}</span>}
            {edit && <button className="btn" onClick={() => save(false)} disabled={!cur.article || !cur.name_ru || !cur.unit.trim()}>Сохранить карточку</button>}
            <button className="btn ghost" onClick={() => setCur(null)}>закрыть</button>
          </div>
        </div>) : null;
  const inStockLbl = (r: Row) => {
    const avail = r.stock_available ?? r.stock_total;
    const extra = [r.stock_reserved ? `резерв ${fmtRub(r.stock_reserved, 0)}` : "", r.stock_in_transit ? `в пути ${fmtRub(r.stock_in_transit, 0)}` : ""].filter(Boolean).join(" · ");
    return <div className="pc-stockcell">
      {r.stock_total > 0
        ? <span className={"pc-stock " + (avail > 0 ? "ok" : "none")} title={`Остаток ${r.stock_total}, доступно ${avail}`}>{avail > 0 ? `${fmtRub(avail, 0)} ${r.unit}` : "всё в резерве"}{avail !== r.stock_total && <small> из {fmtRub(r.stock_total, 0)}</small>}</span>
        : <span className={"pc-stock none"}>{r.stock_in_transit ? "ожидается" : "нет в наличии"}</span>}
      {extra && <span className="pc-stock-sub">{extra}</span>}
      {(r.below_min_count || 0) > 0 && <span className="pc-stock low" title="Свободный остаток ниже минимума хотя бы на одном складе">ниже минимума{(r.below_min_count || 0) > 1 ? ` · ${r.below_min_count} скл.` : ""}</span>}
    </div>;
  };
  const listNv = (
    <div className="card pc-listcard">
      <div className="pc-toolbar">
        <input className="pc-search" placeholder="Артикул, наименование, OEM или номер поставщика" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск запчастей" />
        <div className="dc-groups" role="group" aria-label="Статус карточек">
          {([["active", "Активные"], ["archive", "Архив"], ["", "Все"]] as const).map(([k, t]) => <button key={k} type="button" className={status === k ? "on" : ""} aria-pressed={status === k} onClick={() => setStatus(k)}>{t}</button>)}
        </div>
        <label className="pc-toggle"><input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} /> только в наличии</label>
        <label className="pc-toggle"><input type="checkbox" checked={belowMin} onChange={(e) => setBelowMin(e.target.checked)} /> ниже минимума</label>
        <span className="pc-actions">
          {meta?.can_import && <button className="btn ghost" onClick={() => setShowImport(!showImport)}>Перенос из МойСклад</button>}
          {edit && <button className="btn add primary" onClick={() => { setCur({ ...EMPTY }); setSnap(""); setDups([]); setMsg(""); }}>Запчасть</button>}
        </span>
      </div>
      {(meta?.groups.length || 0) > 0 && <div className="pc-groups" role="group" aria-label="Группа товара">
        <button type="button" className={!group ? "on" : ""} aria-pressed={!group} onClick={() => setGroup("")}>Все группы</button>
        {meta!.groups.map((g) => <button key={g} type="button" className={group === g ? "on" : ""} aria-pressed={group === g} onClick={() => setGroup(group === g ? "" : g)}>{g}</button>)}
      </div>}
      {showImport && <ImportPanel warehouses={warehouses} onDone={() => { load(); api.get<Meta>("/api/parts/meta/catalog").then(setMeta); api.get<typeof warehouses>("/api/parts/warehouses").then(setWarehouses); }} />}
      <div className="pc-count" aria-live="polite">{loaded ? <>Найдено: <b>{list.total}</b>{(q || group || inStock || belowMin || status !== "active") && <button type="button" className="dl-chip-all" onClick={() => { setQ(""); setGroup(""); setInStock(false); setBelowMin(false); setStatus("active"); }}>сбросить отбор</button>}</> : "…"}</div>
      {!loaded ? <Loading /> : list.items.length === 0 ? (
        <div className="st-box st-empty"><div className="st-title">{q ? `По запросу «${q}» запчастей нет` : "Запчастей нет"}</div>
          <div className="muted">{q ? "Поиск идёт по артикулу, наименованию, OEM и номерам поставщиков." : edit ? "Добавьте карточку или перенесите каталог из МойСклад." : "Карточки заводят менеджер, КД и ЗГД."}</div></div>) : (
        <table ref={tblRef} className="pc-table nv-cards">
          <thead><tr><th>Артикул</th><th>Наименование</th><th>Производитель · OEM</th><th className="num">Наличие</th><th className="num">Цена продажи</th></tr></thead>
          <tbody>{list.items.map((r) => (
            <tr key={r.id} className={(cur?.id === r.id ? "cur " : "") + (r.status === "archive" ? "arch" : "")} tabIndex={0} onClick={() => open(r.id)} onKeyDown={(e) => { if (e.key === "Enter") open(r.id); }}>
              <td className="pc-art">{r.article}</td>
              <td><div className="pc-name">{r.name_ru}</div><div className="pc-sub">{r.part_group || "без группы"}{r.has_client_prices && <span className="badge info">договорная цена</span>}{r.status === "archive" && <span className="badge">архив</span>}</div></td>
              <td>{r.manufacturer || <span className="muted">—</span>}{r.oem_number && <div className="pc-sub pc-mono">{r.oem_number}</div>}</td>
              <td className="num">{inStockLbl(r)}</td>
              <td className="num pc-price">{r.sale_price ? `${fmtRub(r.sale_price, 2)} ${r.sale_currency}` : <span className="muted">—</span>}</td></tr>))}</tbody>
        </table>)}
      {list.total > PAGE && <div className="row pc-pager">
        <span className="muted">{`${page * PAGE + 1}–${Math.min((page + 1) * PAGE, list.total)} из ${list.total}`}</span>
        <button className="btn ghost" disabled={page === 0} onClick={() => { setPage(page - 1); load(page - 1); }}>← назад</button>
        <button className="btn ghost" disabled={(page + 1) * PAGE >= list.total} onClick={() => { setPage(page + 1); load(page + 1); }}>вперёд →</button>
      </div>}
    </div>);
  if (nv) return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className={"pc-shell" + (cur ? " with-card" : "")}>
        {listNv}
        {cur && <aside className="pc-panel" aria-label="Карточка запчасти">
          <button type="button" className="btn ghost pc-close" onClick={() => setCur(null)} aria-label="Закрыть карточку">✕</button>
          {cardEl}</aside>}
      </div>
    </>);
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {listEl}
      {cardEl}
    </>
  );
}

type ImportRes = { report: { rows: number; duplicate_articles?: string[]; clients?: string[]; warehouses?: string[]; filled?: Record<string, number>; no_article?: number };
  result: { created?: number; updated?: number; unmatched_clients?: Record<string, number>; total_parts?: number; dry_run?: boolean; missing_count?: number; ambiguous_articles?: string[] } };

function ImportPanel({ warehouses, onDone }: { warehouses: { id: string; name: string }[]; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [stockFile, setStockFile] = useState<File | null>(null);
  const [wh, setWh] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ImportRes | null>(null);
  const [err, setErr] = useState("");
  async function run(dry: boolean) {
    if (!file) return;
    setBusy(true); setErr("");
    try { setRes(await api.upload<ImportRes>("/api/parts/import/moysklad-products", file, { dry_run: String(dry) })); if (!dry) onDone(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function runStock() {
    if (!stockFile) return;
    setBusy(true); setErr("");
    try { setRes(await api.upload<ImportRes>("/api/parts/import/stock", stockFile, { default_warehouse: wh })); onDone(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 10, marginTop: 8 }}>
      <b>Перенос из МойСклад</b>
      <p className="muted" style={{ marginTop: 4 }}>1. В МойСклад: Товары → Экспорт → «Экспорт в Excel». Загрузите файл и нажмите «Проверить» — система покажет, что перенесётся, ничего не записывая. Затем «Перенести». Повторный перенос обновляет карточки по коду МойСклад.</p>
      <div className="row"><input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button className="btn ghost" disabled={!file || busy} onClick={() => run(true)}>Проверить</button>
        <button className="btn" disabled={!file || busy} onClick={() => run(false)}>Перенести</button></div>
      <p className="muted">2. Остатки: Склад → Остатки → «По складам» → Печать в Excel.</p>
      <div className="row"><input type="file" accept=".xlsx" onChange={(e) => setStockFile(e.target.files?.[0] || null)} />
        <select value={wh} onChange={(e) => setWh(e.target.value)}><option value="">склады — из файла</option>{warehouses.map((w) => <option key={w.id} value={w.name}>{w.name}</option>)}</select>
        <button className="btn" disabled={!stockFile || busy} onClick={runStock}>Загрузить остатки</button></div>
      <p className="muted">3. Фото: переносятся напрямую из МойСклад по коду товара; повторный запуск докачивает только новые фото.</p>
      <MsPhotos />
      {busy && <div className="muted">Обрабатываю файл… 25 000 строк — около минуты.</div>}
      {err && <div className="alert info">{err}</div>}
      {res && (
        <div style={{ marginTop: 6, fontSize: 13 }}>
          <div>Строк в файле: {res.report.rows}{res.result.dry_run ? " (проверка, ничего не записано)" : ""}. Будет создано / создано: {res.result.created ?? "—"}, обновлено: {res.result.updated ?? "—"}.</div>
          {res.report.filled && <div className="muted">Заполнено в источнике: {Object.entries(res.report.filled).map(([k, v]) => `${k} — ${v}`).join(", ")}</div>}
          {!!res.report.duplicate_articles?.length && <div>Дубли артикулов (разобрать вручную): {res.report.duplicate_articles.join(", ")}</div>}
          {!!res.report.no_article && <div>Без артикула: {res.report.no_article}</div>}
          {res.result.unmatched_clients && Object.keys(res.result.unmatched_clients).length > 0 && <div>Клиенты не найдены в базе — сопоставьте в карточках: {Object.entries(res.result.unmatched_clients).map(([k, v]) => `${k} (${v})`).join(", ")}</div>}
          {res.result.missing_count != null && <div>Остатки: обновлено {res.result.updated}, не найдено в каталоге {res.result.missing_count}{res.result.ambiguous_articles?.length ? `, неоднозначных артикулов ${res.result.ambiguous_articles.length}` : ""}.</div>}
          {res.result.total_parts != null && <div className="muted">Всего карточек в каталоге: {res.result.total_parts}. Проверено {fmtDate(new Date().toISOString())}.</div>}
        </div>)}
    </div>
  );
}

type PhotoState = { running: boolean; processed?: number; total?: number; with_images?: number; parts_updated?: number; photos_added?: number;
  not_found?: number; errors?: number; last_error?: string; finished_at?: string | null; token_set?: boolean;
  last_run?: { at: string; ok: boolean; details: { photos_added?: number; parts_updated?: number; not_found?: number; errors?: number } } | null };

function MsPhotos() {
  const [st, setSt] = useState<PhotoState | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.bg<PhotoState>("/api/parts/import/moysklad-photos").then(setSt).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!st?.running) return; const t = setInterval(load, 3000); return () => clearInterval(t); }, [st?.running]);
  async function start() {
    setErr("");
    try { setSt(await api.post<PhotoState>("/api/parts/import/moysklad-photos")); setTimeout(load, 1500); } catch (e) { setErr((e as Error).message); }
  }
  if (!st) return null;
  return (
    <div style={{ marginBottom: 6 }}>
      {!st.token_set && <div className="muted" style={{ fontSize: 12 }}>На сервере не задан токен МойСклад: владелец добавляет строку MOYSKLAD_TOKEN=… в backend/.env и перезапускает сервис.</div>}
      <button className="btn" disabled={!st.token_set || st.running} onClick={start}>{st.running ? "Переношу фото…" : "Перенести фото из МойСклад"}</button>
      {st.running && <span className="muted" style={{ marginLeft: 8 }}>товаров просмотрено {st.processed ?? 0} из {st.total || "…"}, фото добавлено {st.photos_added ?? 0}</span>}
      {!st.running && st.last_run && <div className="muted" style={{ fontSize: 12 }}>Последний перенос {fmtDate(st.last_run.at)}: {st.last_run.ok ? "готово" : "ошибка"}
        {st.last_run.details?.photos_added != null && ` — фото добавлено ${st.last_run.details.photos_added}, карточек ${st.last_run.details.parts_updated}, не найдено в каталоге ${st.last_run.details.not_found}, ошибок ${st.last_run.details.errors}`}</div>}
      {st.last_error && <div className="muted" style={{ fontSize: 12 }}>Последняя ошибка: {st.last_error}</div>}
      {err && <div className="alert info">{err}</div>}
    </div>
  );
}
