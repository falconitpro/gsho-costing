import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Me } from "../types";
import { Link, useLocation, useNavigate } from "react-router-dom";
import PartsPurchase from "../components/PartsPurchase";
import PartsWarehouse from "../components/PartsWarehouse";
import PartsBatches from "../components/PartsBatches";
import PartsClaims from "../components/PartsClaims";
import PartsReports from "../components/PartsReports";

// Запчасти, шаг 1: компании группы, маршруты поставки, политика наценок между компаниями (визы ЗГД и ФД).

type Entity = { id?: string; code: string; name: string; country: string; currency: string; warehouse: string; sells_to_clients: boolean; is_active: boolean; notes: string;
  channels?: { mail_profile?: string; mail_from?: string; wecom_profile?: string } };
type Step = { seller_entity_id: string | null; buyer_entity_id: string; incoterms: string; place: string; currency: string; customs_country: string;
  pay_days: number | null; transit_days: number | null; comment: string; seller?: string; buyer?: string };
type Route = { id?: string; name: string; description: string; supplier_country: string; is_active: boolean; steps: Step[]; chain?: string };
type Rules = { default: Record<string, number>; by_group: { entity_id: string; part_group: string; pct: number }[];
  by_supplier: { entity_id: string; supplier_id: string; pct: number }[]; min_margin_pct: number | null;
  gsho_default?: Record<string, number>; gsho_by_supplier?: { entity_id: string; supplier_id: string; pct: number }[]; gsho_by_machine_type?: { entity_id: string; machine_type: string; pct: number }[] };
type Visa = { user_id: string; name: string; at: string; comment: string };
type Policy = { id: string; version_no: number; status: string; status_title: string; valid_from: string | null; reason: string; rules: Rules;
  approvals: Record<string, Visa>; rejection: Partial<Visa>; created_at: string; missing_approvals: string[] };
type Meta = { warehouse_kinds: Record<string, string>; incoterms: string[]; currencies: string[]; approvers: Record<string, string> };
type SimStep = { no: number; seller: string; buyer: string; currency: string; incoterms: string; price: number; price_rub: number; markup_pct: number | null; transfer_price?: boolean };
type Sim = { steps: SimStep[]; companies: { name: string; margin: number; margin_rub: number; margin_pct: number; currency: string }[]; warnings: string[];
  purchase_rub: number; landed_rub: number; intragroup_markup_rub: number; client_price_rub?: number; seller_margin_rub?: number; seller_margin_pct?: number;
  group_margin_rub?: number; group_margin_pct?: number; group_split?: { name: string; margin_rub: number; share_pct: number }[]; final_buyer: string; policy_version: number | null };
type Supplier = { id: string; name: string; short_name: string; country: string };

const EMPTY_ENTITY: Entity = { code: "", name: "", country: "", currency: "RUB", warehouse: "none", sells_to_clients: false, is_active: true, notes: "" };
const EMPTY_STEP = (buyer = ""): Step => ({ seller_entity_id: null, buyer_entity_id: buyer, incoterms: "FCA", place: "", currency: "CNY", customs_country: "", pay_days: null, transit_days: null, comment: "" });
const EMPTY_RULES: Rules = { default: {}, by_group: [], by_supplier: [], min_margin_pct: null, gsho_default: {}, gsho_by_supplier: [], gsho_by_machine_type: [] };
const num = (v: string) => (v === "" ? null : Number(v.replace(",", ".")));

export default function SupplyPage({ me }: { me: Me }) {
  // «Снабжение и склад» (решение владельца 08.11.2026): заказы поставщикам, партии, склад, гарантия, отчёты.
  // Заявки клиентов — в «Сделках», каталог и справочники — в «Каталоге», наценки между компаниями — в Настройках.
  const loc = useLocation();
  const nav = useNavigate();
  type T = "po" | "batches" | "stock" | "claims" | "reports";
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const poReader = has("manager", "purchasing", "cd", "gd", "fd", "logistician", "admin");
  const fleetReader = has("manager", "service_head", "cd", "gd", "admin", "fd", "logistician", "purchasing");
  const TABS: [T, string, boolean][] = [["po", "Заказы поставщикам", poReader], ["batches", "Партии", poReader], ["stock", "Склад", poReader], ["claims", "Гарантия", fleetReader], ["reports", "Отчёты", fleetReader]];
  const visible = TABS.filter((t) => t[2]);
  const asked = new URLSearchParams(loc.search).get("tab") as T;
  const tab: T | undefined = visible.some(([k]) => k === asked) ? asked : visible[0]?.[0];
  return (
    <>
      <div className="page-head"><h1>Снабжение и склад</h1>
        <div className="tabs" style={{ margin: 0 }}>{visible.map(([k, t]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => nav(`/supply?tab=${k}`, { replace: true })}>{t}</button>)}</div>
      </div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>Заявки клиентов на запчасти — в <Link to="/deals?type=parts">Сделках</Link>, каталог запчастей и справочники — в <Link to="/catalog?s=parts">Каталоге</Link>, парк машин — в карточке клиента.</div>
      {tab === "po" && <PartsPurchase me={me} />}
      {tab === "batches" && <PartsBatches />}
      {tab === "stock" && <PartsWarehouse />}
      {tab === "claims" && <PartsClaims me={me} />}
      {tab === "reports" && <PartsReports />}
      {!tab && <div className="card muted">Нет доступа к разделу.</div>}
    </>
  );
}

