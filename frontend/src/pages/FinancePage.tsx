import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney, newKey } from "../api";
import { can, type Me } from "../types";

// Раздел «Финансы», вариант Б (docs/finance/05_UI_UX_в_стилистике_CRM.md). Первый вертикальный срез:
// «Обзор», «Платёжный календарь», «Ликвидность и валюта» (счета, остатки, операции, разнесение).
// Остальные вкладки появятся по мере реализации — пустые вкладки-заглушки не показываем.

type Meta = { can_edit: boolean; entities: { id: string; code: string; name: string; currency: string }[]; currencies: string[]; today: string; statuses: Record<string, string> };
type Ref = { id: string; number: string; url: string } | null;
type Obligation = {
  id: string; direction: "in" | "out"; nature: string; title: string;
  source: { type: string; type_title: string; id: string; line_key: string; line_no: number | null; number: string; url: string | null };
  deal: Ref; contract: (Ref & { status?: string }) | null; legal_entity_id: string | null; counterparty: { kind: string; id: string | null; name: string };
  amount: string; currency: string; confirmed_paid: string; provisional_paid: string; remaining: string;
  legal_due_on: string | null; legal_due_text: string; forecast_on: string | null; event: { code: string; on: string | null; is_fact: boolean };
  status: string; status_title: string; days_overdue: number; is_active: boolean; excluded_reason: string; sync_status: string; flags: string[];
};
type Alloc = { id: string; transaction_id: string; obligation_id: string; amount_tx: string; amount_obligation: string; fx_rate: string | null; fx_on: string | null; fx_source: string;
  status: "provisional" | "confirmed" | "reversed"; allocated_on: string; reverse_reason: string; note: string;
  transaction?: { id: string; booked_at: string; amount: string; currency: string; status: string; external_ref: string; counterparty_name: string };
  obligation?: { id: string; title: string; currency: string; direction: string } };
type ObCard = Obligation & { terms_text: string; event_source: string; pending: { amount: string; currency: string } | null; allocations: Alloc[];
  history: { at: string; action: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null; comment: string }[]; readonly_note: string };
type Account = { id: string; title: string; currency: string; status: string; legal_entity: string; bank_name: string; account_masked: string; balance: string | null;
  balance_status: "unknown" | "confirmed"; last_snapshot: { as_of: string; amount: string; source: string } | null; movements_in: string | null; movements_out: string | null };
type Tx = { id: string; account_id: string | null; account_title: string; direction: "in" | "out"; amount: string; currency: string; booked_at: string; value_date: string | null;
  counterparty_name: string; purpose: string; external_ref: string; status: "draft" | "needs_reconciliation" | "posted" | "reversed"; origin_type: string;
  reversal_of_id: string | null; reversed_by_id: string | null; note: string; allocated: string; unallocated: string; allocations?: Alloc[]; hidden_allocations?: number;
  withdrawn: boolean; withdrawn_reason: string; origin_title: string };
type List<T> = { total: number; items: T[]; totals?: Record<string, Record<string, string>>; can_edit?: boolean };

const TABS = [["overview", "Обзор"], ["calendar", "Платёжный календарь"], ["liquidity", "Ликвидность и валюта"]] as const;
type Tab = (typeof TABS)[number][0];
const STATUS_BADGE: Record<string, string> = { paid: "ok", partially_paid: "info", overdue: "bad", due_soon: "warn", no_date: "", planned: "", superseded: "", excluded: "" };
const TX_STATUS: Record<Tx["status"], [string, string]> = { draft: ["черновик", ""], needs_reconciliation: ["требует сверки", "warn"], posted: ["проведена", "ok"], reversed: ["сторнирована", "bad"] };
const ALLOC_STATUS: Record<Alloc["status"], [string, string]> = { provisional: ["временное", "warn"], confirmed: ["подтверждено", "ok"], reversed: ["сторнировано", ""] };
const dir = (d: "in" | "out") => (d === "in" ? <span className="badge ok">нам платят</span> : <span className="badge bad">мы платим</span>);
const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);
const dt = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—");

/** «Заказ поставщику ЗК-…», «Партия 1» — без повтора типа, если номер его уже содержит. */
const srcLabel = (o: Pick<Obligation, "source">) => {
  const n = o.source.number || "";
  return !n ? o.source.type_title : n.toLowerCase().startsWith(o.source.type_title.toLowerCase()) ? n : `${o.source.type_title} ${n}`;
};

function StatusBadge({ o }: { o: Pick<Obligation, "status" | "status_title" | "days_overdue" | "flags"> }) {
  return <>
    <span className={`badge ${STATUS_BADGE[o.status] ?? ""}`}>{o.status_title}{o.days_overdue > 0 ? ` · ${o.days_overdue} дн.` : ""}</span>
    {o.flags.includes("needs_reconciliation") && <span className="badge warn" style={{ marginLeft: 4 }}>требует сверки</span>}
    {o.flags.includes("conflict") && <span className="badge bad" style={{ marginLeft: 4 }} title="Источник изменился после разнесения">конфликт с источником</span>}
  </>;
}

function Kpi({ label, children, bad }: { label: string; children: ReactNode; bad?: boolean }) {
  return <div className="card kpi" style={{ flex: "1 1 200px", margin: 0, borderLeft: bad ? "4px solid var(--bad)" : undefined }}><div className="v" style={{ fontSize: 17 }}>{children}</div><div className="l">{label}</div></div>;
}

export default function FinancePage({ me }: { me: Me }) {
  const [sp, setSp] = useSearchParams();
  const tab = (TABS.some(([k]) => k === sp.get("tab")) ? sp.get("tab") : "overview") as Tab;
  const [meta, setMeta] = useState<Meta | null>(null);
  const [err, setErr] = useState("");
  const [stamp, setStamp] = useState(0);   // «Обновить» и обновление зависимых блоков после действий
  const allowed = can(me, "finance_section");
  useEffect(() => { if (allowed) api.get<Meta>("/api/finance/meta").then(setMeta).catch((e) => setErr(errText(e))); }, [allowed]);
  const go = (patch: Record<string, string | null>) => {
    const n = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v == null || v === "" ? n.delete(k) : n.set(k, v)));
    setSp(n);
  };
  if (!allowed) return <div className="card muted">Раздел «Финансы» доступен при праве «Раздел «Финансы»» (по умолчанию — ФД и ЗГД; другим сотрудникам его выдаёт администратор в настройках доступа).</div>;
  return (
    <>
      <div className="page-head">
        <h1>Финансы</h1>
        <span className="muted">по состоянию на {fmtDate(meta?.today)}</span>
        <div className="push"><button className="btn secondary" onClick={() => setStamp((x) => x + 1)}>Обновить</button></div>
      </div>
      {err && <div className="alert error">{err}</div>}
      <div className="tabs">{TABS.map(([k, t]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => go({ tab: k })}>{t}</button>)}</div>
      {!meta && !err && <div className="muted">Загружаю…</div>}
      {meta && tab === "overview" && <Overview stamp={stamp} go={go} />}
      {meta && tab === "calendar" && <Calendar meta={meta} sp={sp} go={go} stamp={stamp} onChanged={() => setStamp((x) => x + 1)} />}
      {meta && tab === "liquidity" && <Liquidity meta={meta} sp={sp} go={go} stamp={stamp} onChanged={() => setStamp((x) => x + 1)} />}
    </>
  );
}

// ------------------------------------------------------------------ Обзор

type OverviewData = { as_of: string; horizon_days: number; available: { currency: string; value: string | null; accounts: number; unknown: number; status: string }[];
  stale_accounts: { id: string; title: string; currency: string }[];
  upcoming: { by_currency: Record<string, { in: string; out: string }>; items: Obligation[]; count: number };
  overdue: { by_currency: Record<string, { in: string; out: string }>; items: Obligation[]; count: number };
  no_date_count: number; unallocated: { currency: string; count: number; amount: string }[];
  reconciliation: { transactions_needs_reconciliation: number; obligation_conflicts: number; provisional_allocations: number };
  data_quality: { unknown_balance: boolean; conflicts: boolean; unallocated: boolean } };

function Overview({ stamp, go }: { stamp: number; go: (p: Record<string, string | null>) => void }) {
  const [d, setD] = useState<OverviewData | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setErr(""); api.get<OverviewData>("/api/finance/overview").then(setD).catch((e) => setErr(errText(e))); }, [stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted">Загружаю…</div>;
  const rec = d.reconciliation;
  const recCount = rec.transactions_needs_reconciliation + rec.obligation_conflicts + rec.provisional_allocations;
  const byCcy = (m: Record<string, { in: string; out: string }>, k: "in" | "out") => {
    const rows = Object.entries(m).filter(([, v]) => Number(v[k]) > 0);
    return rows.length ? rows.map(([c, v]) => <div key={c}>{fmtMoney(v[k], c)}</div>) : <span className="muted">нет</span>;
  };
  return (
    <>
      {(d.data_quality.unknown_balance || d.data_quality.conflicts || d.data_quality.unallocated) && (
        <div className="alert warn">
          Качество данных:{d.data_quality.unknown_balance && <> остаток не подтверждён по {d.stale_accounts.length} сч.;</>}
          {d.data_quality.conflicts && <> конфликтов с источником — {rec.obligation_conflicts};</>}
          {d.data_quality.unallocated && <> есть неразнесённые поступления/списания.</>}{" "}
          {d.data_quality.unknown_balance || d.data_quality.unallocated
            ? <button className="btn ghost" style={{ padding: "2px 8px" }} onClick={() => go({ tab: "liquidity" })}>Перейти к счетам и операциям</button>
            : <button className="btn ghost" style={{ padding: "2px 8px" }} onClick={() => go({ tab: "calendar", reconciliation: "1" })}>Показать строки на сверке</button>}
        </div>)}
      <div className="row" style={{ alignItems: "stretch", gap: 10, marginBottom: 14 }}>
        <Kpi label="доступно сейчас, по валютам">
          {d.available.length === 0 ? <span className="muted" style={{ fontSize: 14 }}>счета не заведены</span> : d.available.map((a) => (
            <div key={a.currency}>{a.value == null ? <span className="muted" style={{ fontSize: 14 }}>{a.currency}: остаток не подтверждён</span> : fmtMoney(a.value, a.currency)}
              {a.status === "partial" && <span className="badge warn" style={{ marginLeft: 6, fontSize: 11 }}>без {a.unknown} сч.</span>}</div>))}
        </Kpi>
        <Kpi label={`поступления, ${d.horizon_days} дней`}>{byCcy(d.upcoming.by_currency, "in")}</Kpi>
        <Kpi label={`платежи, ${d.horizon_days} дней`}>{byCcy(d.upcoming.by_currency, "out")}</Kpi>
        <Kpi label="просрочено" bad={d.overdue.count > 0}>
          {d.overdue.count === 0 ? <span className="muted">нет</span> : <>
            <div className="fin-kpi-line"><span className="muted">нам должны</span> {byCcy(d.overdue.by_currency, "in")}</div>
            <div className="fin-kpi-line"><span className="muted">мы должны</span> {byCcy(d.overdue.by_currency, "out")}</div></>}
        </Kpi>
        <Kpi label="требует сверки" bad={recCount > 0}>
          <div>{recCount}</div><div className="muted" style={{ fontSize: 12, fontWeight: 400 }}>записей на сверке {rec.transactions_needs_reconciliation} · временных разнесений {rec.provisional_allocations} · конфликтов {rec.obligation_conflicts}</div>
          {rec.transactions_needs_reconciliation > 0 && <button className="btn ghost" style={{ padding: "2px 8px", marginTop: 4, fontSize: 12 }} onClick={() => go({ tab: "liquidity", tx_status: "needs_reconciliation" })}>Открыть записи на сверке</button>}
        </Kpi>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <div className="section-head"><h2>Ближайшие платежи</h2><button className="btn ghost" style={{ padding: "2px 10px" }} onClick={() => go({ tab: "calendar", status: "open" })}>Показать все ({d.upcoming.count})</button></div>
          <MiniTable rows={d.upcoming.items} empty="На ближайшие 30 дней платежей нет" />
        </div>
        <div className="card">
          <div className="section-head"><h2>Просрочено</h2><button className="btn ghost" style={{ padding: "2px 10px" }} onClick={() => go({ tab: "calendar", status: "overdue" })}>Показать все ({d.overdue.count})</button></div>
          <MiniTable rows={d.overdue.items} empty="Просроченных обязательств нет" />
        </div>
        <div className="card">
          <div className="section-head"><h2>Неразнесённые операции</h2><button className="btn ghost" style={{ padding: "2px 10px" }} onClick={() => go({ tab: "liquidity", unallocated: "1" })}>Показать все</button></div>
          {d.unallocated.length === 0 ? <div className="muted">Все проведённые операции разнесены</div>
            : <table><tbody>{d.unallocated.map((u) => <tr key={u.currency}><td>{u.currency}</td><td className="num">{u.count} опер.</td><td className="num"><b>{fmtMoney(u.amount, u.currency)}</b></td></tr>)}</tbody></table>}
        </div>
        <div className="card">
          <div className="section-head"><h2>Без согласованной даты</h2><button className="btn ghost" style={{ padding: "2px 10px" }} onClick={() => go({ tab: "calendar", status: "no_date" })}>Показать все</button></div>
          <div className="muted">{d.no_date_count ? `${d.no_date_count} строк: срок зависит от события, которое ещё не наступило. В кривую по датам они не входят.` : "Строк графиков без даты нет."}</div>
          <UndatedSummary stamp={stamp} go={go} />
        </div>
      </div>
    </>
  );
}

