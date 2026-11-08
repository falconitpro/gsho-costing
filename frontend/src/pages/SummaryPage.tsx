import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtRub } from "../api";
import type { Me } from "../types";

// Сводные экраны по всем сделкам: Отгрузки (логист), Деньги (ФД), Сервис, Закупки. Один компонент, вкладка по роли.

type Kind = "shipments" | "money" | "service" | "purchasing";
const TITLES: Record<Kind, string> = { shipments: "Отгрузки", money: "Деньги", service: "Сервис", purchasing: "Закупки" };
const load = <T,>(k: string, d: T): T => { try { return (JSON.parse(localStorage.getItem(k) || "") as T) ?? d; } catch { return d; } };

function Kpi({ v, l, bad }: { v: string | number; l: string; bad?: boolean }) {
  return <div style={{ flex: "1 1 130px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}>
    <div style={{ fontSize: 20, fontWeight: 700, color: bad && Number(v) > 0 ? "var(--bad)" : undefined }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{l}</div></div>;
}
const d = (x: string | null | undefined) => (x ? fmtDate(x) : "—");
const late = (n: number) => (n > 0 ? <span style={{ color: "var(--bad)", fontWeight: 600 }}> · просрочено {n} дн.</span> : null);

export default function SummaryPage({ me }: { me: Me }) {
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const fin = (me.perms?.finance_analytics ?? 0) >= 1;
  const kinds = ([
    has("logistician", "cd", "gd", "fd", "manager", "purchasing") && "shipments",
    fin && "money",
    has("service_head", "cd", "gd", "manager", "fd") && "service",
    has("purchasing", "cd", "gd", "service_head", "fd") && "purchasing",
  ].filter(Boolean)) as Kind[];
  const pref = load<Kind>("gsho.summaryKind", has("logistician") ? "shipments" : has("fd") ? "money" : has("service_head") ? "service" : has("purchasing") ? "purchasing" : kinds[0]);
  const urlKind = new URLSearchParams(window.location.search).get("kind") as Kind | null;   // с рабочего места
  const [kind, setKindState] = useState<Kind>(urlKind && kinds.includes(urlKind) ? urlKind : kinds.includes(pref) ? pref : kinds[0]);
  const setKind = (k: Kind) => { setKindState(k); try { localStorage.setItem("gsho.summaryKind", JSON.stringify(k)); } catch { /* нет хранилища */ } };
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setData(null); setErr(""); if (kind) api.get<Record<string, unknown>>(`/api/summary/${kind}`).then(setData).catch((e) => setErr((e as Error).message)); }, [kind]);
  if (!kinds.length) return <div className="card muted">Сводные экраны для вашей роли не предусмотрены.</div>;
  const t = (data?.totals || {}) as Record<string, number>;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ margin: 0 }}>Сводка · {TITLES[kind]}</h1>
        {kinds.length > 1 && <div className="tabs" style={{ margin: 0 }}>{kinds.map((k) => <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{TITLES[k]}</button>)}</div>}
      </div>
      {err && <div className="alert error">{err}</div>}
      {!data && !err && <div className="muted">Загружаю…</div>}
      {data && kind === "shipments" && <Shipments data={data} t={t} />}
      {data && kind === "money" && <Money data={data} t={t} />}
      {data && kind === "service" && <Service data={data} t={t} />}
      {data && kind === "purchasing" && <Purchasing data={data} t={t} />}
    </>
  );
}

type Stage = { code: string; title: string; on: string | null; done: boolean; overdue_days: number; amount?: number | null; currency?: string };
type Ship = { deal_id: string; deal_number: string; client: string; site: string; manager: string; batch: string; route: string; units: number; weight_kg: number | null;
  stages: Stage[]; next: Stage | null; overdue: number; phase: string; readiness: Stage[] };

