import { Fragment, useEffect, useState } from "react";
import { lbl, OUTCOME } from "../labels";
import { UndoButton, useUndoHistory } from "../undo";
import { api, ApiError, fmtDate, fmtPct, fmtRub } from "../api";
import { STATUS_TITLES } from "../types";
import { toneOf } from "../colors";
import ClientRequisites from "../components/ClientRequisites";
import type { Me } from "../types";
import { payText } from "../components/PaymentTermsEditor";
import type { PaymentLine } from "../types";

type Stats = { deals: number; open: number; won: number; lost: number; last_on: string | null; last_number: string; last_status: string; manager: string; sum_won_rub: number | null };
type Client = { id: string; name: string; inn: string; address: string; sites: { name: string }[]; contacts: { name: string; email?: string }[];
  full_name?: string; kpp?: string; ogrn?: string; director?: string; legal_status?: string; okved?: string; registry_checked_at?: string | null; stats?: Stats; requisites_count?: number };
type SortKey = "name" | "deals" | "open" | "won" | "sum" | "last";
type Found = { inn: string; name: string; full_name: string; kpp: string; ogrn: string; address: string; director: string; status: string; okved: string;
  registered_on: string | null; kind: string; source: string; raw: Record<string, unknown> };
const STATUS_RU: Record<string, string> = { ACTIVE: "действующая", LIQUIDATING: "ликвидируется", LIQUIDATED: "ликвидирована", BANKRUPT: "банкротство", REORGANIZING: "реорганизация" };
type KpVer = { version_no: number; on: string; unit_price_rub: number; total_rub: number; payment_terms: PaymentLine[]; delivery_term: string; kp_brand: string; kp_path: string; kp_no: string };
type KpHist = { deal_id: string; deal_number: string; variant: string; factory: string; model: string; qty: number; outcome: string | null; chosen: boolean | null; manager: string; latest: KpVer; versions: KpVer[] };
type Card = Client & { funnel: { requests: number; kp_sent: number; won: number }; deals: { id: string; number: string; status: string; outcome: string | null }[];
  last_kp?: { deal: string; variant: string; price_rub: number; status: string }[]; kp_history?: KpHist[]; kp_totals?: { kp_count: number; won: number; sum_rub: number };
  avg_margin_pct?: number | null; sum_won_rub?: number };

/** Что изменилось в версии КП относительно предыдущей: цена, сумма, срок, условия оплаты, бренд подачи. */
function verDiff(newer: KpVer, older?: KpVer): string[] {
  if (!older) return [];
  const out: string[] = [];
  const pct = (a: number, b: number) => (b ? `${a >= b ? "+" : ""}${(((a - b) / b) * 100).toFixed(1).replace(".", ",")} %` : "");
  if (newer.unit_price_rub !== older.unit_price_rub) out.push(`цена ${fmtRub(older.unit_price_rub)} → ${fmtRub(newer.unit_price_rub)} (${pct(newer.unit_price_rub, older.unit_price_rub)})`);
  else if (newer.total_rub !== older.total_rub) out.push(`сумма ${fmtRub(older.total_rub)} → ${fmtRub(newer.total_rub)}`);
  if ((newer.delivery_term || "") !== (older.delivery_term || "")) out.push(`срок: «${older.delivery_term || "—"}» → «${newer.delivery_term || "—"}»`);
  const pay = (v: KpVer) => (v.payment_terms || []).map(payText).join("; ");
  if (pay(newer) !== pay(older)) out.push("изменены условия оплаты");
  if (newer.kp_brand !== older.kp_brand) out.push(newer.kp_brand === "company" ? "подача под брендом компании" : "подача под маркой завода");
  return out;
}

