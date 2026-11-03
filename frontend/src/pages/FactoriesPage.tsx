import { useEffect, useState } from "react";
import { lbl, CATALOG_STATUS, PRICE_SOURCE } from "../labels";
import { UndoButton, useUndoHistory } from "../undo";
import { api, fmtDate, fmtRub } from "../api";
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
  notes: string; is_active: boolean; models?: { id: string; model: string; machine_type: string; status: string }[]; deals?: number;
};
type Price = { on: string; model: string; unit_price: number; currency: string; basis: string; production_term: { value?: number; unit?: string }; source: string };

const EMPTY_CONTACT: Contact = { name: "", name_zh: "", position: "", roles: [], phone: "", mobile: "", email: "", wechat: "", wecom_id: "", whatsapp: "", telegram: "", language: "zh", primary: false, comment: "" };
const EMPTY: Factory = { name: "", name_zh: "", short_name: "", address: "", address_zh: "", country: "CN", province: "", city: "", website: "", uscc: "",
  supplier_status: "manufacturer", contacts: [], payment_terms: [], bank_details: {}, default_currency: "CNY", default_basis: "FCA", machine_types: [], trademarks: [],
  typical_production_days: null, rating: null, notes: "", is_active: true };
const STATUS: Record<string, string> = { manufacturer: "завод-изготовитель", official_dealer: "официальный дилер", trader: "торговая компания" };