function Shipments({ data, t }: { data: Record<string, unknown>; t: Record<string, number> }) {
  const rows = data.rows as Ship[];
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"" | "late" | "transit">("");
  const shown = rows.filter((r) => (!q || [r.deal_number, r.client, r.site, r.route, r.manager].some((x) => (x || "").toLowerCase().includes(q.toLowerCase())))
    && (only !== "late" || r.overdue > 0) && (only !== "transit" || (r.stages.some((s) => s.code === "shipment" && s.done) && !r.stages.every((s) => s.done))));
  const cell = (s: Stage | undefined) => s ? <td style={{ whiteSpace: "nowrap", background: s.done ? "var(--ok-bg)" : s.overdue_days > 0 ? "var(--late-bg)" : undefined }}>{d(s.on)}{s.done ? " ✓" : ""}{!s.done && late(s.overdue_days)}</td> : <td>—</td>;
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        <Kpi v={t.batches} l="партий в работе" /><Kpi v={t.to_ship_30d} l="отгрузка в ближайшие 30 дней" /><Kpi v={t.in_transit} l="в пути" /><Kpi v={t.overdue} l="с просрочкой" bad />
      </div>
      <div className="row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
        <input placeholder="🔍 сделка, клиент, маршрут, менеджер" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 260px" }} />
        <div className="tabs" style={{ margin: 0 }}>
          <button className={only === "" ? "active" : ""} onClick={() => setOnly("")}>все</button>
          <button className={only === "transit" ? "active" : ""} onClick={() => setOnly("transit")}>в пути</button>
          <button className={only === "late" ? "active" : ""} onClick={() => setOnly("late")}>с просрочкой</button></div>
      </div>
      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 10 }}>
        <table style={{ minWidth: 1000 }}>
          <thead><tr><th>Сделка</th><th>Клиент / объект</th><th>Партия · маршрут</th><th>Готовность завода</th><th>Отгрузка</th><th>Граница</th><th>ДТ</th><th>Доставка</th><th>Этап</th><th>Менеджер</th></tr></thead>
          <tbody>{shown.map((r, i) => {
            const st = (c: string) => r.stages.find((s) => s.code === c);
            return (
              <tr key={i} style={r.overdue > 0 ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}>
                <td><Link to={`/deals/${r.deal_id}?tab=execution`} style={{ fontWeight: 600 }}>{r.deal_number}</Link></td>
                <td><b>{r.client}</b>{r.site && <div className="muted" style={{ fontSize: 12 }}>{r.site}</div>}</td>
                <td><b>{r.batch}</b><div className="muted" style={{ fontSize: 12 }}>{r.route}{r.units ? ` · ${r.units} ТС` : ""}{r.weight_kg ? ` · ${Math.round(r.weight_kg)} кг` : ""}</div></td>
                <td style={{ fontSize: 12 }}>{r.readiness.map((x, j) => <div key={j}>{d(x.on)}{x.done ? " ✓" : ""}{!x.done && late(x.overdue_days)}</div>)}</td>
                {cell(st("shipment"))}{cell(st("border"))}{cell(st("declaration"))}{cell(st("delivery"))}
                <td style={{ fontSize: 12, fontWeight: 600 }}>{r.phase}</td><td style={{ whiteSpace: "nowrap" }}>{r.manager}</td>
              </tr>);
          })}
          {shown.length === 0 && <tr><td colSpan={10} className="muted" style={{ padding: 16 }}>Партий нет</td></tr>}</tbody>
        </table>
      </div>
    </>
  );
}

type Pay = { deal_id: string | null; url?: string; deal_number: string; client: string; manager: string; title: string; direction: "in" | "out"; on: string; amount: number | null; currency: string; overdue_days: number };
type Guar = { deal_id: string; deal_number: string; client: string; kind: string; amount: number | null; currency: string; until: string | null; status: string; bank: string };
type Curve = { week: string; in: number; out: number; cum: number };