/** Настройки → «Ставки и нормативы»: наценки между компаниями группы (визы ЗГД и ФД). */
export function MarkupsBlock({ me }: { me: Me }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<Meta>("/api/parts/meta").then(setMeta).catch((e) => setMsg((e as Error).message)); api.get<Entity[]>("/api/parts/entities").then(setEntities); api.get<Route[]>("/api/parts/routes").then(setRoutes); }, []);
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {meta && <Markups me={me} meta={meta} entities={entities} routes={routes.filter((r) => r.is_active)} setMsg={setMsg} />}
    </>);
}

function Entities({ meta, list, editor, onSaved, setMsg }: { meta: Meta; list: Entity[]; editor: boolean; onSaved: () => void; setMsg: (s: string) => void }) {
  const [cur, setCur] = useState<Entity | null>(null);
  async function save() {
    if (!cur) return;
    try {
      const { id, ...body } = cur;
      if (id) await api.put(`/api/parts/entities/${id}`, body); else await api.post("/api/parts/entities", body);
      setMsg("Компания сохранена"); setCur(null); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  }
  const set = (p: Partial<Entity>) => cur && setCur({ ...cur, ...p });
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Компании группы</h2>
        {editor && <button className="btn add" onClick={() => setCur({ ...EMPTY_ENTITY })}>Компания</button>}
      </div>
      <table style={{ marginTop: 8 }}>
        <thead><tr><th>Код</th><th>Название</th><th>Страна</th><th>Валюта учёта</th><th>Склад</th><th>Продаёт клиентам</th><th></th></tr></thead>
        <tbody>{list.map((e) => (
          <tr key={e.id} style={{ opacity: e.is_active ? 1 : 0.5 }}>
            <td>{e.code}</td><td><b>{e.name}</b>{e.notes && <div className="muted" style={{ fontSize: 11 }}>{e.notes}</div>}</td><td>{e.country}</td><td>{e.currency}</td>
            <td>{meta.warehouse_kinds[e.warehouse] || e.warehouse}</td><td>{e.sells_to_clients ? "да" : ""}</td>
            <td>{editor && <button className="btn ghost" onClick={() => setCur({ ...e })}>изменить</button>}</td></tr>))}</tbody>
      </table>
      {cur && (
        <div style={{ borderTop: "1px solid var(--line)", marginTop: 10, paddingTop: 10 }}>
          <div className="grid cols-3">
            <label className="f">Код (латиница)<input value={cur.code} onChange={(e) => set({ code: e.target.value })} placeholder="ru, kg, cn" /></label>
            <label className="f">Название<input value={cur.name} onChange={(e) => set({ name: e.target.value })} /></label>
            <label className="f">Страна<input value={cur.country} onChange={(e) => set({ country: e.target.value })} placeholder="RU, KG, CN" /></label>
            <label className="f">Валюта учёта<select value={cur.currency} onChange={(e) => set({ currency: e.target.value })}>{meta.currencies.map((c) => <option key={c}>{c}</option>)}</select></label>
            <label className="f">Склад<select value={cur.warehouse} onChange={(e) => set({ warehouse: e.target.value })}>{Object.entries(meta.warehouse_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
            <label className="f">Продаёт клиентам<div className="row"><input type="checkbox" checked={cur.sells_to_clients} onChange={(e) => set({ sells_to_clients: e.target.checked })} /> {cur.sells_to_clients ? "да" : "нет"}</div></label>
            <label className="f">Действует<div className="row"><input type="checkbox" checked={cur.is_active} onChange={(e) => set({ is_active: e.target.checked })} /> {cur.is_active ? "да" : "нет"}</div></label>
          </div>
          <label className="f" style={{ marginTop: 6 }}>Заметки<textarea rows={2} value={cur.notes} onChange={(e) => set({ notes: e.target.value })} /></label>
          <div className="grid cols-3" style={{ marginTop: 6 }}>
            <label className="f">Почта для запросов от имени компании<input value={cur.channels?.mail_from || ""} placeholder="rfq@company.com" onChange={(e) => set({ channels: { ...cur.channels, mail_from: e.target.value } })} /></label>
            <label className="f">Профиль почты на сервере<input value={cur.channels?.mail_profile || ""} placeholder="напр. alliance" onChange={(e) => set({ channels: { ...cur.channels, mail_profile: e.target.value } })} /></label>
            <label className="f">Профиль WeCom на сервере<input value={cur.channels?.wecom_profile || ""} placeholder="напр. alliance" onChange={(e) => set({ channels: { ...cur.channels, wecom_profile: e.target.value } })} /></label>
          </div>
          <div className="muted" style={{ fontSize: 12 }}>Зеркальные запросы ГШО уходят поставщикам с почты и WeCom этой компании. Пароли и ключи задаются администратором на сервере
            (MAIL_PROFILE_&lt;профиль&gt;_SMTP_USER / _SMTP_PASSWORD, WECOM_PROFILE_&lt;профиль&gt;_CORP_ID / _SECRET) и в программе не хранятся.</div>
          <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save} disabled={!cur.code || !cur.name}>Сохранить</button><button className="btn ghost" onClick={() => setCur(null)}>отмена</button></div>
        </div>)}
    </div>
  );
}

function RoutesTab({ meta, list, entities, editor, name, onSaved, setMsg }: { meta: Meta; list: Route[]; entities: Entity[]; editor: boolean;
  name: (id: string | null | undefined) => string; onSaved: () => void; setMsg: (s: string) => void }) {
  const [cur, setCur] = useState<Route | null>(null);
  async function save() {
    if (!cur) return;
    try {
      const { id, chain, ...rest } = cur;
      void chain;
      const body = { ...rest, steps: rest.steps.map(({ seller, buyer, ...s }) => { void seller; void buyer; return s; }) };
      if (id) await api.put(`/api/parts/routes/${id}`, body); else await api.post("/api/parts/routes", body);
      setMsg("Маршрут сохранён"); setCur(null); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  }
  const setS = (i: number, p: Partial<Step>) => cur && setCur({ ...cur, steps: cur.steps.map((s, j) => (j === i ? { ...s, ...p } : s)) });
  function addStep() {
    if (!cur) return;
    const prev = cur.steps[cur.steps.length - 1];
    const ru = entities.find((e) => e.sells_to_clients)?.id || "";
    setCur({ ...cur, steps: [...cur.steps, { ...EMPTY_STEP(ru), seller_entity_id: prev ? prev.buyer_entity_id : null, currency: prev?.currency || "CNY", incoterms: prev ? "DAP" : "FCA" }] });
  }
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Маршруты поставки</h2>
        {editor && <button className="btn add" onClick={() => setCur({ name: "", description: "", supplier_country: "", is_active: true, steps: [EMPTY_STEP()] })}>Маршрут</button>}
      </div>
      <p className="muted" style={{ marginTop: 4 }}>Каждая ступень — продажа: от поставщика к первой компании группы, затем от компании к компании до той, что продаёт клиенту. Принят вариант В: Китай — FCA склад экспедитора в CNY, Киргизия — DAP склад РФ в валюте закупки.</p>
      <table style={{ marginTop: 8 }}>
        <thead><tr><th>Маршрут</th><th>Ступени: базис, валюта, выпуск</th><th></th></tr></thead>
        <tbody>{list.map((r) => (
          <tr key={r.id} style={{ opacity: r.is_active ? 1 : 0.5 }}>
            <td><b>{r.name}</b>{!r.is_active && <span className="badge" style={{ marginLeft: 4 }}>архив</span>}<div className="muted" style={{ fontSize: 11 }}>{r.chain}</div></td>
            <td style={{ fontSize: 12 }}>{r.steps.map((s, i) => <div key={i}>{i + 1}. {s.seller} → {s.buyer}: {s.incoterms} {s.place && `(${s.place})`}, {s.currency}{s.customs_country && `, выпуск в ${s.customs_country}`}</div>)}</td>
            <td>{editor && <button className="btn ghost" onClick={() => setCur(JSON.parse(JSON.stringify(r)))}>изменить</button>}</td></tr>))}</tbody>
      </table>
      {cur && (
        <div style={{ borderTop: "1px solid var(--line)", marginTop: 10, paddingTop: 10 }}>
          <div className="grid cols-3">
            <label className="f">Название<input value={cur.name} onChange={(e) => setCur({ ...cur, name: e.target.value })} /></label>
            <label className="f">Страна поставщика<input value={cur.supplier_country} onChange={(e) => setCur({ ...cur, supplier_country: e.target.value })} placeholder="CN, DE, KG" /></label>
            <label className="f">Действует<div className="row"><input type="checkbox" checked={cur.is_active} onChange={(e) => setCur({ ...cur, is_active: e.target.checked })} /> {cur.is_active ? "да" : "в архиве"}</div></label>
          </div>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>№</th><th>Продавец</th><th>Покупатель</th><th>Incoterms</th><th>Место</th><th>Валюта</th><th>Выпуск</th><th className="num">Отсрочка, дн</th><th className="num">Перевозка, дн</th><th></th></tr></thead>
            <tbody>{cur.steps.map((s, i) => (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>{i === 0 ? "Поставщик" : name(s.seller_entity_id)}</td>
                <td><select value={s.buyer_entity_id} onChange={(e) => {
                  const steps = cur.steps.map((x, j) => (j === i ? { ...x, buyer_entity_id: e.target.value } : j === i + 1 ? { ...x, seller_entity_id: e.target.value } : x));
                  setCur({ ...cur, steps });
                }}><option value="">—</option>{entities.filter((e) => e.is_active).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></td>
                <td><select value={s.incoterms} onChange={(e) => setS(i, { incoterms: e.target.value })}><option value="">—</option>{meta.incoterms.map((x) => <option key={x}>{x}</option>)}</select></td>
                <td><input value={s.place} onChange={(e) => setS(i, { place: e.target.value })} placeholder="склад экспедитора в КНР" /></td>
                <td><select value={s.currency} onChange={(e) => setS(i, { currency: e.target.value })}>{meta.currencies.map((c) => <option key={c}>{c}</option>)}</select></td>
                <td><select value={s.customs_country} onChange={(e) => setS(i, { customs_country: e.target.value })}><option value="">—</option><option value="RU">РФ</option><option value="KG">Киргизия</option></select></td>
                <td className="num"><input type="number" style={{ width: 70 }} value={s.pay_days ?? ""} onChange={(e) => setS(i, { pay_days: num(e.target.value) })} /></td>
                <td className="num"><input type="number" style={{ width: 70 }} value={s.transit_days ?? ""} onChange={(e) => setS(i, { transit_days: num(e.target.value) })} /></td>
                <td>{i === cur.steps.length - 1 && cur.steps.length > 1 && <button className="btn ghost" onClick={() => setCur({ ...cur, steps: cur.steps.slice(0, -1) })}>удалить</button>}</td>
              </tr>))}</tbody>
          </table>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn add" onClick={addStep}>Ступень</button>
            <button className="btn" onClick={save} disabled={!cur.name || cur.steps.some((s) => !s.buyer_entity_id)}>Сохранить маршрут</button>
            <button className="btn ghost" onClick={() => setCur(null)}>отмена</button>
          </div>
        </div>)}
    </div>
  );
}

function Markups({ me, meta, entities, routes, setMsg }: { me: Me; meta: Meta; entities: Entity[]; routes: Route[]; setMsg: (s: string) => void }) {
  const [items, setItems] = useState<Policy[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ id?: string; rules: Rules; valid_from: string; reason: string } | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [comment, setComment] = useState("");
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const author = has("cd", "fd", "gd");
  const approver = has("gd", "fd");
  const sellers = entities.filter((e) => !e.sells_to_clients && e.is_active);   // наценку ставит компания, которая перепродаёт внутри группы

  const load = () => api.get<{ current_id: string | null; items: Policy[] }>("/api/parts/markup-policies").then((r) => { setItems(r.items); setCurrentId(r.current_id); });
  useEffect(() => { load(); api.get<Supplier[]>("/api/refs/factories").then(setSuppliers).catch(() => setSuppliers([])); }, []);
  const current = items.find((p) => p.id === currentId) || null;

  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg(ok); setComment(""); load(); } catch (e) { setMsg((e as Error).message); }
  }
  async function saveDraft() {
    if (!draft) return;
    const body = { rules: draft.rules, valid_from: draft.valid_from || null, reason: draft.reason };
    await act(() => (draft.id ? api.put(`/api/parts/markup-policies/${draft.id}`, body) : api.post("/api/parts/markup-policies", body)), "Черновик сохранён");
    setDraft(null);
  }
  const setRules = (p: Partial<Rules>) => draft && setDraft({ ...draft, rules: { ...draft.rules, ...p } });
  const pctOf = (rules: Rules, id: string) => rules.default?.[id];

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Действующая политика</h2>
          {author && !draft && <button className="btn add" onClick={() => setDraft({ rules: JSON.parse(JSON.stringify(current?.rules || EMPTY_RULES)), valid_from: "", reason: "" })}>Новая версия</button>}
        </div>
        {!current ? <p className="muted">Пока нет утверждённой версии: наценки между компаниями считаются как 0 %.</p> : (
          <>
            <p className="muted" style={{ marginTop: 4 }}>Версия {current.version_no}, действует с {fmtDate(current.valid_from)}. Визы: {Object.entries(current.approvals).map(([k, v]) => `${meta.approvers[k]} — ${v.name}`).join(", ")}.</p>
            <RulesView rules={current.rules} sellers={sellers} suppliers={suppliers} />
          </>)}
      </div>

      {draft && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>{draft.id ? "Правка черновика" : "Новая версия политики"}</h2>
          <p className="muted">Наценка — к себестоимости ступени для компании-продавца. Приоритет: поставщик → группа товара → ставка по умолчанию. Новая версия вступает в силу после виз ЗГД и ФД; любая правка снимает уже поставленные визы.</p>
          <div className="grid cols-3">
            {sellers.map((e) => (
              <label key={e.id} className="f">{e.name}, наценка по умолчанию, %<input type="number" step="0.1" value={pctOf(draft.rules, e.id!) ?? ""}
                onChange={(ev) => { const v = num(ev.target.value); const d = { ...draft.rules.default }; if (v == null) delete d[e.id!]; else d[e.id!] = v; setRules({ default: d }); }} /></label>))}
            <label className="f">Минимум маржи компании на ступени, %<input type="number" step="0.1" value={draft.rules.min_margin_pct ?? ""} onChange={(e) => setRules({ min_margin_pct: num(e.target.value) })} /></label>
            <label className="f">Действует с<input type="date" value={draft.valid_from} onChange={(e) => setDraft({ ...draft, valid_from: e.target.value })} /></label>
            <label className="f">Причина изменения<input value={draft.reason} onChange={(e) => setDraft({ ...draft, reason: e.target.value })} /></label>
          </div>
          <h3>Исключения по группе товара</h3>
          {draft.rules.by_group.map((r, i) => (
            <div key={i} className="row" style={{ marginBottom: 4 }}>
              <select value={r.entity_id} onChange={(e) => setRules({ by_group: draft.rules.by_group.map((x, j) => (j === i ? { ...x, entity_id: e.target.value } : x)) })}>{sellers.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
              <input placeholder="группа товара" value={r.part_group} onChange={(e) => setRules({ by_group: draft.rules.by_group.map((x, j) => (j === i ? { ...x, part_group: e.target.value } : x)) })} />
              <input type="number" step="0.1" style={{ width: 80 }} value={r.pct} onChange={(e) => setRules({ by_group: draft.rules.by_group.map((x, j) => (j === i ? { ...x, pct: Number(e.target.value) } : x)) })} /> %
              <button className="btn ghost" onClick={() => setRules({ by_group: draft.rules.by_group.filter((_, j) => j !== i) })}>удалить</button>
            </div>))}
          {sellers.length > 0 && <button className="btn add" onClick={() => setRules({ by_group: [...draft.rules.by_group, { entity_id: sellers[0].id!, part_group: "", pct: 0 }] })}>Исключение по группе</button>}
          <h3>Исключения по поставщику</h3>
          {draft.rules.by_supplier.map((r, i) => (
            <div key={i} className="row" style={{ marginBottom: 4 }}>
              <select value={r.entity_id} onChange={(e) => setRules({ by_supplier: draft.rules.by_supplier.map((x, j) => (j === i ? { ...x, entity_id: e.target.value } : x)) })}>{sellers.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
              <select value={r.supplier_id} onChange={(e) => setRules({ by_supplier: draft.rules.by_supplier.map((x, j) => (j === i ? { ...x, supplier_id: e.target.value } : x)) })}><option value="">— поставщик —</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select>
              <input type="number" step="0.1" style={{ width: 80 }} value={r.pct} onChange={(e) => setRules({ by_supplier: draft.rules.by_supplier.map((x, j) => (j === i ? { ...x, pct: Number(e.target.value) } : x)) })} /> %
              <button className="btn ghost" onClick={() => setRules({ by_supplier: draft.rules.by_supplier.filter((_, j) => j !== i) })}>удалить</button>
            </div>))}
          {sellers.length > 0 && <button className="btn add" onClick={() => setRules({ by_supplier: [...draft.rules.by_supplier, { entity_id: sellers[0].id!, supplier_id: "", pct: 0 }] })}>Исключение по поставщику</button>}
          <GshoRules rules={draft.rules} sellers={sellers} suppliers={suppliers} setRules={setRules} />
          <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={saveDraft}>Сохранить черновик</button><button className="btn ghost" onClick={() => setDraft(null)}>отмена</button></div>
          <Simulator routes={routes} suppliers={suppliers} rules={draft.rules} title="Проверка черновика на примере" />
        </div>)}

      {!draft && <div className="card"><Simulator routes={routes} suppliers={suppliers} title="Симулятор по действующей версии" /></div>}

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Версии</h2>
        <table>
          <thead><tr><th>Версия</th><th>Статус</th><th>Действует с</th><th>Визы</th><th>Причина</th><th></th></tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.id}>
              <td>{p.version_no}{p.id === currentId && <span className="badge accent" style={{ marginLeft: 4 }}>действует</span>}</td>
              <td>{p.status_title}{p.status === "rejected" && p.rejection?.comment && <div className="muted" style={{ fontSize: 11 }}>{p.rejection.name}: {p.rejection.comment}</div>}</td>
              <td>{fmtDate(p.valid_from)}</td>
              <td style={{ fontSize: 12 }}>{Object.entries(p.approvals || {}).map(([k, v]) => <div key={k}>{meta.approvers[k]}: {v.name}, {fmtDate(v.at)}</div>)}
                {p.status === "on_approval" && p.missing_approvals.length > 0 && <div className="muted">ждём: {p.missing_approvals.join(", ")}</div>}</td>
              <td>{p.reason}</td>
              <td>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  {author && ["draft", "rejected", "on_approval"].includes(p.status) && <button className="btn ghost" onClick={() => setDraft({ id: p.id, rules: JSON.parse(JSON.stringify({ ...EMPTY_RULES, ...p.rules })), valid_from: p.valid_from || "", reason: p.reason })}>изменить</button>}
                  {author && ["draft", "rejected"].includes(p.status) && <button className="btn" onClick={() => act(() => api.post(`/api/parts/markup-policies/${p.id}/submit`), "Отправлено на утверждение ЗГД и ФД")}>на утверждение</button>}
                  {approver && p.status === "on_approval" && !Object.values(p.approvals || {}).some((v) => v.user_id === me.id) && (
                    <>
                      <input placeholder="комментарий" value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: 140 }} />
                      <button className="btn" onClick={() => act(() => api.post(`/api/parts/markup-policies/${p.id}/approve`, { comment }), "Виза поставлена")}>утвердить</button>
                      <button className="btn ghost" onClick={() => act(() => api.post(`/api/parts/markup-policies/${p.id}/reject`, { comment }), "Версия возвращена автору")}>отклонить</button>
                    </>)}
                </div>
              </td>
            </tr>))}{items.length === 0 && <tr><td colSpan={6} className="muted">Версий пока нет</td></tr>}</tbody>
        </table>
      </div>
    </>
  );
}