/** Справочник заводов: реквизиты, контакты по направлениям (заявки, логистика, финансы, сервис…), WeChat / WeCom / почта, история цен. */
export default function FactoriesPage({ me }: { me: Me }) {
  const [list, setList] = useState<Factory[]>([]);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [cur, setCur] = useState<Factory | null>(null);
  const [prices, setPrices] = useState<Price[]>([]);
  const [msg, setMsg] = useState("");
  const history = useUndoHistory({ cur }, (st) => setCur(st.cur), cur?.id || (cur ? "new" : null));
  const editor = me.roles.some((r) => ["manager", "logistician", "cd", "gd", "admin"].includes(r.code));
  const contactEditor = me.roles.some((r) => ["manager", "cd", "gd"].includes(r.code));   // контакты завода — менеджер, КД, ЗГД
  const isGD = me.roles.some((r) => r.code === "gd");
  const [confirmDel, setConfirmDel] = useState(false);
  async function remove() {
    if (!cur?.id) return;
    if (!confirmDel) { setConfirmDel(true); setMsg("Нажмите «Подтвердить удаление» ещё раз. Если завод есть в сделках или каталоге, он будет перенесён в архив."); return; }
    setConfirmDel(false);
    try { const r = await api.del<{ message: string }>(`/api/refs/factories/${cur.id}`); setMsg(r.message); setCur(null); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const load = () => api.get<Factory[]>("/api/refs/factories").then(setList);
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
      const { id, models, deals, ...body } = cur;
      void models; void deals;
      const r = id ? await api.put<Factory>(`/api/refs/factories/${id}`, body) : await api.post<Factory>("/api/refs/factories", body);
      setMsg("Сохранено"); load(); open(r.id!);
    } catch (e) { setMsg((e as Error).message); }
  }
  const set = (p: Partial<Factory>) => cur && setCur({ ...cur, ...p });
  const setC = (i: number, p: Partial<Contact>) => cur && setCur({ ...cur, contacts: cur.contacts.map((c, j) => (j === i ? { ...c, ...p } : c)) });
  const shown = list.filter((f) => (!q || `${f.name} ${f.name_zh} ${f.short_name} ${f.city} ${f.province} ${(f.trademarks || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()))
    && (!roleFilter || (f.contacts || []).some((c) => (c.roles || []).includes(roleFilter))));
  const field = (label: string, key: keyof Factory, ph = "") => (
    <label className="f">{label}<input value={String(cur?.[key] ?? "")} placeholder={ph} disabled={!editor} onChange={(e) => set({ [key]: e.target.value } as Partial<Factory>)} /></label>);

  return (
    <>
      <div className="page-head">
        <h1>Заводы</h1>
        <div className="push">{editor && <button className="btn add" onClick={() => { setCur({ ...EMPTY, contacts: [{ ...EMPTY_CONTACT, roles: ["rfq"], primary: true }] }); setPrices([]); setMsg(""); }}>Добавить завод</button>}</div>
      </div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid" style={{ gridTemplateColumns: "360px 1fr" }}>
        <div className="card">
          <div className="row"><input placeholder="поиск: название, марка, город" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} title="есть контакт по направлению"><option value="">все</option>{Object.entries(roles).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></div>
          <table style={{ marginTop: 6 }}><tbody>{shown.map((f) => {
            const rfq = (f.contacts || []).find((c) => (c.roles || []).includes("rfq"));
            return (
              <tr key={f.id} style={{ cursor: "pointer", background: cur?.id === f.id ? "var(--accent-soft)" : undefined, opacity: f.is_active === false ? 0.5 : 1 }} onClick={() => open(f.id!)}>
                <td><b>{f.short_name || f.name}</b>{(f.trademarks || []).map((t) => <span key={t} className="badge accent" style={{ marginLeft: 4 }}>{t}</span>)}{f.name_zh && <div className="muted" style={{ fontSize: 11 }}>{f.name_zh}</div>}
                  <div className="muted" style={{ fontSize: 11 }}>{[f.city, f.province].filter(Boolean).join(", ")}{rfq ? ` · заявки: ${rfq.name}` : " · нет контакта по заявкам"}</div></td>
                <td className="muted" style={{ fontSize: 11 }}>{(f.contacts || []).length} конт.</td></tr>);
          })}{shown.length === 0 && <tr><td className="muted">Не найдено</td></tr>}</tbody></table>
        </div>
        <div>
          {!cur ? <div className="card muted">Выберите завод слева или добавьте новый. Запрос заводу из сделки уходит контактам с направлением «Заявки и КП» (WeCom и почта).</div> : (
            <>
              <div className="card">
                <h2 style={{ marginTop: 0 }}>{cur.id ? cur.name : "Новый завод"} {!cur.is_active && <span className="badge bad">не работаем</span>}</h2>
                <div className="grid cols-3">
                  {field("Наименование (как в документах, EN)", "name")}{field("Наименование (中文)", "name_zh")}{field("Краткое название", "short_name")}
                  <label className="f">Статус поставщика<select value={cur.supplier_status} disabled={!editor} onChange={(e) => set({ supplier_status: e.target.value })}>{Object.entries(STATUS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
                  {field("Единый код юрлица (统一社会信用代码)", "uscc", "18 знаков")}{field("Сайт", "website", "https://")}
                  {field("Провинция", "province")}{field("Город", "city")}{field("Страна", "country")}
                  {field("Адрес (рус./EN)", "address")}{field("Адрес (中文)", "address_zh")}
                  <label className="f">Торговые марки (через запятую; первая — по умолчанию)<input value={(cur.trademarks || []).join(", ")} placeholder="напр. SHILI" disabled={!editor}
                    onChange={(e) => set({ trademarks: e.target.value.split(",").map((x) => x.trimStart()) })} /></label>
                  <label className="f">Что выпускает (типы машин через запятую)<input value={cur.machine_types.join(", ")} disabled={!editor} onChange={(e) => set({ machine_types: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
                  <label className="f">Валюта / базис по умолчанию<div className="row"><select value={cur.default_currency} disabled={!editor} onChange={(e) => set({ default_currency: e.target.value })}>{["CNY", "USD", "EUR", "RUB"].map((c) => <option key={c}>{c}</option>)}</select>
                    <select value={cur.default_basis} disabled={!editor} onChange={(e) => set({ default_basis: e.target.value })}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select></div></label>
                  <label className="f">Типовой срок производства, дн<input type="number" value={cur.typical_production_days ?? ""} disabled={!editor} onChange={(e) => set({ typical_production_days: e.target.value ? Number(e.target.value) : null })} /></label>
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

              {(editor || contactEditor) && <div className="row" style={{ marginBottom: 12 }}><button className="btn" onClick={save} disabled={!cur.name}>Сохранить завод</button><UndoButton h={history} /><button className="btn ghost" onClick={() => setCur(null)}>закрыть</button>
                {isGD && cur.id && <button className="btn ghost" style={{ marginLeft: "auto", color: "var(--bad)" }} title="только ЗГД; если завод уже есть в сделках или каталоге — переносится в архив" onClick={remove}>{confirmDel ? "Подтвердить удаление" : "Удалить завод"}</button>}</div>}

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
        </div>
      </div>
    </>
  );
}