function Money({ data, t }: { data: Record<string, unknown>; t: Record<string, number> }) {
  const rows = data.rows as Pay[]; const rec = data.receivables as Pay[]; const g = data.guarantees as Guar[]; const curve = data.curve as Curve[];
  const parts = (data.parts || { rows: [], sum_rub: 0 }) as { rows: { id: string; number: string; client: string; manager: string; amount: number; lines: number; urgent: boolean; url: string }[]; sum_rub: number };
  const money = (p: Pay) => (p.amount == null ? "—" : `${fmtRub(p.amount)} ${p.currency}`);
  const maxAbs = Math.max(1, ...curve.map((c) => Math.max(c.in, c.out, Math.abs(c.cum))));
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        <Kpi v={fmtRub(t.in_rub)} l="поступления, RUB (90 дн.)" /><Kpi v={fmtRub(t.out_rub)} l="платежи заводам, RUB (90 дн.)" />
        <Kpi v={t.receivable_count} l="просроченных поступлений" bad /><Kpi v={fmtRub(t.min_cum)} l="минимум накопленного потока, RUB" bad={t.min_cum < 0} />
      </div>
      {rec.length > 0 && (
        <div className="card" style={{ marginTop: 10, borderLeft: "4px solid var(--accent)" }}>
          <b>Просроченная дебиторка</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{rec.map((p, i) => (
            <tr key={i}><td><Link to={p.url || `/deals/${p.deal_id}?tab=planfact`}>{p.deal_number}</Link></td><td>{p.client}</td><td>{p.title}</td><td style={{ whiteSpace: "nowrap" }}>{d(p.on)}{late(p.overdue_days)}</td><td className="num"><b>{money(p)}</b></td><td>{p.manager}</td></tr>))}</tbody></table>
        </div>)}
      {curve.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Поток по неделям, RUB</b> <span className="muted" style={{ fontSize: 12 }}>чёрное — поступления, красное — платежи, линия — накопленный итог</span>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 140, marginTop: 8, borderBottom: "1px solid var(--line)" }}>
            {curve.map((c) => (
              <div key={c.week} title={`${fmtDate(c.week)}: +${fmtRub(c.in)} / −${fmtRub(c.out)} · итог ${fmtRub(c.cum)}`} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", gap: 2, height: "100%" }}>
                <div style={{ width: "100%", display: "flex", gap: 2, alignItems: "flex-end", height: "85%" }}>
                  <div style={{ flex: 1, background: "var(--ok)", height: `${(c.in / maxAbs) * 100}%` }} /><div style={{ flex: 1, background: "var(--bad)", height: `${(c.out / maxAbs) * 100}%` }} /></div>
                <div style={{ fontSize: 10, color: c.cum < 0 ? "var(--bad)" : "var(--muted)", whiteSpace: "nowrap" }}>{fmtDate(c.week).slice(0, 5)}</div>
              </div>))}
          </div>
        </div>)}
      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 10 }}>
        <table>
          <thead><tr><th>Дата</th><th>Сделка</th><th>Клиент</th><th>Платёж</th><th className="num">Сумма</th><th>Менеджер</th></tr></thead>
          <tbody>{rows.map((p, i) => (
            <tr key={i} style={p.overdue_days > 0 ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}>
              <td style={{ whiteSpace: "nowrap" }}>{d(p.on)}{late(p.overdue_days)}</td><td><Link to={p.url || `/deals/${p.deal_id}?tab=planfact`}>{p.deal_number}</Link></td><td>{p.client}</td>
              <td>{p.direction === "in" ? <span className="badge ok">получить</span> : <span className="badge bad">заплатить</span>} {p.title}</td>
              <td className="num" style={{ whiteSpace: "nowrap", fontWeight: 600 }}>{money(p)}</td><td>{p.manager}</td></tr>))}
          {rows.length === 0 && <tr><td colSpan={6} className="muted" style={{ padding: 16 }}>Платежей на горизонте нет</td></tr>}</tbody>
        </table>
      </div>
      {parts.rows.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Запасные части: заказано клиентами, не отгружено</b> <span className="muted">· ожидаемые поступления {fmtRub(parts.sum_rub)} ₽ — срок по счёту или отгрузке, в кривую не входят</span>
          <table style={{ marginTop: 6, fontSize: 13 }}><thead><tr><th>Заявка</th><th>Клиент</th><th className="num">Позиций</th><th className="num">Сумма, ₽</th><th>Менеджер</th></tr></thead>
            <tbody>{parts.rows.map((p) => <tr key={p.id}><td><Link to={p.url}>{p.number}</Link>{p.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td><td>{p.client}</td><td className="num">{p.lines}</td><td className="num"><b>{fmtRub(p.amount)}</b></td><td>{p.manager}</td></tr>)}</tbody></table>
        </div>)}
      {g.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Аккредитивы и гарантии</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><thead><tr><th>Сделка</th><th>Клиент</th><th>Вид</th><th className="num">Сумма</th><th>До</th><th>Статус</th><th>Банк</th></tr></thead>
            <tbody>{g.map((x, i) => <tr key={i}><td><Link to={`/deals/${x.deal_id}?tab=execution`}>{x.deal_number}</Link></td><td>{x.client}</td><td>{x.kind}</td><td className="num">{x.amount != null ? `${fmtRub(x.amount)} ${x.currency || ""}` : "—"}</td><td>{d(x.until)}</td><td>{x.status || "—"}</td><td>{x.bank || ""}</td></tr>)}</tbody></table>
        </div>)}
    </>
  );
}

