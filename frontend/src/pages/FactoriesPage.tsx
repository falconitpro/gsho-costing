import { useEffect, useState } from "react";
import { lbl, CATALOG_STATUS, PRICE_SOURCE } from "../labels";
import { UndoButton, useUndoHistory } from "../undo";
import { api, fmtDate, fmtRub } from "../api";
import { STATUS_TITLES } from "../types";
import { toneOf } from "../colors";
import { can, type Me } from "../types";

type Contact = {
  name: string; name_zh: string; position: string; roles: string[]; phone: string; mobile: string; email: string; wechat: string;
  wecom_id: string; whatsapp: string; telegram: string; language: string; primary: boolean; comment: string;
};
type Factory = {
  id?: string; name: string; name_zh: string; short_name: string; address: string; address_zh: string; country: string; province: string; city: string;
  website: string; uscc: string; supplier_status: string; contacts: Contact[]; payment_terms: unknown[];
  bank_details: { beneficiary?: string; bank?: string; swift?: string; account?: string; bank_address?: string };
  default_currency: string; default_basis: string; machine_types: string[]; trademarks: string[]; typical_production_days: number | null; rating: number | null;
  notes: string; is_active: boolean; approval_status?: string; can_approve?: boolean; created_by_name?: string; approved_by_name?: string; approved_at?: string | null;
  pending_changes?: Change[] | number; stats?: FStats; profiles_count?: number; legal_entity_id?: string | null; language?: string; supplies?: string[]; default_route_id?: string | null; models?: { id: string; model: string; machine_type: string; status: string }[]; deals?: number;
};
type Sums = Record<string, number> | null;
type FStats = { gsho: { deals: number; open: number; won: number; lost: number; last_on: string | null; last_number: string; last_status: string; sum_won: Sums; models: number };
  parts: { po: number; po_open: number; po_sum: Sums; last_on: string | null; last_number: string; last_status: string; rfq: number } };
const PO_T: Record<string, string> = { draft: "черновик", sent: "отправлен", confirmed: "подтверждён", in_progress: "в работе", closed: "закрыт", cancelled: "отменён" };
const sums = (x: Sums) => (x && Object.keys(x).length ? Object.entries(x).map(([c, v]) => `${fmtRub(v)} ${c}`).join(" · ") : "");
type Change = { id: string; proposed_by_name: string; proposed_at: string; comment: string; fields: { field: string; title: string; old: unknown; new: unknown }[] };
type Price = { on: string; model: string; unit_price: number; currency: string; basis: string; production_term: { value?: number; unit?: string }; source: string };

const EMPTY_CONTACT: Contact = { name: "", name_zh: "", position: "", roles: [], phone: "", mobile: "", email: "", wechat: "", wecom_id: "", whatsapp: "", telegram: "", language: "zh", primary: false, comment: "" };
const EMPTY: Factory = { name: "", name_zh: "", short_name: "", address: "", address_zh: "", country: "CN", province: "", city: "", website: "", uscc: "",
  supplier_status: "manufacturer", contacts: [], payment_terms: [], bank_details: {}, default_currency: "CNY", default_basis: "FCA", machine_types: [], trademarks: [],
  typical_production_days: null, rating: null, notes: "", is_active: true, supplies: ["gsho"] };
const STATUS: Record<string, string> = { manufacturer: "завод-изготовитель", official_dealer: "официальный дилер", trader: "торговая компания", group_company: "компания группы" };
const LANGS: Record<string, string> = { "": "—", zh: "китайский", ru: "русский", en: "английский", de: "немецкий" };

const showVal = (v: unknown): string => {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) return v.length && typeof v[0] === "object" ? v.map((x) => (x as { name?: string; email?: string }).name || (x as { email?: string }).email || "…").join(", ") : (v as unknown[]).join(", ") || "—";
  if (typeof v === "object") return Object.values(v as Record<string, unknown>).filter(Boolean).join(", ") || "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  return String(v);
};
const KINDS: Record<string, string> = { gsho: "ГШО", parts: "Запасные части" };