export default function ClientsPage({ me, embedded = false }: { me: Me; embedded?: boolean }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [card, setCard] = useState<Card | null>(null);
  const EMPTY_FORM = { name: "", inn: "", address: "", full_name: "", kpp: "", ogrn: "", director: "", legal_status: "", okved: "", registry: {} as Record<string, unknown> };
  const [form, setForm] = useState(EMPTY_FORM);
  const history = useUndoHistory({ form }, (st) => setForm(st.form));
  const [found, setFound] = useState<Found[] | null>(null);       // филиалы — показываются по ссылке
  const [showBranches, setShowBranches] = useState(false);
  const [picked, setPicked] = useState<"head" | "branch" | null>(null);
  const [lookupMsg, setLookupMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState({ model: "", outcome: "", factory: "", from: "", to: "" });
  const canCreate = me.roles.some((r) => ["manager", "cd", "gd", "admin"].includes(r.code));
  const canReq = me.roles.some((r) => ["manager", "cd", "gd", "admin", "lawyer"].includes(r.code));
  const [q, setQ] = useState("");
  const [view, setView] = useState<"all" | "open" | "won" | "none">("all");
  const [mgr, setMgr] = useState("");
  const [sort, setSort] = useState<{ k: SortKey; d: 1 | -1 }>({ k: "last", d: -1 });
  const [showNew, setShowNew] = useState(false);
  const load = () => api.get<Client[]>("/api/refs/clients?stats=1").then(setClients);
  useEffect(() => { load(); }, []);
  async function create() {
    try { const c = await api.post<Client>("/api/refs/clients", form); setForm(EMPTY_FORM); setFound(null); setLookupMsg(""); setErr(""); load(); api.get<Card>(`/api/refs/clients/${c.id}`).then(setCard); }
    catch (e) { setErr((e as Error).message); }
  }
  // поиск реквизитов по ИНН во внешнем источнике (ЕГРЮЛ/ЕГРИП)
  async function lookup() {
    setBusy(true); setLookupMsg(""); setFound(null);
    try {
      const r = await api.get<{ inn: string; existing_client_id: string | null; results: Found[] }>(`/api/refs/clients/lookup?inn=${form.inn.trim()}`);
      if (r.existing_client_id) { setLookupMsg("Клиент с этим ИНН уже есть в базе — открыта его карточка"); api.get<Card>(`/api/refs/clients/${r.existing_client_id}`).then(setCard); }
      else if (!r.results.length) setLookupMsg("Организация с таким ИНН в реестре не найдена — заполните реквизиты вручную");
      else {
        // головная организация подставляется сразу, филиалы — по ссылке
        const isBranch = (f: Found) => (f.raw?.branch_type as string) === "BRANCH";
        const head = r.results.find((f) => !isBranch(f)) || r.results[0];
        const branches = r.results.filter((f) => f !== head && isBranch(f));
        pick(head, "head", r.inn);
        setFound(branches.length ? branches : null); setShowBranches(false);
      }
    } catch (e) {
      const st = (e as ApiError).status;
      setLookupMsg(st === 503 ? "Источник реквизитов не подключён (нужен ключ DaData на сервере) — заполните вручную" : (e as Error).message);
    } finally { setBusy(false); }
  }
  function pick(f: Found, kind: "head" | "branch" = "head", inn?: string) {
    setForm((cur) => ({ ...cur, inn: f.inn || inn || cur.inn, name: f.name, full_name: f.full_name, kpp: f.kpp, ogrn: f.ogrn, address: f.address,
      director: f.director, legal_status: f.status, okved: f.okved, registry: f as unknown as Record<string, unknown> }));
    setPicked(kind); setShowBranches(false);
    setLookupMsg(`${kind === "branch" ? "Подставлен филиал" : "Подставлена головная организация"}: ${f.name}${f.status && f.status !== "ACTIVE" ? ` — внимание: организация ${STATUS_RU[f.status] || f.status}` : ""}. Проверьте и нажмите «Добавить».`);
  }
  return (
    <div className="grid" style={{ gridTemplateColumns: card ? "minmax(0, 2fr) minmax(0, 3fr)" : "1fr" }}>
      <div>
        {!embedded && <h1>Клиенты</h1>}
        {(() => {
          const fin = clients.some((c) => c.stats?.sum_won_rub != null);
          const st = (c: Client): Stats => c.stats || { deals: 0, open: 0, won: 0, lost: 0, last_on: null, last_number: "", last_status: "", manager: "", sum_won_rub: null };
          const managers = [...new Set(clients.map((c) => st(c).manager).filter(Boolean))].sort();
          const shown = clients.filter((c) => (!q || `${c.name} ${c.inn} ${c.full_name || ""}`.toLowerCase().includes(q.toLowerCase()))
            && (view === "all" || (view === "open" ? st(c).open > 0 : view === "won" ? st(c).won > 0 : st(c).deals === 0)) && (!mgr || st(c).manager === mgr))
            .sort((a, b) => { const A = st(a), B = st(b); const v = sort.k === "name" ? a.name.localeCompare(b.name, "ru") : sort.k === "deals" ? A.deals - B.deals : sort.k === "open" ? A.open - B.open
              : sort.k === "won" ? A.won - B.won : sort.k === "sum" ? (A.sum_won_rub || 0) - (B.sum_won_rub || 0) : String(A.last_on || "").localeCompare(String(B.last_on || "")); return v * sort.d; });
          const th = (k: SortKey, t: string, num = false) => <th className={num ? "num" : undefined} style={{ cursor: "pointer", whiteSpace: "nowrap" }} onClick={() => setSort({ k, d: sort.k === k ? (sort.d === 1 ? -1 : 1) : (k === "name" ? 1 : -1) })}>{t}{sort.k === k ? (sort.d === 1 ? " ↑" : " ↓") : ""}</th>;
          const kpis: [string, string | number][] = [["клиентов", clients.length], ["с открытыми сделками", clients.filter((c) => st(c).open > 0).length], ["выигравших", clients.filter((c) => st(c).won > 0).length],
            ["без сделок", clients.filter((c) => st(c).deals === 0).length], ...(fin ? [["сумма выигранных, RUB", fmtRub(clients.reduce((a, c) => a + (st(c).sum_won_rub || 0), 0))] as [string, string]] : [])];
          return (
            <div className="card">
              <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                <input placeholder="🔍 название, ИНН" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 220px" }} />
                <select value={mgr} onChange={(e) => setMgr(e.target.value)}><option value="">все менеджеры</option>{managers.map((m) => <option key={m}>{m}</option>)}</select>
                <div className="row" style={{ gap: 2 }}>{([["all", "все"], ["open", "с открытыми"], ["won", "выигравшие"], ["none", "без сделок"]] as const).map(([k, t]) => (
                  <button key={k} onClick={() => setView(k)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "6px 10px", fontWeight: view === k ? 700 : 500, borderBottom: view === k ? "2px solid var(--accent)" : "2px solid transparent", fontFamily: "inherit", fontSize: 13 }}>{t}</button>))}</div>
                {canCreate && <button className="btn add" style={{ marginLeft: "auto" }} onClick={() => setShowNew(!showNew)}>{showNew ? "скрыть форму" : "Новый клиент"}</button>}
              </div>
              <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
                {kpis.map(([t, v]) => <div key={t} style={{ flex: "1 1 120px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}><div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{t}</div></div>)}
              </div>
              <div style={{ overflowX: "auto", marginTop: 10 }}>
                <table style={{ fontSize: 13 }}>
                  <thead><tr>{th("name", "Клиент")}{th("deals", "Сделок", true)}{th("open", "В работе", true)}{th("won", "Выиграно", true)}{fin && th("sum", "Сумма выигранных, RUB", true)}{th("last", "Последняя сделка")}<th>Менеджер</th><th title="наборов реквизитов для договоров">Реквизиты</th></tr></thead>
                  <tbody>{shown.map((c) => { const x = st(c); return (
                    <tr key={c.id} style={{ cursor: "pointer", background: card?.id === c.id ? "var(--accent-soft)" : undefined }} onClick={() => api.get<Card>(`/api/refs/clients/${c.id}`).then(setCard)}>
                      <td><b>{c.name}</b><div className="muted" style={{ fontSize: 11 }}>ИНН {c.inn}{c.legal_status && c.legal_status !== "ACTIVE" && <span className="badge bad" style={{ marginLeft: 6 }}>{STATUS_RU[c.legal_status] || c.legal_status}</span>}</div></td>
                      <td className="num">{x.deals || <span className="muted">—</span>}</td><td className="num">{x.open ? <span className="badge info">{x.open}</span> : <span className="muted">—</span>}</td>
                      <td className="num">{x.won ? <span className="badge ok">{x.won}</span> : <span className="muted">—</span>}{x.lost > 0 && <span className="muted" style={{ fontSize: 11 }}> / {x.lost} пр.</span>}</td>
                      {fin && <td className="num">{x.sum_won_rub ? fmtRub(x.sum_won_rub) : <span className="muted">—</span>}</td>}
                      <td style={{ whiteSpace: "nowrap" }}>{x.last_number ? <>{x.last_number} <span className="badge" style={{ background: toneOf(x.last_status)[0], color: toneOf(x.last_status)[1], fontSize: 11 }}>{STATUS_TITLES[x.last_status] || x.last_status}</span><div className="muted" style={{ fontSize: 11 }}>{fmtDate(x.last_on)}</div></> : <span className="muted">—</span>}</td>
                      <td style={{ whiteSpace: "nowrap" }}>{x.manager || <span className="muted">—</span>}</td>
                      <td className="num">{c.requisites_count ? c.requisites_count : <span className="badge warn" title="нет наборов реквизитов для договоров">нет</span>}</td></tr>); })}
                    {shown.length === 0 && <tr><td colSpan={8} className="muted">Не найдено</td></tr>}</tbody>
                </table>
              </div>
            </div>);
        })()}
        {canCreate && showNew && (
          <div className="card">
            <b>Новый клиент</b> <span className="muted">— введите ИНН и нажмите «Найти»: наименование, КПП, ОГРН, адрес и руководитель подтянутся из ЕГРЮЛ/ЕГРИП</span>
            <div className="row" style={{ marginTop: 6 }}>
              <input placeholder="ИНН (10 или 12 цифр)" value={form.inn} inputMode="numeric" onChange={(e) => setForm({ ...form, inn: e.target.value.replace(/\D/g, "").slice(0, 12) })}
                onKeyDown={(e) => { if (e.key === "Enter" && form.inn.length >= 10) lookup(); }} style={{ width: 170 }} />
              <button className="btn secondary" onClick={lookup} disabled={busy || (form.inn.length !== 10 && form.inn.length !== 12)}>{busy ? "Поиск…" : "Найти по ИНН"}</button>
            </div>
            {lookupMsg && <div className="alert info" style={{ marginTop: 6 }}>{lookupMsg}</div>}
            {found && found.length > 0 && (
              <div style={{ marginTop: 4 }}>
                <a href="#" onClick={(e) => { e.preventDefault(); setShowBranches(!showBranches); }}>
                  {showBranches ? "скрыть филиалы" : `у организации есть филиалы (${found.length}${found.length >= 19 ? "+" : ""}) — выбрать филиал вместо головной`}</a>
                {picked === "branch" && <> · <a href="#" onClick={(e) => { e.preventDefault(); lookup(); }}>вернуть головную</a></>}
                {showBranches && (
                  <div style={{ maxHeight: 220, overflow: "auto", marginTop: 4, border: "1px solid var(--line)", borderRadius: 6, padding: 6 }}>
                    {found.map((f, i) => <div key={i} className="row" style={{ marginTop: 3 }}><button className="btn ghost" style={{ padding: "1px 8px" }} onClick={() => pick(f, "branch")}>выбрать</button>
                      <span style={{ fontSize: 12 }}>{f.name} · КПП {f.kpp || "—"} · {f.address}</span></div>)}
                  </div>)}
              </div>)}
            <div className="grid cols-2" style={{ marginTop: 6 }}>
              <label className="f">Наименование<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
              <label className="f">Полное наименование<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
              <label className="f">КПП<input value={form.kpp} onChange={(e) => setForm({ ...form, kpp: e.target.value })} /></label>
              <label className="f">ОГРН / ОГРНИП<input value={form.ogrn} onChange={(e) => setForm({ ...form, ogrn: e.target.value })} /></label>
              <label className="f">Руководитель<input value={form.director} onChange={(e) => setForm({ ...form, director: e.target.value })} /></label>
              <label className="f">Юридический адрес<input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></label>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" onClick={create} disabled={!form.name || (form.inn.length !== 10 && form.inn.length !== 12)}>Добавить</button><UndoButton h={history} />
              {(form.name || form.inn) && <button className="btn ghost" onClick={() => { setForm(EMPTY_FORM); setFound(null); setPicked(null); setLookupMsg(""); }}>очистить</button>}
              {err && <span className="badge bad">{err}</span>}
            </div>
          </div>
        )}
      </div>
      <div>
        {card && (
          <div className="card">
            <h2>{card.name}</h2>
            <table style={{ marginBottom: 8 }}><tbody>
              {card.full_name && <tr><td className="muted">Полное наименование</td><td>{card.full_name}</td></tr>}
              <tr><td className="muted">ИНН / КПП</td><td>{card.inn}{card.kpp ? ` / ${card.kpp}` : ""}</td></tr>
              {card.ogrn && <tr><td className="muted">ОГРН</td><td>{card.ogrn}</td></tr>}
              {card.director && <tr><td className="muted">Руководитель</td><td>{card.director}</td></tr>}
              {card.address && <tr><td className="muted">Адрес</td><td>{card.address}</td></tr>}
              {card.legal_status && <tr><td className="muted">Статус</td><td><span className={"badge " + (card.legal_status === "ACTIVE" ? "ok" : "bad")}>{STATUS_RU[card.legal_status] || card.legal_status}</span></td></tr>}
              <tr><td className="muted">Реестр</td><td>{card.registry_checked_at ? `проверено ${fmtDate(card.registry_checked_at)}` : "не проверялось"}
                {canCreate && <button className="btn ghost" style={{ marginLeft: 8, padding: "1px 8px" }} onClick={() => api.post(`/api/refs/clients/${card.id}/refresh-registry`).then(() => api.get<Card>(`/api/refs/clients/${card.id}`).then(setCard)).catch((e) => setErr((e as Error).message))}>обновить из реестра</button>}</td></tr>
            </tbody></table>
            <div className="row" style={{ gap: 24 }}>
              <div className="kpi"><span className="v">{card.funnel.requests}</span><span className="l">запросов</span></div>
              <div className="kpi"><span className="v">{card.funnel.kp_sent}</span><span className="l">КП</span></div>
              <div className="kpi"><span className="v">{card.funnel.won}</span><span className="l">выиграно</span></div>
              {card.avg_margin_pct !== undefined && <div className="kpi"><span className="v">{fmtPct(card.avg_margin_pct)}</span><span className="l">средняя маржа (закрытые)</span></div>}
              {card.sum_won_rub !== undefined && <div className="kpi"><span className="v">{fmtRub(card.sum_won_rub)}</span><span className="l">сумма выигранных, RUB</span></div>}
            </div>
            {card.kp_history ? (
              <>
                <h2>История КП (все версии) <span className="muted" style={{ fontWeight: 400 }}>· видят КД и ЗГД</span></h2>
                {card.kp_totals && <div className="row" style={{ gap: 20 }}><div className="kpi"><span className="v">{card.kp_totals.kp_count}</span><span className="l">КП</span></div><div className="kpi"><span className="v">{card.kp_totals.won}</span><span className="l">выиграно</span></div><div className="kpi"><span className="v">{fmtRub(card.kp_totals.sum_rub)}</span><span className="l">сумма последних версий, RUB</span></div></div>}
                <div className="row" style={{ margin: "6px 0" }}><input placeholder="модель" value={filter.model} onChange={(e) => setFilter({ ...filter, model: e.target.value })} />
                  <select value={filter.outcome} onChange={(e) => setFilter({ ...filter, outcome: e.target.value })}><option value="">все результаты</option><option value="won">выиграно</option><option value="lost">проиграно</option><option value="cancelled">отменено</option><option value="open">без результата</option></select>
                  <select value={filter.factory} onChange={(e) => setFilter({ ...filter, factory: e.target.value })}><option value="">все заводы</option>{[...new Set(card.kp_history.map((h) => h.factory))].filter(Boolean).map((f) => <option key={f} value={f}>{f}</option>)}</select>
                  <label className="row muted">с <input type="date" value={filter.from} onChange={(e) => setFilter({ ...filter, from: e.target.value })} /></label>
                  <label className="row muted">по <input type="date" value={filter.to} onChange={(e) => setFilter({ ...filter, to: e.target.value })} /></label>
                  {(filter.model || filter.outcome || filter.factory || filter.from || filter.to) && <button className="btn ghost" onClick={() => setFilter({ model: "", outcome: "", factory: "", from: "", to: "" })}>сбросить</button>}</div>
                <table><thead><tr><th>Дата</th><th>Сделка</th><th>Оборудование</th><th className="num">Кол-во</th><th className="num">Цена за ед., RUB</th><th className="num">Итого, RUB</th><th>Оплата</th><th>Срок</th><th>Результат</th><th>Менеджер</th><th>Файл</th></tr></thead>
                  <tbody>{card.kp_history.filter((h) => (!filter.model || h.model.toLowerCase().includes(filter.model.toLowerCase())) && (!filter.outcome || (filter.outcome === "open" ? !h.outcome : h.outcome === filter.outcome))
                    && (!filter.factory || h.factory === filter.factory) && (!filter.from || String(h.latest.on) >= filter.from) && (!filter.to || String(h.latest.on) <= filter.to)).map((h, i) => {
                    const latestDiff = verDiff(h.versions[0] || h.latest, h.versions[1]);
                    const key = h.deal_id + h.model + h.variant;
                    return (<Fragment key={key + i}>
                      <tr style={{ cursor: h.versions.length > 1 ? "pointer" : undefined }} onClick={() => setOpen({ ...open, [key]: !open[key] })}>
                        <td>{fmtDate(h.latest.on)}</td><td><a href={`/deals/${h.deal_id}`} onClick={(e) => e.stopPropagation()}>{h.deal_number}</a>{h.variant && <div className="muted">{h.variant}{h.chosen && " ✓"}</div>}</td>
                        <td>{h.model}<div className="muted" style={{ fontSize: 11 }}>{h.latest.kp_brand === "company" ? "бренд компании" : h.factory}</div></td><td className="num">{h.qty}</td>
                        <td className="num">{fmtRub(h.latest.unit_price_rub)}</td><td className="num">{fmtRub(h.latest.total_rub)}</td><td className="muted" style={{ fontSize: 11 }}>{(h.latest.payment_terms || []).map(payText).join("; ")}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{h.latest.delivery_term}{latestDiff.length > 0 && <div style={{ color: "var(--accent)" }}>к v{h.versions[1]?.version_no}: {latestDiff.join("; ")}</div>}</td><td><span className={"badge " + (h.outcome === "won" ? "ok" : h.outcome ? "bad" : "")}>{lbl(OUTCOME, h.outcome)}</span></td><td>{h.manager}</td>
                        <td>{h.latest.kp_path && <a href={`/api/files/${h.latest.kp_path}`} onClick={(e) => e.stopPropagation()}>v{h.latest.version_no}</a>}{h.versions.length > 1 && <span className="muted"> (+{h.versions.length - 1})</span>}</td>
                      </tr>
                      {open[key] && h.versions.slice(1).map((v, k) => { const d = verDiff(v, h.versions[k + 2]); return (
                        <tr key={key + v.version_no} style={{ background: "#f7f7f7" }}><td className="muted">{fmtDate(v.on)}</td><td className="muted">v{v.version_no}</td><td colSpan={2} className="muted" style={{ fontSize: 11 }}>{d.length > 0 ? `изменения к v${h.versions[k + 2]?.version_no}: ${d.join("; ")}` : k + 2 < h.versions.length ? "без изменений цены и условий" : "первая версия"}</td>
                          <td className="num">{fmtRub(v.unit_price_rub)}</td><td className="num">{fmtRub(v.total_rub)}</td><td className="muted" style={{ fontSize: 11 }}>{(v.payment_terms || []).map(payText).join("; ")}</td><td className="muted" style={{ fontSize: 11 }}>{v.delivery_term}</td><td /><td /><td>{v.kp_path && <a href={`/api/files/${v.kp_path}`}>файл</a>}</td></tr>); })}
                    </Fragment>);
                  })}</tbody></table>
              </>
            ) : <p className="muted">История КП и цен клиентам доступна КД и ЗГД (настраивается в уровнях доступа).</p>}
          </div>
        )}
        {card && <ClientRequisites clientId={card.id} canEdit={canReq} />}
      </div>
    </div>
  );
}