type Acc = Stage & { deal_id: string; deal_number: string; client: string; site: string; manager: string; position: string; qty: number; accepted: number; open_remarks: number };
type Rem = { machine_id: string; machine: string; client: string; to_hours: number; due_on: string | null; overdue_days: number; request_id: string | null };
type Claim = { id: string; number: string; status: string; factory: string; created_at: string };

function Service({ data, t }: { data: Record<string, unknown>; t: Record<string, number> }) {
  const acc = data.acceptances as Acc[]; const pnr = data.pnr as Acc[]; const rem = data.reminders as Rem[]; const claims = data.claims as Claim[];
  const table = (rows: Acc[], title: string) => (
    <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 10 }}>
      <div style={{ padding: "8px 12px", fontWeight: 700 }}>{title} <span className="muted" style={{ fontWeight: 400 }}>· {rows.filter((r) => !r.done).length} впереди</span></div>
      <table>
        <thead><tr><th>Дата</th><th>Этап</th><th>Сделка</th><th>Клиент / объект</th><th>Позиция</th><th>Принято</th><th>Замечаний открыто</th><th>Менеджер</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} style={{ opacity: r.done ? .6 : 1, ...(r.overdue_days > 0 ? { boxShadow: "inset 3px 0 0 var(--bad)" } : {}) }}>
            <td style={{ whiteSpace: "nowrap" }}>{d(r.on)}{r.done ? " ✓" : late(r.overdue_days)}</td><td>{r.title}</td>
            <td><Link to={`/deals/${r.deal_id}?tab=acceptance`}>{r.deal_number}</Link></td><td><b>{r.client}</b>{r.site && <div className="muted" style={{ fontSize: 12 }}>{r.site}</div>}</td>
            <td>{r.position}</td><td>{r.accepted} из {r.qty}</td><td style={{ color: r.open_remarks ? "var(--warn)" : undefined }}>{r.open_remarks || "—"}</td><td>{r.manager}</td></tr>))}
        {rows.length === 0 && <tr><td colSpan={8} className="muted" style={{ padding: 16 }}>Нет</td></tr>}</tbody>
      </table>
    </div>);
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        <Kpi v={t.machines} l="машин в парке" /><Kpi v={t.acceptances_30d} l="приёмок в ближайшие 30 дней" /><Kpi v={t.overdue} l="просроченных приёмок и ПНР" bad />
        <Kpi v={t.reminders_due} l="ТО к сроку" bad /><Kpi v={t.claims_open} l="открытых претензий" bad />
      </div>
      {table(acc, "Приёмки машин (онлайн и на заводе)")}
      {table(pnr, "Пусконаладка (ПНР)")}
      <div className="grid cols-2" style={{ marginTop: 10 }}>
        <div className="card"><b>Напоминания о ТО</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{rem.map((r, i) => <tr key={i}><td><Link to="/counterparties?type=clients">{r.machine || "машина"}</Link></td><td>{r.client}</td><td>{r.to_hours} м·ч</td><td style={{ whiteSpace: "nowrap" }}>{d(r.due_on)}{late(r.overdue_days)}</td></tr>)}
            {rem.length === 0 && <tr><td className="muted">Напоминаний нет</td></tr>}</tbody></table></div>
        <div className="card"><b>Открытые претензии заводам</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{claims.map((c) => <tr key={c.id}><td><Link to="/supply?tab=claims">{c.number}</Link></td><td>{c.factory}</td><td>{c.status}</td><td>{d(c.created_at)}</td></tr>)}
            {claims.length === 0 && <tr><td className="muted">Претензий нет</td></tr>}</tbody></table></div>
      </div>
    </>
  );
}