type UndatedData = { parts: { items: { id: string; number: string; client: string; manager: string; amount: string; currency: string; lines: number; urgent: boolean; url: string }[]; total: string; currency: string; note: string } };

function useUndated(stamp: number) {
  const [d, setD] = useState<UndatedData | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<UndatedData>("/api/finance/undated").then(setD).catch((e) => setErr(errText(e))); }, [stamp]);
  return { d, err };
}

function UndatedSummary({ stamp, go }: { stamp: number; go: (p: Record<string, string | null>) => void }) {
  const { d } = useUndated(stamp);
  if (!d || !d.parts.items.length) return null;
  return <div style={{ marginTop: 6 }}>Запчасти: заказано и не отгружено по {d.parts.items.length} заявк. — <b>{fmtMoney(d.parts.total, d.parts.currency)}</b>, срок не согласован.{" "}
    <button className="btn ghost" style={{ padding: "0 8px", fontSize: 12 }} onClick={() => go({ tab: "calendar", direction: "in" })}>Подробнее</button></div>;
}

/** Ожидаемые суммы без согласованной даты — визуально отдельно от датированного календаря и просрочки (05, раздел 9). */
function Undated({ stamp }: { stamp: number }) {
  const { d, err } = useUndated(stamp);
  if (err) return <div className="alert error">{err}</div>;
  if (!d || !d.parts.items.length) return null;
  return (
    <div className="card" style={{ marginTop: 14, background: "#fcfcfc", borderStyle: "dashed" }}>
      <div className="section-head"><h2>Ожидается, срок не согласован</h2><span className="muted">запчасти · {fmtMoney(d.parts.total, d.parts.currency)}</span></div>
      <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>{d.parts.note}</div>
      <table style={{ fontSize: 13 }}><thead><tr><th>Заявка</th><th>Клиент</th><th className="num">Позиций</th><th className="num">Сумма</th><th>Менеджер</th></tr></thead>
        <tbody>{d.parts.items.map((p) => <tr key={p.id}><td><Link to={p.url}>{p.number}</Link>{p.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td>
          <td>{p.client}</td><td className="num">{p.lines}</td><td className="num"><b>{fmtMoney(p.amount, p.currency)}</b></td><td>{p.manager}</td></tr>)}</tbody></table>
    </div>
  );
}

function MiniTable({ rows, empty }: { rows: Obligation[]; empty: string }) {
  if (!rows.length) return <div className="muted">{empty}</div>;
  return <table style={{ fontSize: 13 }}><thead><tr><th>Срок</th><th>Документ</th><th>Контрагент</th><th className="num">Остаток</th></tr></thead>
    <tbody>{rows.map((o) => <tr key={o.id}><td style={{ whiteSpace: "nowrap" }}>{fmtDate(o.legal_due_on || o.forecast_on)}{!o.legal_due_on && <div className="muted" style={{ fontSize: 11 }}>ожидаемая</div>}</td>
      <td>{o.contract ? o.contract.number : o.source.type_title}{o.deal && <div className="muted" style={{ fontSize: 11 }}>{o.deal.number}</div>}</td>
      <td>{dir(o.direction)} {o.counterparty.name}</td><td className="num"><b>{fmtMoney(o.remaining, o.currency)}</b></td></tr>)}</tbody></table>;
}

// ------------------------------------------------------------------ Платёжный календарь

function Calendar({ meta, sp, go, stamp, onChanged }: { meta: Meta; sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number; onChanged: () => void }) {
  const f = { direction: sp.get("direction") || "", status: sp.get("status") || "", currency: sp.get("currency") || "", legal_entity_id: sp.get("entity") || "",
    search: sp.get("q") || "", contract_id: sp.get("contract_id") || "", source_type: sp.get("source") || "", deal_id: sp.get("deal_id") || "", date_from: sp.get("from") || "", date_to: sp.get("to") || "",
    reconciliation: sp.get("reconciliation") === "1", include_inactive: sp.get("inactive") === "1" };
  const open = sp.get("open");
  const [data, setData] = useState<List<Obligation> | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState(f.search);
  const qs = useMemo(() => {
    const p = new URLSearchParams({ limit: "500" });
    Object.entries(f).forEach(([k, v]) => { if (v === true) p.set(k, "true"); else if (v) p.set(k, String(v)); });
    return p.toString();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  useEffect(() => { setErr(""); api.get<List<Obligation>>(`/api/finance/obligations?${qs}`).then(setData).catch((e) => setErr(errText(e))); }, [qs, stamp]);
  const active = [f.direction, f.status, f.currency, f.legal_entity_id, f.search, f.contract_id, f.deal_id, f.date_from, f.date_to, f.reconciliation, f.include_inactive, f.source_type].filter(Boolean).length;
  return (
    <>
      <div className="card" style={{ padding: 10 }}>
        <div className="row">
          <select value={f.direction} onChange={(e) => go({ direction: e.target.value })} aria-label="Направление">
            <option value="">все направления</option><option value="in">нам платят</option><option value="out">мы платим</option></select>
          <select value={f.status} onChange={(e) => go({ status: e.target.value })} aria-label="Статус">
            <option value="">все статусы</option><option value="open">не оплачено</option>
            {Object.entries(meta.statuses).filter(([k]) => !["superseded", "excluded"].includes(k)).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <select value={f.currency} onChange={(e) => go({ currency: e.target.value })} aria-label="Валюта">
            <option value="">все валюты</option>{meta.currencies.map((c) => <option key={c}>{c}</option>)}</select>
          <select value={f.source_type} onChange={(e) => go({ source: e.target.value })} aria-label="Источник">
            <option value="">все источники</option><option value="contract">договоры</option><option value="purchase_order">заказы поставщикам</option><option value="batch">логистические партии</option><option value="calculation">прогноз по расчёту</option></select>
          <select value={f.legal_entity_id} onChange={(e) => go({ entity: e.target.value })} aria-label="Компания группы">
            <option value="">все компании группы</option>{meta.entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
          <label className="row" style={{ gap: 4 }} title="Срок по договору, а если он ещё не определён — ожидаемая дата">с <input type="date" value={f.date_from} onChange={(e) => go({ from: e.target.value })} />
            по <input type="date" value={f.date_to} onChange={(e) => go({ to: e.target.value })} /></label>
          <form onSubmit={(e) => { e.preventDefault(); go({ q }); }} style={{ flex: "1 1 220px", display: "flex" }}>
            <input placeholder="🔍 договор, сделка, контрагент" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} onBlur={() => q !== f.search && go({ q })} /></form>
        </div>
        <div className="row" style={{ marginTop: 8, fontSize: 13 }}>
          <label><input type="checkbox" checked={f.reconciliation} onChange={(e) => go({ reconciliation: e.target.checked ? "1" : null })} /> только требующие сверки</label>
          <label><input type="checkbox" checked={f.include_inactive} onChange={(e) => go({ inactive: e.target.checked ? "1" : null })} /> показать исключённые из итогов</label>
          {f.deal_id && <span className="badge info">отбор по сделке {data?.items[0]?.deal?.number ?? ""} <button className="btn ghost" style={{ padding: "0 6px", minHeight: 0, marginLeft: 4 }} onClick={() => go({ deal_id: null })} aria-label="Снять отбор по сделке">×</button></span>}
          {f.contract_id && <span className="badge info">отбор по договору {data?.items[0]?.contract?.number ?? ""} <button className="btn ghost" style={{ padding: "0 6px", minHeight: 0, marginLeft: 4 }} onClick={() => go({ contract_id: null })} aria-label="Снять отбор по договору">×</button></span>}
          {active > 0 && <button className="btn ghost" style={{ padding: "2px 10px", marginLeft: "auto" }} onClick={() => { setQ(""); go({ direction: null, status: null, currency: null, entity: null, q: null, contract_id: null, deal_id: null, from: null, to: null, reconciliation: null, inactive: null, source: null }); }}>Сбросить фильтры ({active})</button>}
        </div>
      </div>
      {err && <div className="alert error">{err}</div>}
      {!data && !err && <div className="muted">Загружаю…</div>}
      {data && data.totals && Object.keys(data.totals).length > 0 && (
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>{Object.entries(data.totals).map(([c, t]) => (
          <div key={c} className="card" style={{ margin: 0, padding: "8px 12px", fontSize: 13 }}><b>{c}</b> · к получению <b>{fmtMoney(t.in_remaining, c)}</b> из {fmtMoney(t.in)} · к оплате <b>{fmtMoney(t.out_remaining, c)}</b> из {fmtMoney(t.out)}</div>))}</div>)}
      {data && (
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <table className="fin-cal">
            <thead><tr><th>Срок по договору</th><th className="sec">Ожидаемая дата</th><th className="sec">Сделка / документ</th><th>Контрагент</th><th className="sec">Строка графика</th><th className="sec">Направление</th>
              <th className="num sec">Сумма</th><th className="num sec">Оплачено</th><th className="num">Остаток</th><th>Статус</th><th className="sec">Источник</th><th></th></tr></thead>
            <tbody>{data.items.map((o) => (
              <Fragment key={o.id}>
                <tr style={{ ...(o.status === "overdue" ? { boxShadow: "inset 3px 0 0 var(--bad)" } : {}), ...(o.is_active ? {} : { color: "var(--muted)" }), cursor: "pointer" }}
                  onClick={() => go({ open: open === o.id ? null : o.id })}>
                  <td className="fin-due">{o.nature === "forecast" ? <span className="badge info" title={o.legal_due_text}>прогноз</span> : o.legal_due_on ? fmtDate(o.legal_due_on) : <span className="muted" style={{ fontSize: 12 }} title={o.legal_due_text}>не определён — {o.legal_due_text.replace(/^[^—]*?в течение/, "в течение") || "событие не наступило"}</span>}</td>
                  <td className="sec" style={{ whiteSpace: "nowrap" }}>{o.legal_due_on && o.forecast_on === o.legal_due_on ? <span className="muted">—</span> : fmtDate(o.forecast_on)}</td>
                  <td className="sec">{o.deal ? <Link to={o.deal.url} onClick={(e) => e.stopPropagation()}><b>{o.deal.number}</b></Link> : null}
                    <div style={{ fontSize: 12 }}>{o.contract ? o.contract.number : srcLabel(o)}</div></td>
                  <td>{o.counterparty.name}<div className="fin-narrow muted" style={{ fontSize: 11 }}>{o.direction === "in" ? "нам платят" : "мы платим"} · {o.contract?.number || o.source.number || o.source.type_title}</div></td>
                  <td className="sec" style={{ fontSize: 13 }}>{o.title.replace(/^[^:]+:\s*/, "")}</td>
                  <td className="sec">{dir(o.direction)}</td>
                  <td className="num sec">{fmtMoney(o.amount, o.currency)}</td>
                  <td className="num sec">{fmtMoney(o.confirmed_paid)}{Number(o.provisional_paid) > 0 && <div className="muted" style={{ fontSize: 11 }}>врем. {fmtMoney(o.provisional_paid)}</div>}</td>
                  <td className="num"><b>{fmtMoney(o.remaining, o.currency)}</b></td>
                  <td><StatusBadge o={o} />{!o.is_active && <div className="muted" style={{ fontSize: 11 }}>{o.excluded_reason}</div>}</td>
                  <td className="sec">{o.source.url ? <Link to={o.source.url} onClick={(e) => e.stopPropagation()}>{o.source.type_title}</Link> : o.source.type_title}</td>
                  <td><button className="btn ghost" style={{ padding: "2px 10px" }} aria-expanded={open === o.id}>{open === o.id ? "Свернуть" : "Открыть"}</button></td>
                </tr>
                {open === o.id && <tr><td colSpan={12} style={{ background: "#fcfcfc", padding: 12 }}><ObligationCard id={o.id} canEdit={meta.can_edit} stamp={stamp} onChanged={onChanged} /></td></tr>}
              </Fragment>))}
              {data.items.length === 0 && <tr><td colSpan={12} className="muted" style={{ padding: 16 }}>
                {active ? "По выбранным фильтрам обязательств нет." : "Обязательств пока нет: они появляются из графиков платежей подписанных договоров, подтверждённых заказов поставщикам и логистических партий."}</td></tr>}
            </tbody>
          </table>
        </div>)}
      {!f.direction || f.direction === "in" ? <Undated stamp={stamp} /> : null}
    </>
  );
}

function ObligationCard({ id, canEdit, stamp, onChanged }: { id: string; canEdit: boolean; stamp: number; onChanged: () => void }) {
  const [c, setC] = useState<ObCard | null>(null);
  const [err, setErr] = useState("");
  const [why, setWhy] = useState<Record<string, unknown> | null>(null);
  const [allocating, setAllocating] = useState(false);
  const load = useCallback(() => api.get<ObCard>(`/api/finance/obligations/${id}`).then(setC).catch((e) => setErr(errText(e))), [id]);
  useEffect(() => { load(); }, [load, stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!c) return <div className="muted">Загружаю карточку…</div>;
  const changed = () => { load(); onChanged(); };
  return (
    <div className="grid cols-2">
      <div>
        <div className="row" style={{ justifyContent: "space-between" }}><b>{c.title}</b><StatusBadge o={c} /></div>
        <table style={{ marginTop: 8, fontSize: 13 }}><tbody>
          <tr><td className="muted">Сумма</td><td className="num"><b>{fmtMoney(c.amount, c.currency)}</b></td></tr>
          <tr><td className="muted">Подтверждённо оплачено</td><td className="num">{fmtMoney(c.confirmed_paid, c.currency)}</td></tr>
          {Number(c.provisional_paid) > 0 && <tr><td className="muted">Временно разнесено (не входит в оплату)</td><td className="num">{fmtMoney(c.provisional_paid, c.currency)}</td></tr>}
          <tr><td className="muted">Остаток</td><td className="num"><b>{fmtMoney(c.remaining, c.currency)}</b></td></tr>
          <tr><td className="muted">Условие</td><td>{c.terms_text || "—"}</td></tr>
          <tr><td className="muted">Срок по договору</td><td>{c.legal_due_on ? fmtDate(c.legal_due_on) : <>не определён — {c.legal_due_text || "событие ещё не наступило"}</>}</td></tr>
          <tr><td className="muted">Событие</td><td>{c.event.on ? `${fmtDate(c.event.on)} · ${c.event.is_fact ? "факт" : "план"}` : "не наступило"}{c.event_source && <span className="muted"> ({c.event_source})</span>}</td></tr>
          <tr><td className="muted">Ожидаемая дата</td><td>{fmtDate(c.forecast_on)}</td></tr>
          <tr><td className="muted">Связи</td><td>{c.deal && <><Link to={c.deal.url}>сделка {c.deal.number}</Link> · </>}{c.contract && <Link to={c.contract.url}>договор {c.contract.number}</Link>}</td></tr>
        </tbody></table>
        {c.pending && <div className="alert warn" style={{ fontSize: 13 }}>Источник изменился: в договоре теперь {fmtMoney(c.pending.amount, c.pending.currency)}. По строке уже есть разнесения, поэтому сумма не перезаписана — нужна сверка.</div>}
        <div className="alert info" style={{ fontSize: 13 }}>{c.readonly_note}</div>
        <div className="row">
          <button className="btn secondary" onClick={() => (why ? setWhy(null) : api.get<Record<string, unknown>>(`/api/finance/obligations/${id}/explanation`).then(setWhy).catch((e) => setErr(errText(e))))}>
            {why ? "Скрыть объяснение" : "Объяснить цифру"}</button>
          {canEdit && c.is_active && c.sync_status === "ok" && Number(c.remaining) > 0 && <button className="btn" onClick={() => setAllocating((x) => !x)}>{allocating ? "Отмена" : "Разнести операцию"}</button>}
        </div>
        {why && <Explanation e={why} />}
        {allocating && <AllocateFromObligation ob={c} onDone={() => { setAllocating(false); changed(); }} />}
      </div>
      <div>
        <b>Разнесения</b>
        <AllocTable rows={c.allocations} canEdit={canEdit} onChanged={changed} side="obligation" />
        <details style={{ marginTop: 10 }}><summary className="muted">История источника и синхронизации ({c.history.length})</summary>
          <table style={{ fontSize: 12 }}><tbody>{c.history.map((h, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{dt(h.at)}</td><td>{h.action}</td><td className="muted">{h.comment || JSON.stringify(h.after)}</td></tr>)}</tbody></table>
        </details>
      </div>
    </div>
  );
}

function Explanation({ e }: { e: Record<string, unknown> }) {
  const inc = (e.included as { id: string; amount: string; status: string; transaction_id: string }[]) || [];
  const exc = (e.excluded as { id: string; amount: string; reason: string }[]) || [];
  const src = e.source as { type: string; line_key: string; revision: string; synced_at: string | null };
  return (
    <div className="card" style={{ marginTop: 10, fontSize: 13 }}>
      <div><b>Остаток {fmtMoney(String(e.value), String(e.currency))}</b> на {fmtDate(String(e.as_of))}</div>
      <div className="muted">{String(e.formula)}</div>
      <div style={{ marginTop: 6 }}>Сумма обязательства: {fmtMoney(String(e.amount), String(e.currency))}</div>
      <div>Учтено ({inc.length}): {inc.map((x) => fmtMoney(x.amount)).join(" + ") || "—"}</div>
      {exc.length > 0 && <div>Не учтено: {exc.map((x) => `${fmtMoney(x.amount)} — ${x.reason}`).join("; ")}</div>}
      <div className="muted" style={{ marginTop: 6 }}>{String(e.status_rule)}</div>
      <div className="muted">Источник: {src.type} · строка {src.line_key} · ревизия {src.revision.slice(0, 10)}… · сверено {dt(src.synced_at)}</div>
    </div>
  );
}

function AllocTable({ rows, canEdit, onChanged, side, txStatus }: { rows: Alloc[]; canEdit: boolean; onChanged: () => void; side: "obligation" | "transaction"; txStatus?: string }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const act = async (a: Alloc, what: "confirm" | "reverse") => {
    let reason = "";
    if (what === "reverse") {
      reason = window.prompt("Причина отмены разнесения") || "";
      if (!reason.trim()) return;
    }
    setBusy(a.id); setErr("");
    try { await api.post(`/api/finance/allocations/${a.id}/${what}`, what === "reverse" ? { reason } : undefined); onChanged(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(""); }
  };
  if (!rows.length) return <div className="muted" style={{ marginTop: 6 }}>Разнесений нет</div>;
  return <>
    {err && <div className="alert error">{err}</div>}
    <table style={{ marginTop: 6, fontSize: 13 }}>
      <thead><tr><th>{side === "obligation" ? "Операция" : "Обязательство"}</th><th className="num">Сумма</th><th>Статус</th><th></th></tr></thead>
      <tbody>{rows.map((a) => (
        <tr key={a.id} style={a.status === "reversed" ? { color: "var(--muted)" } : undefined}>
          <td>{side === "obligation" && a.transaction ? <>{dt(a.transaction.booked_at)} · {a.transaction.external_ref || "без номера"}<div className="muted" style={{ fontSize: 11 }}>{a.transaction.counterparty_name} · операция {TX_STATUS[a.transaction.status as Tx["status"]]?.[0] ?? a.transaction.status}</div></>
            : a.obligation?.title}</td>
          <td className="num">{fmtMoney(side === "obligation" ? a.amount_obligation : a.amount_tx)}{a.fx_rate && <div className="muted" style={{ fontSize: 11 }} title={`курс ${a.fx_rate} на ${fmtDate(a.fx_on)}, ${a.fx_source}`}>по курсу {a.fx_rate}</div>}</td>
          <td><span className={`badge ${ALLOC_STATUS[a.status][1]}`}>{ALLOC_STATUS[a.status][0]}</span>{a.reverse_reason && <div className="muted" style={{ fontSize: 11 }}>{a.reverse_reason}</div>}</td>
          <td style={{ whiteSpace: "nowrap" }}>{canEdit && a.status === "provisional" && ((side === "transaction" ? txStatus : a.transaction?.status) === "posted"
              ? <button className="btn" style={{ padding: "2px 8px" }} disabled={busy === a.id} onClick={() => act(a, "confirm")}>Подтвердить</button>
              : <span className="muted" style={{ fontSize: 11 }}>подтверждение — после проведения операции </span>)}
            {canEdit && a.status !== "reversed" && <button className="btn ghost" style={{ padding: "2px 8px", marginLeft: 4 }} disabled={busy === a.id} onClick={() => act(a, "reverse")}>Отменить</button>}</td>
        </tr>))}</tbody>
    </table>
  </>;
}

/** Разнесение со стороны обязательства: выбрать операцию того же направления с неразнесённым остатком. */
function AllocateFromObligation({ ob, onDone }: { ob: ObCard; onDone: () => void }) {
  const [list, setList] = useState<Tx[] | null>(null);
  const [txId, setTxId] = useState("");
  const [amount, setAmount] = useState("");
  const [fx, setFx] = useState({ rate: "", on: "", source: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const key = useRef(newKey());
  useEffect(() => { api.get<List<Tx>>(`/api/finance/transactions?direction=${ob.direction}&unallocated=true&limit=500`).then((r) => setList(r.items.filter((t) => t.status !== "reversed"))).catch((e) => setErr(errText(e))); }, [ob.direction]);
  const tx = list?.find((t) => t.id === txId);
  const cross = !!tx && tx.currency !== ob.currency;
  const obAmount = cross && fx.rate && amount ? (Number(amount) * Number(fx.rate)).toFixed(2) : amount;
  const submit = async () => {
    if (!tx) return;
    setBusy(true); setErr("");
    try {
      await api.postIdem(`/api/finance/transactions/${tx.id}/allocations`, { status: "provisional",
        items: [{ obligation_id: ob.id, amount_tx: amount, amount_obligation: obAmount, ...(cross ? { fx_rate: fx.rate, fx_on: fx.on, fx_source: fx.source } : {}) }] }, key.current);
      key.current = newKey(); onDone();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <b>Разнести на эту строку</b>
      {err && <div className="alert error">{err}</div>}
      {!list ? <div className="muted">Загружаю операции…</div> : list.length === 0 ? <div className="muted">Нет операций ({ob.direction === "in" ? "поступлений" : "списаний"}) с неразнесённым остатком. Внесите операцию во вкладке «Ликвидность и валюта».</div> : <>
        <label className="f" style={{ marginTop: 6 }}>Операция
          <select value={txId} onChange={(e) => { setTxId(e.target.value); const t = list.find((x) => x.id === e.target.value); if (t) setAmount(Math.min(Number(t.unallocated), t.currency === ob.currency ? Number(ob.remaining) : Number(t.unallocated)).toFixed(2)); }}>
            <option value="">— выберите —</option>
            {list.map((t) => <option key={t.id} value={t.id}>{dt(t.booked_at)} · {fmtMoney(t.unallocated, t.currency)} свободно · {t.counterparty_name || t.external_ref || "без контрагента"} · {TX_STATUS[t.status][0]}</option>)}
          </select></label>
        {tx && <>
          <div className="row" style={{ marginTop: 6 }}>
            <label className="f">Сумма из операции, {tx.currency}<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} /></label>
            {cross && <><label className="f">Курс {ob.currency} за 1 {tx.currency}<input inputMode="decimal" value={fx.rate} onChange={(e) => setFx({ ...fx, rate: e.target.value.replace(",", ".") })} /></label>
              <label className="f">Дата курса<input type="date" value={fx.on} onChange={(e) => setFx({ ...fx, on: e.target.value })} /></label>
              <label className="f">Источник курса<input value={fx.source} placeholder="ЦБ РФ / банк" onChange={(e) => setFx({ ...fx, source: e.target.value })} /></label></>}
          </div>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>Свободно в операции {fmtMoney(tx.unallocated, tx.currency)} · остаток строки {fmtMoney(ob.remaining, ob.currency)} · погашение {fmtMoney(obAmount || "0", ob.currency)}
            {Number(obAmount) > Number(ob.remaining) && <b style={{ color: "var(--bad)" }}> — больше остатка строки</b>}
            {Number(amount) > Number(tx.unallocated) && <b style={{ color: "var(--bad)" }}> — больше свободного остатка операции</b>}
            {" · разнесение сохранится временным; подтвердите его отдельно"}{tx.status !== "posted" && " после проведения операции"}</div>
          <button className="btn" style={{ marginTop: 8 }} disabled={busy || !Number(amount) || (cross && (!fx.rate || !fx.on || !fx.source))} onClick={submit}>{busy ? "Сохраняю…" : "Разнести"}</button>
        </>}
      </>}
    </div>
  );
}

// ------------------------------------------------------------------ Ликвидность и валюта: счета, остатки, операции

function Liquidity({ meta, sp, go, stamp, onChanged }: { meta: Meta; sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number; onChanged: () => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [err, setErr] = useState("");
  const [newAcc, setNewAcc] = useState(false);
  const [snapFor, setSnapFor] = useState("");
  useEffect(() => { setErr(""); api.get<{ items: Account[] }>("/api/finance/accounts").then((r) => setAccounts(r.items)).catch((e) => setErr(errText(e))); }, [stamp]);
  return (
    <>
      <div className="card">
        <div className="section-head"><h2>Счета и остатки</h2>{meta.can_edit && <button className="btn add" onClick={() => setNewAcc((x) => !x)}>Счёт</button>}</div>
        {err && <div className="alert error">{err}</div>}
        {newAcc && <AccountForm onDone={() => { setNewAcc(false); onChanged(); }} />}
        {!accounts ? <div className="muted">Загружаю…</div> : accounts.length === 0 ? <div className="muted">Финансовых счетов нет. {meta.can_edit ? "Добавьте счёт на банковском профиле компании группы." : "Их заводят ФД или ЗГД."}</div> : (
          <div style={{ overflowX: "auto" }}><table>
            <thead><tr><th>Счёт</th><th>Компания группы</th><th>Банк</th><th className="num">Остаток</th><th>Подтверждён</th><th></th></tr></thead>
            <tbody>{accounts.map((a) => (
              <Fragment key={a.id}>
                <tr>
                  <td><b>{a.title}</b> <span className="muted">{a.currency}</span></td><td>{a.legal_entity}</td><td>{a.bank_name} <span className="muted">{a.account_masked}</span></td>
                  <td className="num">{a.balance == null ? <span className="badge warn">остаток не подтверждён</span> : <b>{fmtMoney(a.balance, a.currency)}</b>}</td>
                  <td style={{ fontSize: 12 }}>{a.last_snapshot ? <>{dt(a.last_snapshot.as_of)}: {fmtMoney(a.last_snapshot.amount)}<div className="muted">движение после: +{fmtMoney(a.movements_in)} / −{fmtMoney(a.movements_out)}</div></> : "—"}</td>
                  <td>{meta.can_edit && <button className="btn ghost" style={{ padding: "2px 10px" }} onClick={() => setSnapFor(snapFor === a.id ? "" : a.id)}>Подтвердить остаток</button>}</td>
                </tr>
                {snapFor === a.id && <tr><td colSpan={6}><SnapshotForm a={a} onDone={() => { setSnapFor(""); onChanged(); }} /></td></tr>}
              </Fragment>))}</tbody>
          </table></div>)}
      </div>
      <Forecast sp={sp} go={go} stamp={stamp} />
      <Transactions meta={meta} accounts={accounts || []} sp={sp} go={go} stamp={stamp} onChanged={onChanged} />
    </>
  );
}

type BankOption = { bank_profile_id: string; legal_entity_id: string; legal_entity: string; profile: string; title: string; bank_name: string; account_masked: string; currency: string; has_account: boolean };

function AccountForm({ onDone }: { onDone: () => void }) {
  const [opts, setOpts] = useState<BankOption[] | null>(null);
  const [sel, setSel] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ items: BankOption[] }>("/api/finance/accounts/options").then((r) => setOpts(r.items)).catch((e) => setErr(errText(e))); }, []);
  const o = opts?.find((x) => x.bank_profile_id === sel);
  const submit = async () => {
    if (!o) return;
    setBusy(true); setErr("");
    try { await api.post("/api/finance/accounts", { legal_entity_id: o.legal_entity_id, bank_profile_id: o.bank_profile_id, title }); onDone(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ background: "#fcfcfc" }}>
      <b>Новый финансовый счёт</b> <span className="muted">— на существующем банковском профиле компании группы; реквизиты не копируются</span>
      {err && <div className="alert error">{err}</div>}
      {!opts ? <div className="muted">Загружаю…</div> : opts.length === 0 ? <div className="muted">У компаний группы нет действующих банковских профилей. Добавьте их в «Юридический блок» → карточка БЕРГ-ХАУС.</div> : (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
          <label className="f" style={{ flex: "1 1 320px" }}>Банковский профиль
            <select value={sel} onChange={(e) => setSel(e.target.value)}><option value="">— выберите —</option>
              {opts.map((x) => <option key={x.bank_profile_id} value={x.bank_profile_id} disabled={x.has_account}>{x.legal_entity} · {x.title} · {x.bank_name} {x.account_masked} · {x.currency}{x.has_account ? " (счёт уже есть)" : ""}</option>)}</select></label>
          <label className="f">Рабочее название<input value={title} placeholder={o?.title || ""} onChange={(e) => setTitle(e.target.value)} /></label>
          <button className="btn" disabled={!o || busy} onClick={submit}>{busy ? "Сохраняю…" : "Создать счёт"}</button>
        </div>)}
    </div>
  );
}

const localNow = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const localDayStart = () => `${localNow().slice(0, 10)}T00:00`;   // входящий остаток выписки — на начало дня
const toIso = (local: string) => new Date(local).toISOString();   // datetime-local → момент с часовым поясом (UTC)

function SnapshotForm({ a, onDone }: { a: Account; onDone: () => void }) {
  const [asOf, setAsOf] = useState(localDayStart());
  const [amount, setAmount] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const key = useRef(newKey());
  const submit = async () => {
    setBusy(true); setErr("");
    try { await api.postIdem(`/api/finance/accounts/${a.id}/balance-snapshots`, { as_of: toIso(asOf), amount, external_ref: ref }, key.current); key.current = newKey(); onDone(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      {err && <div className="alert error" style={{ flexBasis: "100%" }}>{err}</div>}
      <label className="f">Остаток на момент (входящий — на начало дня)<input type="datetime-local" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
      <label className="f">Сумма, {a.currency}<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(",", "."))} /></label>
      <label className="f" style={{ flex: "1 1 200px" }}>Основание (выписка, документ)<input value={ref} onChange={(e) => setRef(e.target.value)} /></label>
      <button className="btn" disabled={busy || amount === ""} onClick={submit}>{busy ? "Сохраняю…" : "Подтвердить"}</button>
      <span className="muted" style={{ fontSize: 12, flexBasis: "100%" }}>В остаток входят операции, проведённые до указанного момента включительно; более поздние прибавляются к нему. Прошлый снимок не перезаписывается — сохраняется новый, история остаётся.</span>
    </div>
  );
}

function Transactions({ meta, accounts, sp, go, stamp, onChanged }: { meta: Meta; accounts: Account[]; sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number; onChanged: () => void }) {
  const f = { account_id: sp.get("account") || "", status: sp.get("tx_status") || "", unallocated: sp.get("unallocated") === "1", origin: sp.get("origin") || "", withdrawn: sp.get("withdrawn") === "1" };
  const open = sp.get("tx");
  const [data, setData] = useState<List<Tx> | null>(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  useEffect(() => {
    const p = new URLSearchParams({ limit: "500" });
    if (f.account_id) p.set("account_id", f.account_id);
    if (f.status) p.set("status", f.status);
    if (f.unallocated) p.set("unallocated", "true");
    if (f.origin) p.set("origin", f.origin);
    if (f.withdrawn) p.set("include_withdrawn", "true");
    setErr(""); api.get<List<Tx>>(`/api/finance/transactions?${p}`).then(setData).catch((e) => setErr(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp, stamp]);
  return (
    <div className="card">
      <div className="section-head"><h2>Банковские операции</h2>
        <div className="row">
          <select value={f.account_id} onChange={(e) => go({ account: e.target.value })} aria-label="Счёт"><option value="">все счета</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.title} · {a.currency}</option>)}</select>
          <select value={f.status} onChange={(e) => go({ tx_status: e.target.value })} aria-label="Статус операции"><option value="">все статусы</option>{Object.entries(TX_STATUS).map(([k, [t]]) => <option key={k} value={k}>{t}</option>)}</select>
          <select value={f.origin} onChange={(e) => go({ origin: e.target.value })} aria-label="Источник записи"><option value="">все источники</option><option value="manual">введены вручную</option>
            <option value="legacy">старые отметки оплаты</option><option value="reversal">сторно</option></select>
          <label style={{ fontSize: 13 }}><input type="checkbox" checked={f.unallocated} onChange={(e) => go({ unallocated: e.target.checked ? "1" : null })} /> только неразнесённые</label>
          <label style={{ fontSize: 13 }}><input type="checkbox" checked={f.withdrawn} onChange={(e) => go({ withdrawn: e.target.checked ? "1" : null })} /> показать отозванные</label>
          {meta.can_edit && <button className="btn secondary" onClick={() => { setImporting((x) => !x); setCreating(false); }}>{importing ? "Закрыть импорт" : "Импорт выписки"}</button>}
          {meta.can_edit && <button className="btn add" onClick={() => { setCreating((x) => !x); setImporting(false); }}>Операция</button>}
        </div>
      </div>
      {importing && <ImportForm accounts={accounts} onDone={() => { onChanged(); }} />}
      {creating && <TxForm accounts={accounts} onDone={(id) => { setCreating(false); onChanged(); go({ tx: id }); }} />}
      {err && <div className="alert error">{err}</div>}
      {!data ? <div className="muted">Загружаю…</div> : (
        <div style={{ overflowX: "auto" }}><table style={{ minWidth: 900 }}>
          <thead><tr><th>Дата</th><th>Счёт</th><th>Направление</th><th>Контрагент / назначение</th><th>Банковский №</th><th className="num">Сумма</th><th className="num">Не разнесено</th><th>Статус</th><th></th></tr></thead>
          <tbody>{data.items.map((t) => (
            <Fragment key={t.id}>
              <tr style={{ cursor: "pointer", ...(t.status === "reversed" ? { color: "var(--muted)" } : {}) }} onClick={() => go({ tx: open === t.id ? null : t.id })}>
                <td style={{ whiteSpace: "nowrap" }}>{dt(t.booked_at)}</td><td>{t.account_title || <span className="muted">без счёта</span>}</td>
                <td>{t.origin_type === "reversal" ? <span className="badge">сторно</span> : t.direction === "in" ? <span className="badge ok">поступление</span> : <span className="badge bad">списание</span>}</td>
                <td>{t.counterparty_name}<div className="muted" style={{ fontSize: 12 }}>{t.purpose}</div>{t.origin_title && <span className="badge info" style={{ fontSize: 11 }}>{t.origin_title}</span>}</td><td>{t.external_ref || "—"}</td>
                <td className="num"><b>{t.direction === "out" ? "−" : "+"}{fmtMoney(t.amount, t.currency)}</b></td>
                <td className="num">{Number(t.unallocated) > 0 ? <b>{fmtMoney(t.unallocated)}</b> : "—"}</td>
                <td>{t.withdrawn ? <span className="badge" title={t.withdrawn_reason}>отозвана</span> : <span className={`badge ${TX_STATUS[t.status][1]}`}>{TX_STATUS[t.status][0]}</span>}</td>
                <td><button className="btn ghost" style={{ padding: "2px 10px" }} aria-expanded={open === t.id}>{open === t.id ? "Свернуть" : "Открыть"}</button></td>
              </tr>
              {open === t.id && <tr><td colSpan={9} style={{ background: "#fcfcfc", padding: 12 }}><TxCard id={t.id} canEdit={meta.can_edit} stamp={stamp} onChanged={onChanged} /></td></tr>}
            </Fragment>))}
            {data.items.length === 0 && <tr><td colSpan={9} className="muted" style={{ padding: 16 }}>{f.account_id || f.status || f.unallocated || f.origin ? "По выбранным фильтрам операций нет." : "Операций пока нет."}</td></tr>}
          </tbody>
        </table></div>)}
    </div>
  );
}

function TxForm({ accounts, onDone }: { accounts: Account[]; onDone: (id: string) => void }) {
  const [v, setV] = useState({ account_id: accounts[0]?.id || "", direction: "in", amount: "", booked_at: localNow(), counterparty_name: "", purpose: "", external_ref: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const key = useRef(newKey());
  const acc = accounts.find((a) => a.id === v.account_id);
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      const t = await api.postIdem<Tx>("/api/finance/transactions", { ...v, account_id: v.account_id || null, currency: acc?.currency || "RUB", booked_at: toIso(v.booked_at) }, key.current);
      key.current = newKey(); onDone(t.id);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [k]: k === "amount" ? e.target.value.replace(",", ".") : e.target.value });
  return (
    <div className="card" style={{ background: "#fcfcfc" }}>
      <b>Новая банковская операция</b> <span className="muted">— сохраняется черновиком; проведение — отдельным действием</span>
      {err && <div className="alert error">{err}</div>}
      {accounts.length === 0 && <div className="alert warn">Нет финансовых счетов: операция без счёта сохранится со статусом «требует сверки» и не попадёт в остатки.</div>}
      <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
        <label className="f">Счёт<select value={v.account_id} onChange={set("account_id")}><option value="">без счёта (требует сверки)</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.title} · {a.currency}</option>)}</select></label>
        <label className="f">Направление<select value={v.direction} onChange={set("direction")}><option value="in">поступление</option><option value="out">списание</option></select></label>
        <label className="f">Сумма, {acc?.currency || "RUB"}<input inputMode="decimal" value={v.amount} onChange={set("amount")} /></label>
        <label className="f">Дата и время<input type="datetime-local" value={v.booked_at} onChange={set("booked_at")} /></label>
      </div>
      {acc?.last_snapshot && v.booked_at && new Date(v.booked_at) <= new Date(acc.last_snapshot.as_of) && (
        <div className="alert warn" style={{ fontSize: 13 }}>Дата операции не позже подтверждённого остатка ({dt(acc.last_snapshot.as_of)}): считается, что она уже вошла в этот остаток, и повторно к нему не прибавится.</div>)}
      <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
        <label className="f" style={{ flex: "1 1 220px" }}>Контрагент<input value={v.counterparty_name} onChange={set("counterparty_name")} /></label>
        <label className="f" style={{ flex: "2 1 280px" }}>Назначение платежа<input value={v.purpose} onChange={set("purpose")} /></label>
        <label className="f">Банковский № документа<input value={v.external_ref} onChange={set("external_ref")} /></label>
        <button className="btn" disabled={busy || !Number(v.amount)} onClick={submit}>{busy ? "Сохраняю…" : "Сохранить черновик"}</button>
      </div>
    </div>
  );
}

function TxCard({ id, canEdit, stamp, onChanged }: { id: string; canEdit: boolean; stamp: number; onChanged: () => void }) {
  const [t, setT] = useState<Tx | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [alloc, setAlloc] = useState(false);
  const postKey = useRef(newKey());
  const revKey = useRef(newKey());
  const load = useCallback(() => api.get<Tx>(`/api/finance/transactions/${id}`).then(setT).catch((e) => setErr(errText(e))), [id]);
  useEffect(() => { load(); }, [load, stamp]);
  if (err && !t) return <div className="alert error">{err}</div>;
  if (!t) return <div className="muted">Загружаю…</div>;
  const changed = () => { load(); onChanged(); };
  const run = async (f: () => Promise<unknown>) => { setBusy(true); setErr(""); try { await f(); changed(); } catch (e) { setErr(errText(e)); } finally { setBusy(false); } };
  const reverse = () => {
    const reason = window.prompt("Причина сторно (обязательно). Исходная операция не удаляется — создаётся обратная проведённая операция, разнесения сторнируются.") || "";
    if (!reason.trim()) return;
    run(() => api.postIdem(`/api/finance/transactions/${t.id}/reverse`, { reason }, revKey.current));
  };
  const editable = t.origin_type !== "reversal" && t.status !== "reversed" && !t.withdrawn;
  const pending = (t.status === "draft" || t.status === "needs_reconciliation") && !t.withdrawn;
  const withdraw = () => {
    const reason = window.prompt("Почему запись отзывается (дубль операции из выписки, не платёж, ошибка ввода)? Запись не удаляется.") || "";
    if (!reason.trim()) return;
    run(() => api.post(`/api/finance/transactions/${t.id}/withdraw`, { reason }));
  };
  return (
    <div className="grid cols-2">
      <div>
        {err && <div className="alert error">{err}</div>}
        <table style={{ fontSize: 13 }}><tbody>
          <tr><td className="muted">Сумма</td><td className="num"><b>{fmtMoney(t.amount, t.currency)}</b></td></tr>
          <tr><td className="muted">Разнесено / свободно</td><td className="num">{fmtMoney(t.allocated)} / <b>{fmtMoney(t.unallocated)}</b></td></tr>
          <tr><td className="muted">Счёт</td><td>{t.account_title || "не указан — операция требует сверки и не входит в остаток"}</td></tr>
          <tr><td className="muted">Статус</td><td>{t.withdrawn ? <><span className="badge">отозвана</span> <span className="muted">{t.withdrawn_reason}</span></> : <span className={`badge ${TX_STATUS[t.status][1]}`}>{TX_STATUS[t.status][0]}</span>}{t.reversal_of_id && <span className="muted"> · сторно операции</span>}</td></tr>
          {t.origin_title && <tr><td className="muted">Источник</td><td>{t.origin_title} — {t.purpose}. Это не банковский факт: выберите счёт и проведите запись или отзовите её, если платёж уже есть в выписке.</td></tr>}
          {t.note && <tr><td className="muted">Примечание</td><td>{t.note}</td></tr>}
        </tbody></table>
        {canEdit && (
          <div className="row" style={{ marginTop: 8 }}>
            {pending && <button className="btn" disabled={busy || !t.account_id} title={t.account_id ? "" : "Без счёта провести нельзя"}
              onClick={() => run(() => api.postIdem(`/api/finance/transactions/${t.id}/post`, {}, postKey.current))}>Провести</button>}
            {t.status === "posted" && t.origin_type !== "reversal" && <button className="btn ghost" disabled={busy} onClick={reverse}>Сторнировать</button>}
            {editable && Number(t.unallocated) > 0 && <button className="btn secondary" onClick={() => setAlloc((x) => !x)}>{alloc ? "Отмена" : "Разнести на обязательства"}</button>}
            {pending && <button className="btn ghost" disabled={busy} onClick={withdraw}>Отозвать</button>}
          </div>)}
        {canEdit && pending && <AssignAccount tx={t} onDone={changed} />}
        {alloc && <AllocateFromTx tx={t} onDone={() => { setAlloc(false); changed(); }} />}
      </div>
      <div>
        <b>Разнесения</b>
        <AllocTable rows={t.allocations || []} canEdit={canEdit} onChanged={changed} side="transaction" txStatus={t.status} />
        {(t.hidden_allocations || 0) > 0 && <div className="muted" style={{ fontSize: 12 }}>и ещё {t.hidden_allocations} — по обязательствам, закрытым для вашей роли</div>}
      </div>
    </div>
  );
}

/** Разнесение со стороны операции: несколько строк обязательств с суммами; итог и превышение видны до сохранения. */
function AllocateFromTx({ tx, onDone }: { tx: Tx; onDone: () => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Obligation[] | null>(null);
  const [lines, setLines] = useState<{ ob: Obligation; amount: string; rate: string; on: string; source: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const key = useRef(newKey());
  useEffect(() => {
    const p = new URLSearchParams({ direction: tx.direction, status: "open", limit: "50" });
    if (q.trim()) p.set("search", q.trim());
    const h = setTimeout(() => api.get<List<Obligation>>(`/api/finance/obligations?${p}`).then((r) => setFound(r.items.filter((o) => o.sync_status === "ok"))).catch((e) => setErr(errText(e))), 250);
    return () => clearTimeout(h);
  }, [q, tx.direction]);
  const total = lines.reduce((s, l) => s + (Number(l.amount) || 0), 0);
  const free = Number(tx.unallocated);
  const add = (o: Obligation) => {
    if (lines.some((l) => l.ob.id === o.id)) return;
    const left = Math.max(0, free - total);
    setLines([...lines, { ob: o, amount: (o.currency === tx.currency ? Math.min(left, Number(o.remaining)) : left).toFixed(2), rate: "", on: "", source: "" }]);
  };
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      await api.postIdem(`/api/finance/transactions/${tx.id}/allocations`, { status: "provisional", items: lines.map((l) => l.ob.currency === tx.currency
        ? { obligation_id: l.ob.id, amount_tx: l.amount }
        : { obligation_id: l.ob.id, amount_tx: l.amount, amount_obligation: (Number(l.amount) * Number(l.rate)).toFixed(2), fx_rate: l.rate, fx_on: l.on, fx_source: l.source }) }, key.current);
      key.current = newKey(); onDone();
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const bad = lines.some((l) => !Number(l.amount) || (l.ob.currency !== tx.currency && (!l.rate || !l.on || !l.source)) || (l.ob.currency === tx.currency && Number(l.amount) > Number(l.ob.remaining)));
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <b>Разнесение операции</b> <span className="muted">свободно {fmtMoney(tx.unallocated, tx.currency)}{" · сохраняется временным, подтверждение — отдельной кнопкой"}{tx.status !== "posted" && " после проведения"}</span>
      {err && <div className="alert error">{err}</div>}
      <input placeholder="🔍 найти строку: договор, сделка, контрагент" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%", marginTop: 8 }} />
      <div style={{ maxHeight: 180, overflowY: "auto", marginTop: 6, border: "1px solid var(--line)", borderRadius: 6 }}>
        {!found ? <div className="muted" style={{ padding: 8 }}>Ищу…</div> : found.length === 0 ? <div className="muted" style={{ padding: 8 }}>Неоплаченных строк того же направления не найдено</div>
          : found.map((o) => <div key={o.id} className="row" style={{ padding: "4px 8px", borderBottom: "1px solid var(--line)", fontSize: 13 }}>
            <span style={{ flex: 1 }}>{o.contract?.number || o.source.type_title} · {o.counterparty.name} · {o.title.replace(/^[^:]+:\s*/, "")} · срок {fmtDate(o.legal_due_on || o.forecast_on)}</span>
            <b>{fmtMoney(o.remaining, o.currency)}</b><button className="btn ghost" style={{ padding: "0 8px" }} disabled={lines.some((l) => l.ob.id === o.id)} onClick={() => add(o)}>Добавить</button></div>)}
      </div>
      {lines.length > 0 && <table style={{ marginTop: 8, fontSize: 13 }}>
        <thead><tr><th>Строка</th><th className="num">Остаток строки</th><th>Сумма из операции, {tx.currency}</th><th>Курс (если валюта другая)</th><th></th></tr></thead>
        <tbody>{lines.map((l, i) => {
          const upd = (patch: Partial<typeof l>) => setLines(lines.map((x, j) => (j === i ? { ...x, ...patch } : x)));
          return <tr key={l.ob.id}><td>{l.ob.title}</td><td className="num">{fmtMoney(l.ob.remaining, l.ob.currency)}</td>
            <td><input inputMode="decimal" value={l.amount} onChange={(e) => upd({ amount: e.target.value.replace(",", ".") })} style={{ width: 130 }} /></td>
            <td>{l.ob.currency === tx.currency ? <span className="muted">—</span> : <div className="row">
              <input placeholder={`${l.ob.currency} за 1 ${tx.currency}`} value={l.rate} onChange={(e) => upd({ rate: e.target.value.replace(",", ".") })} style={{ width: 110 }} />
              <input type="date" value={l.on} onChange={(e) => upd({ on: e.target.value })} /><input placeholder="источник" value={l.source} onChange={(e) => upd({ source: e.target.value })} style={{ width: 100 }} /></div>}</td>
            <td><button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => setLines(lines.filter((_, j) => j !== i))} aria-label="Убрать строку">×</button></td></tr>;
        })}
          <tr className="total"><td colSpan={2}>Итого разносится</td><td className="num" colSpan={3} style={{ textAlign: "left", color: total > free ? "var(--bad)" : undefined }}>
            {fmtMoney(total.toFixed(2), tx.currency)}{total > free ? ` — больше свободного остатка (${fmtMoney(tx.unallocated)})` : ` · останется ${fmtMoney((free - total).toFixed(2))}`}</td></tr>
        </tbody></table>}
      <button className="btn" style={{ marginTop: 8 }} disabled={busy || !lines.length || bad || total > free} onClick={submit}>{busy ? "Сохраняю…" : "Разнести"}</button>
    </div>
  );
}

/** Сверка записи без счёта (старая отметка оплаты): выбрать реальный счёт и банковский номер; проведение — отдельной кнопкой. */
function AssignAccount({ tx, onDone }: { tx: Tx; onDone: () => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [acc, setAcc] = useState(tx.account_id || "");
  const [ref, setRef] = useState(tx.external_ref || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ items: Account[] }>("/api/finance/accounts").then((r) => setAccounts(r.items.filter((a) => a.currency === tx.currency))).catch((e) => setErr(errText(e))); }, [tx.currency]);
  const changed = acc !== (tx.account_id || "") || ref !== (tx.external_ref || "");
  const submit = async () => {
    setBusy(true); setErr("");
    try { await api.post(`/api/finance/transactions/${tx.id}/assign-account`, { account_id: acc, external_ref: ref }); onDone(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ marginTop: 10, background: "#fcfcfc" }}>
      <b>Сверка: счёт и банковский документ</b>
      {err && <div className="alert error">{err}</div>}
      {!accounts ? <div className="muted">Загружаю счета…</div> : accounts.length === 0 ? <div className="muted">Нет действующих счетов в {tx.currency}. Создайте счёт в блоке «Счета и остатки».</div> : (
        <div className="row" style={{ marginTop: 6, alignItems: "flex-end" }}>
          <label className="f">Счёт<select value={acc} onChange={(e) => setAcc(e.target.value)}><option value="">— выберите —</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.title} · {a.bank_name} {a.account_masked}</option>)}</select></label>
          <label className="f">Банковский № документа<input value={ref} onChange={(e) => setRef(e.target.value)} /></label>
          <button className="btn secondary" disabled={busy || !acc || !changed} onClick={submit}>{busy ? "Сохраняю…" : "Сохранить"}</button>
        </div>)}
    </div>
  );
}