function RulesView({ rules, sellers, suppliers }: { rules: Rules; sellers: Entity[]; suppliers: Supplier[] }) {
  const ename = (id: string) => sellers.find((e) => e.id === id)?.name || "?";
  const sname = (id: string) => { const s = suppliers.find((x) => x.id === id); return s ? s.short_name || s.name : "?"; };
  return (
    <table>
      <thead><tr><th>Компания</th><th>Правило</th><th className="num">Наценка</th></tr></thead>
      <tbody>
        {Object.entries(rules.default || {}).map(([id, pct]) => <tr key={id}><td>{ename(id)}</td><td>по умолчанию</td><td className="num">{pct} %</td></tr>)}
        {(rules.by_group || []).map((r, i) => <tr key={`g${i}`}><td>{ename(r.entity_id)}</td><td>группа «{r.part_group}»</td><td className="num">{r.pct} %</td></tr>)}
        {(rules.by_supplier || []).map((r, i) => <tr key={`s${i}`}><td>{ename(r.entity_id)}</td><td>поставщик {sname(r.supplier_id)}</td><td className="num">{r.pct} %</td></tr>)}
        {rules.min_margin_pct != null && <tr><td colSpan={2} className="muted">Минимум маржи компании на ступени</td><td className="num">{rules.min_margin_pct} %</td></tr>}
        {Object.entries(rules.gsho_default || {}).map(([id, pct]) => <tr key={`gd${id}`}><td>{ename(id)}</td><td>ГШО, по умолчанию</td><td className="num">{pct} %</td></tr>)}
        {(rules.gsho_by_machine_type || []).map((r, i) => <tr key={`gm${i}`}><td>{ename(r.entity_id)}</td><td>ГШО, тип «{r.machine_type}»</td><td className="num">{r.pct} %</td></tr>)}
        {(rules.gsho_by_supplier || []).map((r, i) => <tr key={`gs${i}`}><td>{ename(r.entity_id)}</td><td>ГШО, поставщик {sname(r.supplier_id)}</td><td className="num">{r.pct} %</td></tr>)}
      </tbody>
    </table>
  );
}