/** Справочник поставщиков (ГШО и запасных частей): реквизиты, контакты по направлениям (заявки, логистика, финансы, сервис…), WeChat / WeCom / почта, история цен. */
export default function FactoriesPage({ me, embedded = false }: { me: Me; embedded?: boolean }) {
  const [list, setList] = useState<Factory[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [cur, setCur] = useState<Factory | null>(null);
  const [prices, setPrices] = useState<Price[]>([]);
  const [msg, setMsg] = useState("");
  const history = useUndoHistory({ cur }, (st) => setCur(st.cur), cur?.id || (cur ? "new" : null));
  const editor = me.roles.some((r) => r.code !== "observer");          // заводят и правят все; утверждают КД или ЗГД
  const contactEditor = editor;
  const approver = me.roles.some((r) => ["cd", "gd"].includes(r.code));
  const isGD = me.roles.some((r) => r.code === "gd");
  const [confirmDel, setConfirmDel] = useState(false);
  const [entities, setEntities] = useState<{ id: string; name: string }[]>([]);
  const [kind, setKind] = useState<"gsho" | "parts" | "" | "approval">(() => { try { return (localStorage.getItem("suppliers_kind") as "gsho" | "parts" | "" | "approval") ?? "gsho"; } catch { return "gsho"; } });
  useEffect(() => { try { localStorage.setItem("suppliers_kind", kind); } catch { /* приватный режим */ } }, [kind]);
  const [routes, setRoutes] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => { api.get<{ id: string; name: string }[]>("/api/parts/routes").then(setRoutes).catch(() => setRoutes([])); }, []);
  useEffect(() => { api.get<{ id: string; name: string }[]>("/api/parts/entities").then(setEntities).catch(() => setEntities([])); }, []);
  async function remove() {
    if (!cur?.id) return;
    if (!confirmDel) { setConfirmDel(true); setMsg("Нажмите «Подтвердить удаление» ещё раз. Если завод есть в сделках или каталоге, он будет перенесён в архив."); return; }
    setConfirmDel(false);
    try { const r = await api.del<{ message: string }>(`/api/refs/factories/${cur.id}`); setMsg(r.message); setCur(null); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const load = () => api.get<Factory[]>("/api/refs/factories?stats=1").then(setList);
  const [view, setView] = useState<"all" | "active" | "won" | "none">("all");
  const [sort, setSort] = useState<{ k: string; d: 1 | -1 }>({ k: "last", d: -1 });
  useEffect(() => { load(); api.get<Record<string, string>>("/api/refs/factories/contact-roles").then(setRoles); }, []);
  async function open(id: string) {
    setConfirmDel(false);
    const f = await api.get<Factory>(`/api/refs/factories/${id}`);
    setCur({ ...EMPTY, ...f, bank_details: f.bank_details || {}, contacts: (f.contacts || []).map((c) => ({ ...EMPTY_CONTACT, ...c, roles: c.roles || [] })) });
    setMsg("");
    if (can(me, "factory_price_history")) api.get<Price[]>(`/api/refs/factories/${id}/prices`).then(setPrices).catch(() => setPrices([])); else setPrices([]);
  }
  async function save() {
    if (!cur) return;
    try {
      const { id, models, deals, pending_changes, ...body } = cur;
      void models; void deals; void pending_changes;
      const r = id ? await api.put<Factory & { proposed?: boolean }>(`/api/refs/factories/${id}`, body) : await api.post<Factory>("/api/refs/factories", body);
      await open(r.id!); load();
      setMsg((r as { proposed?: boolean }).proposed ? "Изменения отправлены на утверждение КД / ЗГД — в карточке они появятся после утверждения"
        : r.approval_status === "pending" ? "Сохранено. Поставщик на утверждении КД / ЗГД" : "Сохранено");
    } catch (e) { setMsg((e as Error).message); }
  }
  const set = (p: Partial<Factory>) => cur && setCur({ ...cur, ...p });
  const setC = (i: number, p: Partial<Contact>) => cur && setCur({ ...cur, contacts: cur.contacts.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const waiting = (f: Factory) => f.approval_status === "pending" || Number(f.pending_changes || 0) > 0;
  const shown = list.filter((f) => (kind === "approval" ? waiting(f) : (!kind || (f.supplies || ["gsho"]).includes(kind))) && (!q || `${f.name} ${f.name_zh} ${f.short_name} ${f.city} ${f.province} ${(f.trademarks || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()))
    && (!roleFilter || (f.contacts || []).some((c) => (c.roles || []).includes(roleFilter))));
  const field = (label: string, key: keyof Factory, ph = "") => (
    <label className="f">{label}<input value={String(cur?.[key] ?? "")} placeholder={ph} disabled={!editor} onChange={(e) => set({ [key]: e.target.value } as Partial<Factory>)} /></label>);

  return (
    <>
      <div className="page-head">
        {!embedded && <h1>Поставщики</h1>}
        <div className="row" style={{ marginLeft: embedded ? 0 : 12 }}>{([["gsho", "ГШО"], ["parts", "Запасные части"], ["", "Все"]] as const).map(([k, t]) => (
          <button key={k} className={`btn ${kind === k ? "" : "ghost"}`} onClick={() => setKind(k)}>{t} <span className="muted">({list.filter((f) => !k || (f.supplies || ["gsho"]).includes(k)).length})</span></button>))}
          {list.some(waiting) && <button className={`btn ${kind === "approval" ? "" : "ghost"}`} onClick={() => setKind("approval")}>На утверждении <span className="badge bad">{list.filter(waiting).length}</span></button>}</div>
        <div className="push">{editor && <button className="btn add" onClick={() => { setCur({ ...EMPTY, supplies: [kind || "gsho"], contacts: [{ ...EMPTY_CONTACT, roles: ["rfq"], primary: true }] }); setPrices([]); setMsg(""); }}>Добавить поставщика</button>}</div>
      </div>
      {msg && <div className="alert info">{msg}</div>}
      {(() => {
        const fin = list.some((f) => f.stats?.gsho.sum_won != null);
        const st = (f: Factory): FStats => f.stats || { gsho: { deals: 0, open: 0, won: 0, lost: 0, last_on: null, last_number: "", last_status: "", sum_won: null, models: 0 }, parts: { po: 0, po_open: 0, po_sum: null, last_on: null, last_number: "", last_status: "", rfq: 0 } };
        const showG = kind !== "parts", showP = kind !== "gsho";
        const activity = (f: Factory) => (showG ? st(f).gsho.open : 0) + (showP ? st(f).parts.po_open : 0);
        const wins = (f: Factory) => (showG ? st(f).gsho.won : 0) + (showP ? st(f).parts.po : 0);
        const lastOn = (f: Factory) => [showG ? st(f).gsho.last_on : null, showP ? st(f).parts.last_on : null].filter(Boolean).sort().pop() || "";
        const rows = shown.filter((f) => view === "all" || (view === "active" ? activity(f) > 0 : view === "won" ? wins(f) > 0 : st(f).gsho.deals + st(f).parts.po === 0))
          .sort((a, b) => { const A = st(a), B = st(b); const v = sort.k === "name" ? (a.short_name || a.name).localeCompare(b.short_name || b.name, "ru") : sort.k === "deals" ? A.gsho.deals - B.gsho.deals : sort.k === "open" ? A.gsho.open - B.gsho.open
            : sort.k === "won" ? A.gsho.won - B.gsho.won : sort.k === "po" ? A.parts.po - B.parts.po : sort.k === "po_open" ? A.parts.po_open - B.parts.po_open : sort.k === "rating" ? (a.rating || 0) - (b.rating || 0) : lastOn(a).localeCompare(lastOn(b)); return v * sort.d; });
        const th = (k: string, t: string, num = false) => <th className={num ? "num" : undefined} style={{ cursor: "pointer", whiteSpace: "nowrap" }} onClick={() => setSort({ k, d: sort.k === k ? (sort.d === 1 ? -1 : 1) : (k === "name" ? 1 : -1) })}>{t}{sort.k === k ? (sort.d === 1 ? " ↑" : " ↓") : ""}</th>;
        const kpis: [string, string | number][] = [["поставщиков", shown.length], ["с активными сделками / заказами", shown.filter((f) => activity(f) > 0).length],
          ...(showG ? [["выигранных сделок ГШО", shown.reduce((a, f) => a + st(f).gsho.won, 0)] as [string, number]] : []), ...(showP ? [["заказов запчастей", shown.reduce((a, f) => a + st(f).parts.po, 0)] as [string, number]] : []),
          ["без сделок и заказов", shown.filter((f) => st(f).gsho.deals + st(f).parts.po === 0).length]];
        const statusBadge = (code: string, title: string) => <span className="badge" style={{ background: toneOf(code)[0], color: toneOf(code)[1], fontSize: 11 }}>{title}</span>;
        const table = (
          <div className="card">
            <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
              <input placeholder="🔍 название, марка, город" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 220px" }} />
              <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} title="есть контакт по направлению"><option value="">все контакты</option>{Object.entries(roles).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
              <div className="row" style={{ gap: 2 }}>{([["all", "все"], ["active", "с активными"], ["won", showG ? "с выигранными" : "с заказами"], ["none", "без сделок"]] as const).map(([k, t]) => (
                <button key={k} onClick={() => setView(k)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "6px 10px", fontWeight: view === k ? 700 : 500, borderBottom: view === k ? "2px solid var(--accent)" : "2px solid transparent", fontFamily: "inherit", fontSize: 13 }}>{t}</button>))}</div>
            </div>
            {!cur && <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
              {kpis.map(([t, v]) => <div key={t} style={{ flex: "1 1 120px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}><div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{t}</div></div>)}
            </div>}
            <div style={{ overflowX: "auto", marginTop: 10 }}>
              <table style={{ fontSize: 13 }}>
                <thead><tr>{th("name", "Поставщик")}<th>Тип</th>
                  {showG && <>{th("deals", "Сделок ГШО", true)}{th("open", "В работе", true)}{th("won", "Выиграно", true)}{fin && !cur && <th className="num">Сумма выигранных</th>}{!cur && th("last", "Последняя сделка")}</>}
                  {showP && <>{th("po", "Заказов ЗЧ", true)}{th("po_open", "Открытых", true)}{fin && !cur && <th className="num">Сумма заказов</th>}{!cur && kind === "parts" && th("last", "Последний заказ")}</>}
                  {!cur && th("rating", "Рейтинг", true)}{!cur && <th>Контакты</th>}{!cur && <th title="юридический профиль">Юр. профиль</th>}</tr></thead>
                <tbody>{rows.map((f) => { const x = st(f); const rfq = (f.contacts || []).find((c) => (c.roles || []).includes("rfq")); return (
                  <tr key={f.id} style={{ cursor: "pointer", background: cur?.id === f.id ? "var(--accent-soft)" : undefined, opacity: f.is_active === false ? 0.5 : 1 }} onClick={() => open(f.id!)}>
                    <td style={{ minWidth: 260 }}><b>{f.short_name || f.name}</b>{(f.trademarks || []).map((t) => <span key={t} className="badge accent" style={{ marginLeft: 4, whiteSpace: "nowrap" }}>{t}</span>)}{f.approval_status === "pending" && <span className="badge warn" style={{ marginLeft: 4 }}>на утверждении</span>}{Number(f.pending_changes || 0) > 0 && <span className="badge warn" style={{ marginLeft: 4 }}>изменения</span>}
                      <div className="muted" style={{ fontSize: 11 }}>{[f.name_zh, [f.city, f.province].filter(Boolean).join(", ")].filter(Boolean).join(" · ")}{!kind && (f.supplies || []).map((x2) => <span key={x2} className="badge" style={{ marginLeft: 4, whiteSpace: "nowrap" }}>{KINDS[x2]}</span>)}</div></td>
                    <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{STATUS[f.supplier_status] || "—"}</td>
                    {showG && <>
                      <td className="num">{x.gsho.deals || <span className="muted">—</span>}{x.gsho.models > 0 && !cur && <div className="muted" style={{ fontSize: 11 }}>{x.gsho.models} мод.</div>}</td>
                      <td className="num">{x.gsho.open ? <span className="badge info">{x.gsho.open}</span> : <span className="muted">—</span>}</td>
                      <td className="num">{x.gsho.won ? <span className="badge ok">{x.gsho.won}</span> : <span className="muted">—</span>}{x.gsho.lost > 0 && <span className="muted" style={{ fontSize: 11 }}> / {x.gsho.lost} пр.</span>}</td>
                      {fin && !cur && <td className="num" style={{ whiteSpace: "nowrap" }}>{sums(x.gsho.sum_won) || <span className="muted">—</span>}</td>}
                      {!cur && <td style={{ whiteSpace: "nowrap" }}>{x.gsho.last_number ? <>{x.gsho.last_number} {statusBadge(x.gsho.last_status, STATUS_TITLES[x.gsho.last_status] || x.gsho.last_status)}<div className="muted" style={{ fontSize: 11 }}>{fmtDate(x.gsho.last_on)}</div></> : <span className="muted">—</span>}</td>}</>}
                    {showP && <>
                      <td className="num">{x.parts.po || <span className="muted">—</span>}{x.parts.rfq > 0 && !cur && <div className="muted" style={{ fontSize: 11 }}>{x.parts.rfq} запр.</div>}</td>
                      <td className="num">{x.parts.po_open ? <span className="badge info">{x.parts.po_open}</span> : <span className="muted">—</span>}</td>
                      {fin && !cur && <td className="num" style={{ whiteSpace: "nowrap" }}>{sums(x.parts.po_sum) || <span className="muted">—</span>}</td>}
                      {!cur && kind === "parts" && <td style={{ whiteSpace: "nowrap" }}>{x.parts.last_number ? <>{x.parts.last_number} <span className="badge" style={{ fontSize: 11 }}>{PO_T[x.parts.last_status] || x.parts.last_status}</span><div className="muted" style={{ fontSize: 11 }}>{fmtDate(x.parts.last_on)}</div></> : <span className="muted">—</span>}</td>}</>}
                    {!cur && <td className="num">{f.rating ? `${f.rating} / 5` : <span className="muted">—</span>}</td>}
                    {!cur && <td className="muted" style={{ fontSize: 11 }}>{(f.contacts || []).length} конт.{rfq ? ` · заявки: ${rfq.name}` : <span style={{ color: "var(--warn)" }}> · нет контакта по заявкам</span>}</td>}
                    {!cur && <td className="num">{f.profiles_count ? f.profiles_count : <span className="badge warn" title="юридический профиль не заполнен">нет</span>}</td>}
                  </tr>); })}
                  {rows.length === 0 && <tr><td colSpan={12} className="muted">Не найдено</td></tr>}</tbody>
              </table>
            </div>
          </div>);
        return <div className="grid" style={{ gridTemplateColumns: cur ? "minmax(0, 2fr) minmax(0, 3fr)" : "1fr" }}>
        {table}
        {cur && <div>
          {!cur ? null : (
            <>
              <div className="card">
                <h2 style={{ marginTop: 0 }}>{cur.id ? cur.name : "Новый поставщик"} {!cur.is_active && <span className="badge bad">не работаем</span>}</h2>
                {cur.id && cur.approval_status === "pending" && (
                  <div className="alert info">Новый поставщик{cur.created_by_name && ` (завёл ${cur.created_by_name})`} — ждёт утверждения КД или ЗГД. Пока правки сохраняются сразу; заказ у него оформить нельзя.
                    {approver && <div className="row" style={{ marginTop: 6 }}><button className="btn" onClick={() => api.post(`/api/refs/factories/${cur.id}/approve`, {}).then(() => { open(cur.id!); load(); setMsg("Поставщик утверждён"); }).catch((e) => setMsg((e as Error).message))}>Утвердить</button>
                      <button className="btn ghost" onClick={() => { const c = prompt("Почему не утверждаем? Карточка уйдёт в архив."); if (c !== null) api.post(`/api/refs/factories/${cur.id}/reject`, { comment: c }).then(() => { open(cur.id!); load(); setMsg("Не утверждён — в архиве"); }).catch((e) => setMsg((e as Error).message)); }}>Не утверждать</button></div>}</div>)}
                {cur.id && cur.approval_status === "rejected" && <div className="alert info">Поставщик не утверждён (в архиве).</div>}
                {cur.id && cur.approval_status === "approved" && !approver && <p className="muted" style={{ marginTop: 0 }}>Карточка утверждена{cur.approved_by_name && ` (${cur.approved_by_name})`}. Ваши правки уйдут на утверждение КД или ЗГД и появятся в карточке после него.</p>}
                {Array.isArray(cur.pending_changes) && cur.pending_changes.map((ch) => (
                  <div key={ch.id} className="alert info">
                    <b>Предложены изменения</b> — {ch.proposed_by_name}, {fmtDate(ch.proposed_at)}{ch.comment && ` · ${ch.comment}`}
                    <table style={{ marginTop: 4 }}><tbody>{ch.fields.map((x) => (
                      <tr key={x.field}><td>{x.title}</td><td className="muted">{showVal(x.old)}</td><td>→</td><td><b>{showVal(x.new)}</b></td></tr>))}</tbody></table>
                    {approver && <div className="row" style={{ marginTop: 4 }}>
                      <button className="btn" onClick={() => api.post(`/api/refs/factory-changes/${ch.id}/approve`, {}).then(() => { open(cur.id!); load(); setMsg("Изменения утверждены и внесены в карточку"); }).catch((e) => setMsg((e as Error).message))}>Утвердить изменения</button>
                      <button className="btn ghost" onClick={() => { const c = prompt("Причина отказа"); if (c !== null) api.post(`/api/refs/factory-changes/${ch.id}/reject`, { comment: c }).then(() => { open(cur.id!); load(); setMsg("Изменения отклонены"); }).catch((e) => setMsg((e as Error).message)); }}>Отклонить</button></div>}
                  </div>))}
                <div className="grid cols-3">
                  {field("Наименование (как в документах, EN)", "name")}{field("Наименование (中文)", "name_zh")}{field("Краткое название", "short_name")}
                  <label className="f">Что поставляет<div className="row">{Object.entries(KINDS).map(([k, t]) => (
                    <label key={k} className="row"><input type="checkbox" disabled={!editor} checked={(cur.supplies || []).includes(k)}
                      onChange={(e) => set({ supplies: e.target.checked ? [...(cur.supplies || []), k] : (cur.supplies || []).filter((x) => x !== k) })} /> {t}</label>))}</div></label>
                  <label className="f">Статус поставщика<select value={cur.supplier_status} disabled={!editor} onChange={(e) => set({ supplier_status: e.target.value })}>{Object.entries(STATUS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
                  {cur.supplier_status === "group_company" && <label className="f">Какая компания группы<select value={cur.legal_entity_id || ""} disabled={!editor} onChange={(e) => set({ legal_entity_id: e.target.value || null })}><option value="">— выберите —</option>{entities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
                  {(cur.supplies || []).includes("parts") && <label className="f">Маршрут поставки запчастей по умолчанию<select value={cur.default_route_id || ""} disabled={!editor} onChange={(e) => set({ default_route_id: e.target.value || null })}>
                    <option value="">— не задан —</option>{routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
                  <label className="f">Язык переписки<select value={cur.language || ""} disabled={!editor} onChange={(e) => set({ language: e.target.value })}>{Object.entries(LANGS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
                  {field("Единый код юрлица (统一社会信用代码)", "uscc", "18 знаков")}{field("Сайт", "website", "https://")}
                  {field("Провинция", "province")}{field("Город", "city")}{field("Страна", "country")}
                  {field("Адрес (рус./EN)", "address")}{field("Адрес (中文)", "address_zh")}
                  {(cur.supplies || []).includes("gsho") && <>
                  <label className="f">Торговые марки (через запятую; первая — по умолчанию)<input value={(cur.trademarks || []).join(", ")} placeholder="напр. SHILI" disabled={!editor}
                    onChange={(e) => set({ trademarks: e.target.value.split(",").map((x) => x.trimStart()) })} /></label>
                  <label className="f">Что выпускает (типы машин через запятую)<input value={cur.machine_types.join(", ")} disabled={!editor} onChange={(e) => set({ machine_types: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
                  <label className="f">Типовой срок производства, дн<input type="number" value={cur.typical_production_days ?? ""} disabled={!editor} onChange={(e) => set({ typical_production_days: e.target.value ? Number(e.target.value) : null })} /></label></>}
                  <label className="f">Валюта / базис по умолчанию<div className="row"><select value={cur.default_currency} disabled={!editor} onChange={(e) => set({ default_currency: e.target.value })}>{["CNY", "USD", "EUR", "KGS", "RUB"].map((c) => <option key={c}>{c}</option>)}</select>
                    <select value={cur.default_basis} disabled={!editor} onChange={(e) => set({ default_basis: e.target.value })}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select></div></label>
                  <label className="f">Оценка (1–5)<input type="number" min={1} max={5} step={0.5} value={cur.rating ?? ""} disabled={!editor} onChange={(e) => set({ rating: e.target.value ? Number(e.target.value) : null })} /></label>
                  <label className="f">Работаем<div className="row"><input type="checkbox" checked={cur.is_active} disabled={!editor} onChange={(e) => set({ is_active: e.target.checked })} /> {cur.is_active ? "да" : "нет — в архиве"}</div></label>
                </div>
                <label className="f" style={{ marginTop: 6 }}>Заметки<textarea rows={2} value={cur.notes} disabled={!editor} onChange={(e) => set({ notes: e.target.value })} /></label>
              </div>

              <div className="card">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <h2 style={{ margin: 0 }}>Контакты по направлениям</h2>
                  {contactEditor && <button className="btn add" onClick={() => set({ contacts: [...cur.contacts, { ...EMPTY_CONTACT }] })}>Контакт</button>}
                </div>
                <p className="muted" style={{ marginTop: 4 }}>Отметьте, по каким вопросам писать человеку. Запрос заводу уходит контактам «Заявки и КП»: в WeCom (ID внешнего контакта) и на почту.</p>
                {cur.contacts.length === 0 && <div className="muted">Контактов нет</div>}
                {cur.contacts.map((c, i) => (
                  <div key={i} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 8, marginBottom: 8, background: c.primary ? "#fafafa" : undefined }}>
                    <div className="grid cols-3">
                      <label className="f">Имя<input value={c.name} disabled={!contactEditor} onChange={(e) => setC(i, { name: e.target.value })} /></label>
                      <label className="f">Имя (中文)<input value={c.name_zh} disabled={!contactEditor} onChange={(e) => setC(i, { name_zh: e.target.value })} /></label>
                      <label className="f">Должность<input value={c.position} disabled={!contactEditor} onChange={(e) => setC(i, { position: e.target.value })} /></label>
                      <label className="f">E-mail<input value={c.email} disabled={!contactEditor} onChange={(e) => setC(i, { email: e.target.value })} /></label>
                      <label className="f">WeChat ID<input value={c.wechat} disabled={!contactEditor} onChange={(e) => setC(i, { wechat: e.target.value })} /></label>
                      <label className="f">WeCom — ID внешнего контакта<input value={c.wecom_id} disabled={!contactEditor} placeholder="wm…" onChange={(e) => setC(i, { wecom_id: e.target.value })} /></label>
                      <label className="f">Телефон<input value={c.phone} disabled={!contactEditor} onChange={(e) => setC(i, { phone: e.target.value })} /></label>
                      <label className="f">Мобильный<input value={c.mobile} disabled={!contactEditor} onChange={(e) => setC(i, { mobile: e.target.value })} /></label>
                      <label className="f">WhatsApp / Telegram<div className="row"><input value={c.whatsapp} placeholder="WhatsApp" disabled={!contactEditor} onChange={(e) => setC(i, { whatsapp: e.target.value })} /><input value={c.telegram} placeholder="Telegram" disabled={!contactEditor} onChange={(e) => setC(i, { telegram: e.target.value })} /></div></label>
                      <label className="f">Язык общения<select value={c.language} disabled={!contactEditor} onChange={(e) => setC(i, { language: e.target.value })}><option value="zh">китайский</option><option value="ru">русский</option><option value="en">английский</option></select></label>
                      <label className="f">Комментарий<input value={c.comment} disabled={!contactEditor} onChange={(e) => setC(i, { comment: e.target.value })} /></label>
                      <label className="f">Основной контакт<div className="row"><input type="checkbox" checked={c.primary} disabled={!contactEditor} onChange={(e) => setC(i, { primary: e.target.checked })} />
                        {contactEditor && <button className="btn ghost" style={{ padding: "1px 8px" }} onClick={() => set({ contacts: cur.contacts.filter((_, j) => j !== i) })}>удалить контакт</button>}</div></label>
                    </div>
                    <div className="row" style={{ marginTop: 4, flexWrap: "wrap" }}><span className="muted">Направления:</span>
                      {Object.entries(roles).map(([k, t]) => (
                        <label key={k} className="row" style={{ fontSize: 12 }}><input type="checkbox" checked={c.roles.includes(k)} disabled={!contactEditor}
                          onChange={(e) => setC(i, { roles: e.target.checked ? [...c.roles, k] : c.roles.filter((x) => x !== k) })} /> {t}</label>))}
                    </div>
                  </div>
                ))}
              </div>

              {can(me, "factory_price_history") && (
                <div className="card">
                  <h2 style={{ marginTop: 0 }}>Банковские реквизиты</h2>
                  <div className="grid cols-3">
                    {(["beneficiary", "bank", "swift", "account", "bank_address"] as const).map((k) => (
                      <label key={k} className="f">{{ beneficiary: "Получатель", bank: "Банк", swift: "SWIFT", account: "Счёт", bank_address: "Адрес банка" }[k]}
                        <input value={cur.bank_details[k] || ""} disabled={!editor} onChange={(e) => set({ bank_details: { ...cur.bank_details, [k]: e.target.value } })} /></label>))}
                  </div>
                </div>)}

              {(editor || contactEditor) && <div className="row" style={{ marginBottom: 12 }}><button className="btn" onClick={save} disabled={!cur.name}>{cur.id && cur.approval_status === "approved" && !approver ? "Предложить изменения" : "Сохранить"}</button><UndoButton h={history} /><button className="btn ghost" onClick={() => setCur(null)}>закрыть</button>
                {isGD && cur.id && <button className="btn ghost" style={{ marginLeft: "auto", color: "var(--bad)" }} title="только ЗГД; если завод уже есть в сделках или каталоге — переносится в архив" onClick={remove}>{confirmDel ? "Подтвердить удаление" : "Удалить поставщика"}</button>}</div>}

              {cur.id && (
                <div className="card">
                  <h2 style={{ marginTop: 0 }}>Модели в каталоге и цены</h2>
                  <div className="muted">Сделок с этим заводом: {cur.deals ?? 0}</div>
                  {(cur.models || []).length > 0 && <div style={{ marginTop: 4 }}>{(cur.models || []).map((m) => <span key={m.id} className="badge" style={{ marginRight: 4 }}>{m.model}{m.status !== "approved" ? ` (${lbl(CATALOG_STATUS, m.status)})` : ""}</span>)}</div>}
                  {prices.length > 0 && (
                    <table style={{ marginTop: 8 }}><thead><tr><th>Дата</th><th>Модель</th><th className="num">Цена</th><th>Базис</th><th>Срок производства</th><th>Источник</th></tr></thead>
                      <tbody>{prices.map((p, i) => <tr key={i}><td>{fmtDate(p.on)}</td><td>{p.model}</td><td className="num">{fmtRub(p.unit_price)} {p.currency}</td><td>{p.basis}</td>
                        <td>{p.production_term?.value ? `${p.production_term.value} ${p.production_term.unit === "weeks" ? "нед." : "дн."}` : "—"}</td><td className="muted">{lbl(PRICE_SOURCE, p.source)}</td></tr>)}</tbody></table>)}
                </div>)}
            </>
          )}
        </div>}
      </div>; })()}
    </>
  );
}