// ------------------------------------------------------------------ прогноз ликвидности

type LiqPoint = { on: string; inflow: string; outflow: string; balance: string | null; late_in_point: boolean; count: number };
type Bucket = { in: string; out: string; count: number };
type LiqCur = { currency: string; opening_balance: string | null; balance_status: "confirmed" | "partial" | "unknown";
  accounts: { id: string; title: string; balance: string | null; balance_status: string; snapshot_as_of: string | null; stale: boolean }[];
  points: LiqPoint[]; minimum: string | null; minimum_on: string | null; threshold: string; cash_gaps: { from: string; to: string; min: string; min_on: string }[];
  undated: Bucket; beyond_horizon: Bucket; needs_reconciliation: Bucket; unallocated: Bucket; fx: { rate: string; on: string; source: string } | null };
type Liq = { as_of: string; group_by: "day" | "week"; horizon_days: number; currencies: LiqCur[]; formula: string;
  rub: { included: string[]; excluded: { currency: string; reason: string }[]; fx: { currency: string; rate: string; on: string; source: string }[]; points: { on: string; balance: string }[]; note: string } };
type LiqExpl = { on: string; items: { id: string; title: string; direction: "in" | "out"; amount: string; on: string; late: boolean; nature: string; counterparty: string }[] };

function Forecast({ sp, go, stamp }: { sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number }) {
  const groupBy = sp.get("group") === "day" ? "day" : "week";
  const horizon = sp.get("horizon") || "90";
  const [data, setData] = useState<Liq | null>(null);
  const [err, setErr] = useState("");
  const [threshold, setThreshold] = useState(sp.get("min") || "0");
  const [expl, setExpl] = useState<LiqExpl | null>(null);
  useEffect(() => {
    setErr(""); setExpl(null);
    api.get<Liq>(`/api/finance/liquidity?group_by=${groupBy}&horizon_days=${horizon}&threshold=${encodeURIComponent(sp.get("min") || "0")}`).then(setData).catch((e) => setErr(errText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupBy, horizon, sp.get("min"), stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!data) return <div className="card muted">Загружаю прогноз…</div>;
  const curList = data.currencies.map((c) => c.currency);
  const curCode = sp.get("ccy") && curList.includes(sp.get("ccy")!) ? sp.get("ccy")! : curList.includes("RUB") ? "RUB" : curList[0];
  const cur = data.currencies.find((c) => c.currency === curCode);
  const explain = (on: string) => api.get<LiqExpl>(`/api/finance/liquidity/explanation?currency=${curCode}&on=${on}&group_by=${groupBy}&horizon_days=${horizon}`).then(setExpl).catch((e) => setErr(errText(e)));
  const outside = (b: Bucket | undefined, label: string) => b && b.count > 0 ? <tr><td>{label}</td><td className="num">{Number(b.in) ? `+${fmtMoney(b.in)}` : "—"}</td><td className="num">{Number(b.out) ? `−${fmtMoney(b.out)}` : "—"}</td><td className="num">{b.count}</td></tr> : null;
  return (
    <div className="card">
      <div className="section-head"><h2>Прогноз остатков</h2>
        <div className="row">
          {curList.length > 1 && <div className="tabs" style={{ margin: 0, border: 0 }}>{curList.map((c) => <button key={c} className={c === curCode ? "active" : ""} onClick={() => go({ ccy: c })}>{c}</button>)}</div>}
          <select value={groupBy} onChange={(e) => go({ group: e.target.value })} aria-label="Шаг"><option value="week">по неделям</option><option value="day">по дням</option></select>
          <select value={horizon} onChange={(e) => go({ horizon: e.target.value })} aria-label="Горизонт"><option value="30">30 дней</option><option value="90">90 дней</option><option value="180">180 дней</option><option value="365">год</option></select>
          <form onSubmit={(e) => { e.preventDefault(); go({ min: threshold.replace(",", ".") || null }); }} className="row" style={{ gap: 4 }}>
            <label className="muted" style={{ fontSize: 12 }}>порог разрыва</label><input value={threshold} onChange={(e) => setThreshold(e.target.value)} style={{ width: 110 }} inputMode="decimal" onBlur={() => go({ min: threshold.replace(",", ".") || null })} />
          </form>
        </div>
      </div>
      {!cur ? <div className="muted">Нет счетов и обязательств для прогноза.</div> : <>
        {cur.balance_status !== "confirmed" && <div className="alert warn" style={{ fontSize: 13 }}>
          {cur.balance_status === "unknown" ? `Входящий остаток в ${cur.currency} не подтверждён — кривая не строится от нуля. Подтвердите остаток счёта в блоке «Счета и остатки».`
            : `Остаток подтверждён не по всем счетам в ${cur.currency}: кривая — по подтверждённым счетам, без ${cur.accounts.filter((a) => a.balance == null).map((a) => a.title).join(", ")}.`}</div>}
        {cur.accounts.some((a) => a.stale) && <div className="alert warn" style={{ fontSize: 13 }}>Подтверждённый остаток старше 7 дней: {cur.accounts.filter((a) => a.stale).map((a) => a.title).join(", ")}.</div>}
        <div className="row" style={{ gap: 10, alignItems: "stretch", marginBottom: 10 }}>
          <Kpi label={`входящий остаток, ${cur.currency}`}>{cur.opening_balance == null ? <span className="muted" style={{ fontSize: 14 }}>не подтверждён</span> : fmtMoney(cur.opening_balance, cur.currency)}</Kpi>
          <Kpi label={cur.minimum_on ? `минимальный остаток · ${fmtDate(cur.minimum_on)}` : "минимальный остаток"} bad={cur.minimum != null && Number(cur.minimum) < Number(cur.threshold)}>{cur.minimum == null ? <span className="muted" style={{ fontSize: 14 }}>нет данных</span> : fmtMoney(cur.minimum, cur.currency)}</Kpi>
          <Kpi label="кассовые разрывы" bad={cur.cash_gaps.length > 0}>{cur.cash_gaps.length === 0 ? (cur.minimum == null ? <span className="muted" style={{ fontSize: 14 }}>нет данных</span> : "нет") :
            cur.cash_gaps.map((g, i) => <div key={i} style={{ fontSize: 13 }}>{fmtDate(g.from)} — {fmtDate(g.to)}: <b>{fmtMoney(g.min, cur.currency)}</b></div>)}</Kpi>
        </div>
        <LiqChart cur={cur} onPick={explain} />
        <div style={{ overflowX: "auto", marginTop: 10 }}><table style={{ fontSize: 13 }}>
          <thead><tr><th>{groupBy === "week" ? "Неделя с" : "Дата"}</th><th className="num">Поступления</th><th className="num">Списания</th><th className="num">Остаток на конец</th><th></th></tr></thead>
          <tbody>{cur.points.filter((p, i) => i === 0 || Number(p.inflow) || Number(p.outflow)).map((p) => (
            <tr key={p.on} style={p.balance != null && Number(p.balance) < Number(cur.threshold) ? { background: "var(--late-bg)" } : undefined}>
              <td style={{ whiteSpace: "nowrap" }}>{fmtDate(p.on)}{p.late_in_point && <span className="badge bad" style={{ marginLeft: 4, fontSize: 10 }} title="Срок уже прошёл — ожидается сейчас">просрочено</span>}</td>
              <td className="num">{Number(p.inflow) ? `+${fmtMoney(p.inflow)}` : "—"}</td><td className="num">{Number(p.outflow) ? `−${fmtMoney(p.outflow)}` : "—"}</td>
              <td className="num"><b>{p.balance == null ? "—" : fmtMoney(p.balance)}</b>{p.balance != null && Number(p.balance) < 0 && <span className="muted" style={{ fontSize: 11 }}> минус</span>}</td>
              <td>{p.count > 0 && <button className="btn ghost" style={{ padding: "0 8px", fontSize: 12 }} onClick={() => explain(p.on)}>состав ({p.count})</button>}</td></tr>))}</tbody>
        </table></div>
        {expl && <div className="card" style={{ marginTop: 10, background: "#fcfcfc", fontSize: 13 }}>
          <div className="row" style={{ justifyContent: "space-between" }}><b>Состав точки {fmtDate(expl.on)}</b><button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => setExpl(null)} aria-label="Закрыть">×</button></div>
          <div className="muted">{data.formula}</div>
          <table style={{ marginTop: 6 }}><tbody>{expl.items.map((i) => <tr key={i.id}><td>{fmtDate(i.on)}{i.late && <span className="muted"> (срок прошёл)</span>}</td><td>{i.title}{i.nature === "forecast" && <span className="badge info" style={{ marginLeft: 4, fontSize: 10 }}>прогноз</span>}</td><td>{i.counterparty}</td>
            <td className="num">{i.direction === "in" ? "+" : "−"}{fmtMoney(i.amount, cur.currency)}</td></tr>)}</tbody></table>
        </div>}
        <details style={{ marginTop: 10 }} open={[cur.undated, cur.beyond_horizon, cur.needs_reconciliation, cur.unallocated].some((b) => b.count > 0)}>
          <summary><b>Не вошло в кривую</b></summary>
          <table style={{ fontSize: 13, marginTop: 6 }}><thead><tr><th>Что</th><th className="num">Поступления</th><th className="num">Списания</th><th className="num">Строк</th></tr></thead><tbody>
            {outside(cur.undated, "Без согласованной даты")}{outside(cur.beyond_horizon, "Позже горизонта")}
            {outside(cur.needs_reconciliation, "Записи «требует сверки» — не банковский факт")}{outside(cur.unallocated, "Проведено, но не разнесено (уже в остатке)")}
            {[cur.undated, cur.beyond_horizon, cur.needs_reconciliation, cur.unallocated].every((b) => b.count === 0) && <tr><td colSpan={4} className="muted">Ничего</td></tr>}
          </tbody></table>
        </details>
        <details style={{ marginTop: 6 }}>
          <summary><b>RUB-эквивалент по всем валютам</b></summary>
          <div className="muted" style={{ fontSize: 12, margin: "6px 0" }}>{data.rub.note} Курсы: {data.rub.fx.map((f) => `${f.currency} ${f.rate.replace(".", ",")} (${f.source}, ${fmtDate(f.on)})`).join("; ") || "—"}.
            {data.rub.excluded.length > 0 && <> Не включено: {data.rub.excluded.map((x) => `${x.currency} — ${x.reason}`).join("; ")}.</>}</div>
          {data.rub.points.length > 0 && <table style={{ fontSize: 13 }}><thead><tr><th>Дата</th><th className="num">Остаток, ≈ RUB</th></tr></thead>
            <tbody>{data.rub.points.filter((_, i) => i % (groupBy === "day" ? 7 : 1) === 0).map((p) => <tr key={p.on}><td>{fmtDate(p.on)}</td><td className="num">≈ {fmtMoney(p.balance, "₽")}</td></tr>)}</tbody></table>}
        </details>
      </>}
    </div>
  );
}

/** SVG-кривая остатка: сплошная точка — подтверждённый входящий остаток, пунктир — прогноз; порог подписан; столбики — движения. */
function LiqChart({ cur, onPick }: { cur: LiqCur; onPick: (on: string) => void }) {
  const W = 900, Hh = 220, pad = { l: 70, r: 12, t: 12, b: 24 };
  const pts = cur.points;
  if (cur.opening_balance == null || pts.length < 2) return <div className="muted" style={{ padding: 20, textAlign: "center", border: "1px dashed var(--line)", borderRadius: 8 }}>
    {cur.opening_balance == null ? "График не строится: нет подтверждённого входящего остатка." : "Недостаточно точек для графика."}</div>;
  const vals = pts.map((p) => Number(p.balance));
  const thr = Number(cur.threshold);
  const lo = Math.min(thr, ...vals, Number(cur.opening_balance)), hi = Math.max(thr, ...vals, Number(cur.opening_balance));
  const span = hi - lo || 1;
  const x = (i: number) => pad.l + (i * (W - pad.l - pad.r)) / (pts.length - 1);
  const y = (v: number) => pad.t + ((hi - v) * (Hh - pad.t - pad.b)) / span;
  const path = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const short = (v: number) => new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 }).format(v);
  return (
    <>
    <svg viewBox={`0 0 ${W} ${Hh}`} style={{ width: "100%", height: "auto", display: "block" }} role="img" aria-label={`Прогноз остатка в ${cur.currency}`}>
      {[lo, (lo + hi) / 2, hi].map((v, i) => <g key={i}><line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="var(--line)" /><text x={pad.l - 6} y={y(v) + 4} fontSize="11" textAnchor="end" fill="var(--muted)">{short(v)}</text></g>)}
      <line x1={pad.l} x2={W - pad.r} y1={y(thr)} y2={y(thr)} stroke="var(--bad)" strokeDasharray="2 3" />
      <text x={W - pad.r} y={y(thr) - 4} fontSize="11" textAnchor="end" fill="var(--bad)">порог {short(thr)} {cur.currency}</text>
      <path d={path} fill="none" stroke="var(--ink)" strokeWidth="2" strokeDasharray="6 4" />
      <circle cx={x(0)} cy={y(Number(cur.opening_balance))} r="4.5" fill="var(--ink)"><title>Подтверждённый входящий остаток: {fmtMoney(cur.opening_balance, cur.currency)}</title></circle>
      {pts.map((p, i) => (Number(p.inflow) || Number(p.outflow)) ? <circle key={p.on} cx={x(i)} cy={y(vals[i])} r="4" fill={vals[i] < thr ? "var(--bad)" : "#fff"} stroke="var(--ink)" style={{ cursor: "pointer" }} onClick={() => onPick(p.on)}>
        <title>{fmtDate(p.on)}: +{fmtMoney(p.inflow)} / −{fmtMoney(p.outflow)} · остаток {fmtMoney(p.balance, cur.currency)}</title></circle> : null)}
      {[0, Math.floor((pts.length - 1) / 2), pts.length - 1].map((i) => <text key={i} x={x(i)} y={Hh - 6} fontSize="11" textAnchor={i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle"} fill="var(--muted)">{fmtDate(pts[i].on)}</text>)}
    </svg>
    <div className="row muted" style={{ fontSize: 12, gap: 16, marginTop: 4 }}>
      <span><span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 5, background: "var(--ink)", marginRight: 4 }} />подтверждённый входящий остаток</span>
      <span><span style={{ display: "inline-block", width: 24, borderTop: "2px dashed var(--ink)", verticalAlign: "middle", marginRight: 4 }} />прогноз остатка</span>
      <span><span style={{ display: "inline-block", width: 24, borderTop: "1px dotted var(--bad)", verticalAlign: "middle", marginRight: 4 }} />порог кассового разрыва</span>
      <span>кружок — точка с движением, клик — состав</span></div>
    </>
  );
}

// ------------------------------------------------------------------ импорт выписки

type ImpRow = { n: number; status: "valid" | "duplicate" | "error"; booked_on: string; direction: string; amount: string; counterparty: string; purpose: string; external_ref: string; errors: string[] };
type Imp = { id: string; file_name: string; status: string; rows: ImpRow[]; rows_valid: number; rows_duplicates: number; rows_errors: number; totals: { in: string; out: string };
  result: { created?: number; skipped_duplicates?: number; errors?: number; same_file_confirmed_at?: string | null }; mapping: { header_row: number; columns: Record<string, number>; headers: string[] }; fields: Record<string, string> };
const IMP_STATUS: Record<ImpRow["status"], [string, string]> = { valid: ["к загрузке", "ok"], duplicate: ["уже есть", ""], error: ["ошибка", "bad"] };

function ImportForm({ accounts, onDone }: { accounts: Account[]; onDone: () => void }) {
  const [acc, setAcc] = useState(accounts[0]?.id || "");
  const [file, setFile] = useState<File | null>(null);
  const [imp, setImp] = useState<Imp | null>(null);
  const [pick, setPick] = useState<Set<number>>(new Set());
  const [mapping, setMapping] = useState<{ headers: string[]; header_row: number; columns: Record<string, number> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const key = useRef(newKey());
  const doPreview = async (m?: typeof mapping) => {
    if (!file || !acc) return;
    setBusy(true); setErr(""); setImp(null);
    try {
      const fd = new FormData();
      fd.append("file", file); fd.append("account_id", acc);
      if (m) fd.append("mapping", JSON.stringify({ header_row: m.header_row, columns: m.columns }));
      const res = await fetch("/api/finance/imports/preview", { method: "POST", body: fd, credentials: "include" });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const d = data?.detail;
        if (d && typeof d === "object" && d.headers) setMapping({ headers: d.headers, header_row: 0, columns: d.detected || {} });
        throw new Error(typeof d === "string" ? d : d?.message || `HTTP ${res.status}`);
      }
      setImp(data); setMapping(null); key.current = newKey();
      setPick(new Set((data as Imp).rows.filter((r) => r.status === "valid").map((r) => r.n)));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!imp) return;
    setBusy(true); setErr("");
    try { setImp(await api.postIdem<Imp>(`/api/finance/imports/${imp.id}/confirm`, { rows: [...pick] }, key.current)); onDone(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ background: "#fcfcfc" }}>
      <b>Импорт банковской выписки</b> <span className="muted">— CSV или XLSX, одна строка — одна операция. Сначала предпросмотр: ничего не проводится до подтверждения.</span>
      {err && <div className="alert error">{err}</div>}
      {accounts.length === 0 ? <div className="muted">Сначала создайте финансовый счёт.</div> : (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
          <label className="f">Счёт<select value={acc} onChange={(e) => { setAcc(e.target.value); setImp(null); }}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.title} · {a.currency}</option>)}</select></label>
          <label className="f">Файл выписки<input type="file" accept=".csv,.xlsx,.txt" onChange={(e) => { setFile(e.target.files?.[0] || null); setImp(null); setMapping(null); }} /></label>
          <button className="btn secondary" disabled={!file || busy} onClick={() => doPreview()}>{busy && !imp ? "Разбираю…" : "Предпросмотр"}</button>
        </div>)}
      {mapping && <div className="card" style={{ marginTop: 8 }}>
        <b>Сопоставление колонок</b> <span className="muted">— заголовки не распознаны автоматически</span>
        <div className="row" style={{ marginTop: 6, alignItems: "flex-end" }}>
          <label className="f">Строка заголовков (с 1)<input type="number" min={1} value={mapping.header_row + 1} style={{ width: 80 }} onChange={(e) => setMapping({ ...mapping, header_row: Math.max(0, Number(e.target.value) - 1) })} /></label>
          {["date", "amount", "inflow", "outflow", "counterparty", "purpose", "external_ref", "currency"].map((f) => (
            <label key={f} className="f">{({ date: "Дата", amount: "Сумма ±", inflow: "Поступление", outflow: "Списание", counterparty: "Контрагент", purpose: "Назначение", external_ref: "Номер документа", currency: "Валюта" } as Record<string, string>)[f]}
              <select value={mapping.columns[f] ?? ""} onChange={(e) => { const c = { ...mapping.columns }; if (e.target.value === "") delete c[f]; else c[f] = Number(e.target.value); setMapping({ ...mapping, columns: c }); }}>
                <option value="">—</option>{mapping.headers.map((h, i) => <option key={i} value={i}>{h || `колонка ${i + 1}`}</option>)}</select></label>))}
          <button className="btn secondary" disabled={busy} onClick={() => doPreview(mapping)}>Разобрать</button>
        </div>
      </div>}
      {imp && <>
        {imp.result.same_file_confirmed_at && imp.status === "preview" && <div className="alert warn" style={{ fontSize: 13 }}>Этот файл уже загружался по счёту {dt(imp.result.same_file_confirmed_at)} — совпадающие строки отмечены «уже есть» и не загрузятся.</div>}
        {imp.status === "confirmed" && <div className="alert ok">Загружено операций: {imp.result.created}; пропущено как дубли: {imp.result.skipped_duplicates}; с ошибками: {imp.result.errors}. Операции проведены — разнесите их на обязательства.</div>}
        <div className="row" style={{ marginTop: 8, fontSize: 13 }}>
          <span className="badge ok">к загрузке {imp.rows_valid}</span><span className="badge">уже есть {imp.rows_duplicates}</span><span className="badge bad">ошибки {imp.rows_errors}</span>
          <span className="muted">поступления +{fmtMoney(imp.totals.in)} · списания −{fmtMoney(imp.totals.out)}</span>
        </div>
        <div style={{ maxHeight: 360, overflowY: "auto", marginTop: 6 }}><table style={{ fontSize: 13 }}>
          <thead><tr><th></th><th>Стр.</th><th>Дата</th><th>Направление</th><th className="num">Сумма</th><th>Контрагент / назначение</th><th>№</th><th>Статус</th></tr></thead>
          <tbody>{imp.rows.map((r) => <tr key={r.n} style={r.status !== "valid" ? { color: "var(--muted)" } : undefined}>
            <td>{r.status === "valid" && imp.status === "preview" && <input type="checkbox" checked={pick.has(r.n)} aria-label={`Загрузить строку ${r.n}`} onChange={(e) => { const s = new Set(pick); if (e.target.checked) s.add(r.n); else s.delete(r.n); setPick(s); }} />}</td>
            <td>{r.n}</td><td>{r.booked_on ? fmtDate(r.booked_on) : "—"}</td><td>{r.direction === "in" ? "поступление" : r.direction === "out" ? "списание" : "—"}</td>
            <td className="num">{r.amount ? fmtMoney(r.amount) : "—"}</td><td>{r.counterparty}<div className="muted" style={{ fontSize: 11 }}>{r.purpose}</div></td><td>{r.external_ref || "—"}</td>
            <td><span className={`badge ${IMP_STATUS[r.status][1]}`}>{IMP_STATUS[r.status][0]}</span>{r.errors.length > 0 && <div style={{ fontSize: 11, color: "var(--bad)" }}>{r.errors.join("; ")}</div>}</td></tr>)}</tbody>
        </table></div>
        {imp.status === "preview" && <button className="btn" style={{ marginTop: 8 }} disabled={busy || pick.size === 0} onClick={confirm}>{busy ? "Загружаю…" : `Загрузить и провести (${pick.size})`}</button>}
      </>}
    </div>
  );
}