function Simulator({ routes, suppliers, rules, title }: { routes: Route[]; suppliers: Supplier[]; rules?: Rules; title: string }) {
  const [f, setF] = useState({ route_id: "", purchase_price: "1000", part_group: "", supplier_id: "", client_price_rub: "", extra_costs_rub: "", kgs: "" });
  const [res, setRes] = useState<Sim | null>(null);
  const [err, setErr] = useState("");
  const route = routes.find((r) => r.id === f.route_id);
  async function run() {
    try {
      const body = { route_id: f.route_id, purchase_price: Number(f.purchase_price), part_group: f.part_group, supplier_id: f.supplier_id || null,
        client_price_rub: num(f.client_price_rub), extra_costs_rub: num(f.extra_costs_rub) || 0, fx: f.kgs ? { KGS: Number(f.kgs.replace(",", ".")) } : {},
        ...(rules ? { rules } : {}) };
      setRes(await api.post<Sim>("/api/parts/markup-policies/simulate", body)); setErr("");
    } catch (e) { setRes(null); setErr((e as Error).message); }
  }
  return (
    <div style={{ marginTop: 12 }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <div className="grid cols-3">
        <label className="f">Маршрут<select value={f.route_id} onChange={(e) => setF({ ...f, route_id: e.target.value })}><option value="">— выберите —</option>{routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
        <label className="f">Цена поставщика{route ? `, ${route.steps[0]?.currency}` : ""}<input type="number" value={f.purchase_price} onChange={(e) => setF({ ...f, purchase_price: e.target.value })} /></label>
        <label className="f">Группа товара<input value={f.part_group} onChange={(e) => setF({ ...f, part_group: e.target.value })} placeholder="необязательно" /></label>
        <label className="f">Поставщик<select value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select></label>
        <label className="f">Цена клиенту, ₽ (для маржи группы)<input type="number" value={f.client_price_rub} onChange={(e) => setF({ ...f, client_price_rub: e.target.value })} /></label>
        <label className="f">Прочие расходы до клиента, ₽<input type="number" value={f.extra_costs_rub} onChange={(e) => setF({ ...f, extra_costs_rub: e.target.value })} placeholder="логистика, пошлина" /></label>
        {route?.steps.some((s) => s.currency === "KGS") && <label className="f">Курс KGS, ₽ за 1 сом<input value={f.kgs} placeholder="по умолчанию — курс ЦБ РФ" onChange={(e) => setF({ ...f, kgs: e.target.value })} /></label>}
      </div>
      <button className="btn" style={{ marginTop: 8 }} onClick={run} disabled={!f.route_id || !Number(f.purchase_price)}>Посчитать</button>
      {err && <div className="alert info" style={{ marginTop: 8 }}>{err}</div>}
      {res && (
        <>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>№</th><th>Продавец → покупатель</th><th>Базис</th><th className="num">Наценка</th><th className="num">Цена</th><th className="num">В рублях</th></tr></thead>
            <tbody>{res.steps.map((s) => (
              <tr key={s.no}><td>{s.no}</td><td>{s.seller} → {s.buyer}</td><td>{s.incoterms}</td>
                <td className="num">{s.transfer_price ? "трансфертная цена" : s.markup_pct == null ? "—" : `${s.markup_pct} %`}</td>
                <td className="num">{fmtRub(s.price, 2)} {s.currency}</td><td className="num">{fmtRub(s.price_rub)}</td></tr>))}</tbody>
          </table>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>Маржа компании</th><th className="num">Сумма</th><th className="num">В рублях</th><th className="num">% к продаже</th></tr></thead>
            <tbody>
              {res.companies.map((c, i) => <tr key={i}><td>{c.name}</td><td className="num">{fmtRub(c.margin, 2)} {c.currency}</td><td className="num">{fmtRub(c.margin_rub)}</td><td className="num">{c.margin_pct} %</td></tr>)}
              {res.seller_margin_rub != null && <tr><td>{res.final_buyer} (продажа клиенту)</td><td></td><td className="num">{fmtRub(res.seller_margin_rub)}</td><td className="num">{res.seller_margin_pct} %</td></tr>}
              {res.group_margin_rub != null && <tr><td><b>Группа в целом</b></td><td></td><td className="num"><b>{fmtRub(res.group_margin_rub)}</b></td><td className="num"><b>{res.group_margin_pct} %</b></td></tr>}
            </tbody>
          </table>
          <p className="muted" style={{ marginTop: 4 }}>Закупка {fmtRub(res.purchase_rub)} ₽, стоимость для {res.final_buyer} {fmtRub(res.landed_rub)} ₽, наценки внутри группы {fmtRub(res.intragroup_markup_rub)} ₽.
            {res.policy_version != null && ` Версия политики ${res.policy_version}.`}</p>
          {res.warnings.map((w, i) => <div key={i} className="alert info">{w}</div>)}
        </>)}
    </div>
  );
}

/** Каталог → «Компании группы» и «Маршруты поставки» (перенесены из раздела запчастей). */
export function GroupSetupBlock({ me, part }: { me: Me; part: "entities" | "routes" }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [msg, setMsg] = useState("");
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const loadEntities = () => api.get<Entity[]>("/api/parts/entities").then(setEntities);
  const loadRoutes = () => api.get<Route[]>("/api/parts/routes?include_archived=true").then(setRoutes);
  useEffect(() => { api.get<Meta>("/api/parts/meta").then(setMeta).catch((e) => setMsg((e as Error).message)); loadEntities(); loadRoutes(); }, []);
  const name = (id: string | null | undefined) => (id ? entities.find((e) => e.id === id)?.name || "?" : "Поставщик");
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {meta && part === "entities" && <Entities meta={meta} list={entities} editor={has("cd", "gd", "admin")} onSaved={() => { loadEntities(); loadRoutes(); }} setMsg={setMsg} />}
      {meta && part === "routes" && <RoutesTab meta={meta} list={routes} entities={entities} editor={has("cd", "gd", "admin", "logistician")} name={name} onSaved={loadRoutes} setMsg={setMsg} />}
    </>);
}

/** Наценки компаний группы на ГШО (зеркальные запросы, D-153): по умолчанию, исключения по типу машины и поставщику. Без ставки — цена не считается. */
function GshoRules({ rules, sellers, suppliers, setRules }: { rules: Rules; sellers: Entity[]; suppliers: Supplier[]; setRules: (p: Partial<Rules>) => void }) {
  const d = rules.gsho_default || {}, mt = rules.gsho_by_machine_type || [], bs = rules.gsho_by_supplier || [];
  const n = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));
  return (
    <>
      <h3>ГШО: наценка компании группы на технику</h3>
      <p className="muted">Применяется к предложению компании группы Берг-Хаусу по ответу конечного поставщика. Приоритет: поставщик → тип машины → ставка ГШО по умолчанию. Без ставки предложение не рассчитывается — ставки запчастей на технику не переносятся.</p>
      <div className="grid cols-3">
        {sellers.map((e) => (
          <label key={e.id} className="f">{e.name}, ГШО по умолчанию, %<input type="number" step="0.1" value={d[e.id!] ?? ""}
            onChange={(ev) => { const v = n(ev.target.value); const x = { ...d }; if (v == null) delete x[e.id!]; else x[e.id!] = v; setRules({ gsho_default: x }); }} /></label>))}
      </div>
      <h4>Исключения по типу машины</h4>
      {mt.map((r, i) => (
        <div key={i} className="row" style={{ marginBottom: 4 }}>
          <select value={r.entity_id} onChange={(e) => setRules({ gsho_by_machine_type: mt.map((x, j) => (j === i ? { ...x, entity_id: e.target.value } : x)) })}>{sellers.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
          <input placeholder="тип машины, напр. Самосвал" value={r.machine_type} onChange={(e) => setRules({ gsho_by_machine_type: mt.map((x, j) => (j === i ? { ...x, machine_type: e.target.value } : x)) })} />
          <input type="number" step="0.1" style={{ width: 80 }} value={r.pct} onChange={(e) => setRules({ gsho_by_machine_type: mt.map((x, j) => (j === i ? { ...x, pct: Number(e.target.value) } : x)) })} /> %
          <button className="btn ghost" onClick={() => setRules({ gsho_by_machine_type: mt.filter((_, j) => j !== i) })}>удалить</button>
        </div>))}
      {sellers.length > 0 && <button className="btn add" onClick={() => setRules({ gsho_by_machine_type: [...mt, { entity_id: sellers[0].id!, machine_type: "", pct: 0 }] })}>Исключение по типу машины</button>}
      <h4>Исключения по поставщику ГШО</h4>
      {bs.map((r, i) => (
        <div key={i} className="row" style={{ marginBottom: 4 }}>
          <select value={r.entity_id} onChange={(e) => setRules({ gsho_by_supplier: bs.map((x, j) => (j === i ? { ...x, entity_id: e.target.value } : x)) })}>{sellers.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
          <select value={r.supplier_id} onChange={(e) => setRules({ gsho_by_supplier: bs.map((x, j) => (j === i ? { ...x, supplier_id: e.target.value } : x)) })}><option value="">— поставщик —</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select>
          <input type="number" step="0.1" style={{ width: 80 }} value={r.pct} onChange={(e) => setRules({ gsho_by_supplier: bs.map((x, j) => (j === i ? { ...x, pct: Number(e.target.value) } : x)) })} /> %
          <button className="btn ghost" onClick={() => setRules({ gsho_by_supplier: bs.filter((_, j) => j !== i) })}>удалить</button>
        </div>))}
      {sellers.length > 0 && <button className="btn add" onClick={() => setRules({ gsho_by_supplier: [...bs, { entity_id: sellers[0].id!, supplier_id: "", pct: 0 }] })}>Исключение по поставщику ГШО</button>}
    </>
  );
}