type Opt = { deal_id: string; deal_number: string; client: string; manager: string; option: string; qty: number; supplier: string; lead_days: number; status: string; need_by: string | null; order_by: string | null; ordered_at: string | null; late: boolean; cost: number | null; currency: string };
type Req = { id: string; number: string; urgent: boolean; client: string; created_at: string; lines: number; unordered: number };
type Po = { id: string; number: string; status: string; supplier: string; lines: number; received: number; eta: string | null; overdue: number };

function Purchasing({ data, t }: { data: Record<string, unknown>; t: Record<string, number> }) {
  const opts = data.options as Opt[]; const reqs = data.requests as Req[]; const pos = data.orders as Po[];
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        <Kpi v={t.options_to_order} l="опций РФ заказать" /><Kpi v={t.options_late} l="опций с упущенным сроком заказа" bad /><Kpi v={t.requests_open} l="заявок на запчасти без заказа" /><Kpi v={t.orders_open} l="заказов поставщикам в работе" /><Kpi v={t.orders_overdue} l="заказов с просроченным ETA" bad />
      </div>
      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 10 }}>
        <div style={{ padding: "8px 12px", fontWeight: 700 }}>Опции РФ по сделкам <span className="muted" style={{ fontWeight: 400 }}>· заказать не позже «готовность завода − срок поставки»</span></div>
        <table>
          <thead><tr><th>Заказать до</th><th>Нужно к</th><th>Опция</th><th>Поставщик · срок</th><th>Сделка</th><th>Клиент</th><th>Статус</th><th>Менеджер</th></tr></thead>
          <tbody>{opts.map((o, i) => (
            <tr key={i} style={o.late ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}>
              <td style={{ whiteSpace: "nowrap", fontWeight: 600, color: o.late ? "var(--bad)" : undefined }}>{d(o.order_by)}{o.late && " · упущен"}</td><td>{d(o.need_by)}</td>
              <td>{o.option}{o.qty > 1 && ` × ${o.qty}`}</td><td>{o.supplier || "—"}{o.lead_days ? ` · ${o.lead_days} дн.` : ""}</td>
              <td><Link to={`/deals/${o.deal_id}?tab=positions`}>{o.deal_number}</Link></td><td>{o.client}</td>
              <td>{o.ordered_at ? <span className="badge ok">заказано {fmtDate(o.ordered_at)}</span> : <span className="badge">{o.status}</span>}</td><td>{o.manager}</td></tr>))}
          {opts.length === 0 && <tr><td colSpan={8} className="muted" style={{ padding: 16 }}>Опций РФ в активных сделках нет</td></tr>}</tbody>
        </table>
      </div>
      <div className="grid cols-2" style={{ marginTop: 10 }}>
        <div className="card"><b>Заявки на запчасти без заказа</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{reqs.map((r) => <tr key={r.id}><td><Link to={`/deals/parts/${r.id}`}>{r.number}</Link>{r.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td><td>{r.client}</td><td>{r.unordered} из {r.lines} строк</td><td>{d(r.created_at)}</td></tr>)}
            {reqs.length === 0 && <tr><td className="muted">Нет</td></tr>}</tbody></table></div>
        <div className="card"><b>Заказы поставщикам</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{pos.map((p) => <tr key={p.id} style={p.overdue ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}><td><Link to="/supply?tab=po">{p.number}</Link></td><td>{p.supplier}</td><td>{p.received} из {p.lines} принято</td><td style={{ whiteSpace: "nowrap" }}>ETA {d(p.eta)}{p.overdue ? <span style={{ color: "var(--bad)" }}> · просрочено {p.overdue}</span> : ""}</td></tr>)}
            {pos.length === 0 && <tr><td className="muted">Нет</td></tr>}</tbody></table></div>
      </div>
    </>
  );
}
