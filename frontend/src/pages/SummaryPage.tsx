import { useEffect, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { Crumbs } from "../components/Levels";

import { api, fmtDate, fmtRub } from "../api";
import type { Me } from "../types";
import { isNewUi } from "../uiMode";
import { Loading } from "../components/States";
import SummaryOverview from "../components/SummaryOverview";

// Сводные экраны по всем сделкам: Отгрузки (логист), Деньги (ФД), Сервис, Закупки. Один компонент, вкладка по роли.

type Kind = "overview" | "shipments" | "money" | "service" | "purchasing" | "rework" | "speed";
const TITLES: Record<Kind, string> = { overview: "Показатели", shipments: "Отгрузки", money: "Деньги", service: "Сервис", purchasing: "Закупки", rework: "Возвраты на доработку", speed: "Скорость процесса" };
const load = <T,>(k: string, d: T): T => { try { return (JSON.parse(localStorage.getItem(k) || "") as T) ?? d; } catch { return d; } };

function Kpi({ v, l, bad }: { v: string | number; l: string; bad?: boolean }) {
  // новый вид: единая карточка показателя (значение, подпись; красным — только отклонение)
  if (isNewUi()) return <div className={"sum-kpi" + (bad && Number(v) > 0 ? " bad" : "")}><b>{v ?? "нет данных"}</b><span>{l}</span></div>;
  return <div style={{ flex: "1 1 130px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}>
    <div style={{ fontSize: 20, fontWeight: 700, color: bad && Number(v) > 0 ? "var(--bad)" : undefined }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{l}</div></div>;
}
const d = (x: string | null | undefined) => (x ? fmtDate(x) : "—");
const late = (n: number) => (n > 0 ? <span style={{ color: "var(--bad)", fontWeight: 600 }}> · просрочено {n} дн.</span> : null);

export default function SummaryPage({ me }: { me: Me }) {
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const fin = (me.perms?.finance_analytics ?? 0) >= 1;
  const kinds = ([
    isNewUi() && has("cd", "gd", "fd", "manager") && "overview",   // D-175: период, динамика, сравнение, план/факт — только новый вид
    has("logistician", "cd", "gd", "fd", "manager", "purchasing") && "shipments",
    fin && "money",
    has("service_head", "cd", "gd", "manager", "fd") && "service",
    has("purchasing", "cd", "gd", "service_head", "fd") && "purchasing",
    has("cd", "gd", "fd") && "rework",   // D-100: возвраты по причинам из справочника
    has("cd", "gd", "fd") && "speed",    // D-144: время на этапах сделки
  ].filter(Boolean)) as Kind[];
  const pref = load<Kind>("gsho.summaryKind", isNewUi() && has("cd", "gd", "fd", "manager") ? "overview" : has("logistician") ? "shipments" : has("fd") ? "money" : has("service_head") ? "service" : has("purchasing") ? "purchasing" : kinds[0]);
  // вид сводки — в адресе (?kind=…): «Назад» возвращает прежний вид, ссылка открывает нужный; без параметра — запомненный выбор
  const [sp, setSp] = useSearchParams();
  const urlKind = sp.get("kind") as Kind | null;
  const kind: Kind = urlKind && kinds.includes(urlKind) ? urlKind : kinds.includes(pref) ? pref : kinds[0];
  const setKind = (k: Kind) => { try { localStorage.setItem("gsho.summaryKind", JSON.stringify(k)); } catch { /* нет хранилища */ } const n = new URLSearchParams(sp); n.set("kind", k); setSp(n); };
  // данные привязаны к виду: при переключении вида прежние данные не попадают в чужой экран (раньше «Сервис» падал на данных «Отгрузок»)
  const [loaded, setLoaded] = useState<{ kind: Kind; data: Record<string, unknown> } | null>(null);
  const data = loaded && loaded.kind === kind ? loaded.data : null;
  const [err, setErr] = useState("");
  useEffect(() => { setErr(""); if (kind && kind !== "speed" && kind !== "overview") api.get<Record<string, unknown>>(kind === "rework" ? "/api/reports/rework" : `/api/summary/${kind}`).then((d) => setLoaded({ kind, data: d })).catch((e) => setErr((e as Error).message)); }, [kind]);
  if (!kinds.length) return <div className="card muted">Сводные экраны для вашей роли не предусмотрены.</div>;
  // D-130: у кого открыт раздел «Финансы» — «Деньги» там; остальным (КД с финансовой аналитикой) — сводка на общем сервисе
  if (kind === "money" && (me.perms?.finance_section ?? 0) >= 1) return <Navigate to="/finance?tab=overview" replace />;
  const t = (data?.totals || {}) as Record<string, number>;
  return (
    <>
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Сводка", to: "/summary" }, { label: TITLES[kind] }]} />
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ margin: 0 }}>Сводка · {TITLES[kind]}</h1>
        {kinds.length > 1 && <div className="tabs" style={{ margin: 0 }}>{kinds.map((k) => <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>{TITLES[k]}</button>)}</div>}
      </div>
      {err && <div className="alert error">{err}</div>}
      {kind === "speed" && <Speed />}
      {kind === "overview" && <SummaryOverview />}
      {!data && !err && kind !== "speed" && kind !== "overview" && <Loading text="Загружаю…" />}
      {data && kind === "shipments" && <Shipments data={data} t={t} />}
      {data && kind === "money" && <Money data={data} t={t} />}
      {data && kind === "service" && <Service data={data} t={t} />}
      {data && kind === "purchasing" && <Purchasing data={data} t={t} />}
      {data && kind === "rework" && <Rework data={data as unknown as ReworkData} />}
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
  const cell = (s: Stage | undefined, label = "") => s ? <td data-label={label} style={{ whiteSpace: "nowrap", background: s.done ? "var(--ok-bg)" : s.overdue_days > 0 ? "var(--late-bg)" : undefined }}>{d(s.on)}{s.done ? " ✓" : ""}{!s.done && late(s.overdue_days)}</td> : <td data-label={label}>—</td>;
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
      <div className="card sum-ship" style={{ padding: 0, overflowX: "auto", marginTop: 10 }}>
        <table style={{ minWidth: 1000 }}>
          <thead><tr><th>Сделка</th><th>Клиент / объект</th><th>Партия · маршрут</th><th>Готовность завода</th><th>Отгрузка</th><th>Граница</th><th>ДТ</th><th>Доставка</th><th>Этап</th><th>Менеджер</th></tr></thead>
          <tbody>{shown.map((r, i) => {
            const st = (c: string) => r.stages.find((s) => s.code === c);
            return (
              <tr key={i} style={r.overdue > 0 ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}>
                <td data-label="Сделка"><Link to={`/deals/${r.deal_id}?tab=execution`} style={{ fontWeight: 600 }}>{r.deal_number}</Link></td>
                <td data-label="Клиент"><b>{r.client}</b>{r.site && <div className="muted" style={{ fontSize: 12 }}>{r.site}</div>}</td>
                <td data-label="Партия"><b>{r.batch}</b><div className="muted" style={{ fontSize: 12 }}>{r.route}{r.units ? ` · ${r.units} ТС` : ""}{r.weight_kg ? ` · ${Math.round(r.weight_kg)} кг` : ""}</div></td>
                <td data-label="Готовность завода" style={{ fontSize: 12 }}>{r.readiness.map((x, j) => <div key={j}>{d(x.on)}{x.done ? " ✓" : ""}{!x.done && late(x.overdue_days)}</div>)}</td>
                {cell(st("shipment"), "Отгрузка")}{cell(st("border"), "Граница")}{cell(st("declaration"), "ДТ")}{cell(st("delivery"), "Доставка")}
                <td data-label="Этап" style={{ fontSize: 12, fontWeight: 600 }}>{r.phase}</td><td data-label="Менеджер" style={{ whiteSpace: "nowrap" }}>{r.manager}</td>
              </tr>);
          })}
          {shown.length === 0 && <tr><td colSpan={10} className="muted" style={{ padding: 16 }}>{isNewUi() && (q || only) && rows.length > 0
            ? <>По отбору{q ? ` «${q}»` : ""}{only ? ` · ${only === "late" ? "с просрочкой" : "в пути"}` : ""} партий нет. <button type="button" className="btn ghost" onClick={() => { setQ(""); setOnly(""); }}>Сбросить отбор</button></>
            : "Партий нет"}</td></tr>}</tbody>
        </table>
      </div>
    </>
  );
}

type Pay = { deal_id: string | null; url?: string; deal_number: string; client: string; manager: string; title: string; direction: "in" | "out"; on: string; amount: number | null; currency: string; overdue_days: number };
type Guar = { deal_id: string; deal_number: string; client: string; kind: string; amount: number | null; currency: string; until: string | null; status: string; bank: string };
type Curve = { week: string; in: number; out: number; cum: number | null };

function Money({ data, t }: { data: Record<string, unknown>; t: Record<string, number> }) {
  const rows = data.rows as Pay[]; const rec = data.receivables as Pay[]; const g = data.guarantees as Guar[]; const curve = data.curve as Curve[];
  const parts = (data.parts || { rows: [], sum_rub: 0 }) as { rows: { id: string; number: string; client: string; manager: string; amount: number; lines: number; urgent: boolean; url: string }[]; sum_rub: number };
  const money = (p: Pay) => (p.amount == null ? "—" : `${fmtRub(p.amount)} ${p.currency}`);
  const maxAbs = Math.max(1, ...curve.map((c) => Math.max(c.in, c.out)));
  const unknown = (t as unknown as Record<string, unknown>).balance_status === "unknown" || t.min_cum == null;
  const byCcy = (data.totals as { by_currency?: Record<string, { in: number; out: number }> }).by_currency || {};
  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        <Kpi v={fmtRub(t.in_rub)} l="поступления, RUB (90 дн.)" /><Kpi v={fmtRub(t.out_rub)} l="платежи, RUB (90 дн.)" />
        <Kpi v={t.receivable_count} l="просроченных поступлений" bad /><Kpi v={unknown ? "не подтверждён" : fmtRub(t.min_cum)} l="минимум прогнозного остатка RUB-счетов" bad={!unknown && t.min_cum < 0} />
      </div>
      {unknown && <div className="alert warn" style={{ fontSize: 13 }}>Остаток RUB-счетов не подтверждён: прогнозный остаток не рассчитывается от нуля. Подтвердить остаток могут ФД и ЗГД в разделе «Финансы».</div>}
      {Object.keys(byCcy).filter((c) => c !== "RUB").length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Также в валюте: {Object.entries(byCcy).filter(([c]) => c !== "RUB").map(([c, v]) => `${c} — поступления ${fmtRub(v.in)}, платежи ${fmtRub(v.out)}`).join("; ")}</div>}
      {rec.length > 0 && (
        <div className="card" style={{ marginTop: 10, borderLeft: "4px solid var(--accent)" }}>
          <b>Просроченная дебиторка</b>
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{rec.map((p, i) => (
            <tr key={i}><td><Link to={p.url || `/deals/${p.deal_id}?tab=planfact`}>{p.deal_number}</Link></td><td>{p.client}</td><td>{p.title}</td><td style={{ whiteSpace: "nowrap" }}>{d(p.on)}{late(p.overdue_days)}</td><td className="num"><b>{money(p)}</b></td><td>{p.manager}</td></tr>))}</tbody></table>
        </div>)}
      {curve.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Поток по неделям, RUB</b> <span className="muted" style={{ fontSize: 12 }}>зелёное — поступления, красное — платежи; при наведении — прогнозный остаток RUB-счетов на конец недели</span>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 140, marginTop: 8, borderBottom: "1px solid var(--line)" }}>
            {curve.map((c) => (
              <div key={c.week} title={`${fmtDate(c.week)}: +${fmtRub(c.in)} / −${fmtRub(c.out)} · остаток ${c.cum == null ? "не подтверждён" : fmtRub(c.cum)}`} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center", gap: 2, height: "100%" }}>
                <div style={{ width: "100%", display: "flex", gap: 2, alignItems: "flex-end", height: "85%" }}>
                  <div style={{ flex: 1, background: "var(--ok)", height: `${(c.in / maxAbs) * 100}%` }} /><div style={{ flex: 1, background: "var(--bad)", height: `${(c.out / maxAbs) * 100}%` }} /></div>
                <div style={{ fontSize: 10, color: c.cum != null && c.cum < 0 ? "var(--bad)" : "var(--muted)", whiteSpace: "nowrap" }}>{fmtDate(c.week).slice(0, 5)}</div>
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
          <table style={{ marginTop: 6, fontSize: 13 }}><tbody>{rem.map((r, i) => <tr key={i}><td><Link to="/legal?tab=counterparties&type=clients">{r.machine || "машина"}</Link></td><td>{r.client}</td><td>{r.to_hours} м·ч</td><td style={{ whiteSpace: "nowrap" }}>{d(r.due_on)}{late(r.overdue_days)}</td></tr>)}
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

type ReworkData = { total: number; by_reason: { code: string; title: string; count: number }[]; by_block: { block: string; title: string; count: number }[];
  by_deal: { deal_id: string; number: string; count: number }[];
  rows: { at: string; deal_id: string; number: string; title: string; block: string; comment: string; by: string }[] };

/** D-100: возвраты на доработку — по причинам из справочника, по блокам, сделки с повторными кругами, последние возвраты. */
function Rework({ data }: { data: ReworkData }) {
  if (!data.total) return <div className="card muted">Возвратов на доработку пока нет.</div>;
  const max = Math.max(...data.by_reason.map((r) => r.count), 1);
  return (
    <>
      <p className="muted" style={{ margin: "8px 0" }}>Всего возвратов: <b>{data.total}</b>. Причину выбирают из списка — его ведут КД и ЗГД в «Настройки → Ставки и нормативы → Возвраты на доработку». Возвраты до 20.11.2026 — «без кода», с исходным текстом; отказ ФД или ЗГД в визе — отдельной строкой.</p>
      <div className="grid cols-2">
        <div className="card">
          <div className="section-head"><h2>По причинам</h2></div>
          <table><tbody>{data.by_reason.map((r) => (
            <tr key={r.code}><td>{r.title}</td><td style={{ width: "40%" }}><div style={{ background: "var(--accent-soft)", height: 10, borderRadius: 4, width: `${(r.count / max) * 100}%` }} /></td><td className="num"><b>{r.count}</b></td></tr>))}</tbody></table>
        </div>
        <div className="card">
          <div className="section-head"><h2>Кому возвращали</h2></div>
          <table><tbody>{data.by_block.map((r) => <tr key={r.block}><td>{r.title}</td><td className="num"><b>{r.count}</b></td></tr>)}</tbody></table>
          {data.by_deal.length > 0 && <>
            <div className="section-head" style={{ marginTop: 12 }}><h2>Сделки с повторными возвратами</h2></div>
            <table><tbody>{data.by_deal.map((r) => <tr key={r.deal_id}><td><Link to={`/deals/${r.deal_id}?tab=pricing`}>{r.number || "сделка"}</Link></td><td className="num"><b>{r.count}</b> {r.count >= 3 ? <span className="badge bad">кругов</span> : "раза"}</td></tr>)}</tbody></table>
          </>}
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <div className="section-head"><h2>Последние возвраты</h2></div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ fontSize: 13 }}><thead><tr><th>Когда</th><th>Сделка</th><th>Причина</th><th>Кому</th><th>Уточнение</th><th>Кто вернул</th></tr></thead>
            <tbody>{data.rows.map((r, i) => (
              <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{fmtDate(r.at)}</td><td>{r.deal_id ? <Link to={`/deals/${r.deal_id}?tab=pricing`}>{r.number}</Link> : "—"}</td>
                <td>{r.title}</td><td>{r.block || "—"}</td><td className="muted">{r.comment || "—"}</td><td style={{ whiteSpace: "nowrap" }}>{r.by}</td></tr>))}</tbody></table>
        </div>
      </div>
    </>);
}


type SpeedStage = { status: string; title: string; who: string; passes: number; median_h: number | null; p80_h: number | null; total_h: number; now: number;
  now_longest: { deal_id: string; number: string; hours: number }[] };
type SpeedData = { days: number; since: string; stages: SpeedStage[]; bottleneck: string | null; rework_deals: number; rework_rounds: number;
  end_to_end: { key: string; title: string; deals: number; median_h: number | null; p80_h: number | null }[];
  people: { user_id: string; name: string; done: number; median_h: number | null; on_time_pct: number | null; open: number; overdue: number }[] };

const dur = (h: number | null | undefined) => (h == null ? "—" : h < 1 ? "< 1 ч" : h < 48 ? `${Math.round(h)} ч` : `${(h / 24).toFixed(h < 240 ? 1 : 0).replace(".", ",")} дн.`);

/** D-144: «Скорость процесса» — где сделки ждут дольше всего и у кого (КД, ФД, ЗГД). */
function Speed() {
  const [days, setDays] = useState(90);
  const [d, setD] = useState<SpeedData | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setD(null); api.get<SpeedData>(`/api/reports/speed?days=${days}`).then(setD).catch((e) => setErr((e as Error).message)); }, [days]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted">Считаю…</div>;
  const max = Math.max(1, ...d.stages.map((s) => s.p80_h || 0));
  const bn = d.stages.find((s) => s.status === d.bottleneck);
  return (
    <div className="speed">
      <div className="row speed-head">
        <span className="muted">Время на этапах сделки по истории смен статуса; период — этапы, завершённые за последние</span>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 90, 180, 365].map((x) => <option key={x} value={x}>{x} дней</option>)}</select>
      </div>
      {bn && bn.total_h > 0 && <div className="alert warn">Дольше всего сделки ждут на этапе <b>«{bn.title}»</b> ({bn.who}): обычно {dur(bn.median_h)}, у каждой пятой — дольше {dur(bn.p80_h)}; за период — {dur(bn.total_h)} суммарно по {bn.passes} проходам.</div>}
      <div className="speed-e2e">{d.end_to_end.map((e) => (
        <div key={e.key} className="speed-kpi"><b>{dur(e.median_h)}</b><span>{e.title}</span><small>обычно · у 80 % — до {dur(e.p80_h)} · сделок: {e.deals}</small></div>))}
        <div className="speed-kpi"><b>{d.rework_rounds}</b><span>возвратов на доработку</span><small>в {d.rework_deals} сделках</small></div>
      </div>
      <div className="card">
        <h2>Этапы</h2>
        <table className="tbl speed-tbl">
          <thead><tr><th>Этап</th><th>Кто</th><th className="num">Проходов</th><th className="num">Обычно</th><th className="num">У 80 % — до</th><th>Распределение</th><th>Сейчас на этапе</th></tr></thead>
          <tbody>{d.stages.map((s) => (
            <tr key={s.status} className={s.status === d.bottleneck ? "speed-bn" : ""}>
              <td><b>{s.title}</b></td><td className="muted">{s.who}</td><td className="num">{s.passes || ""}</td>
              <td className="num">{dur(s.median_h)}</td><td className="num">{dur(s.p80_h)}</td>
              <td><svg className="speed-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden><rect className="bg" width="100" height="10" rx="5" />
                <rect className="p80" width={((s.p80_h || 0) / max) * 100} height="10" rx="5" /><rect className="med" width={((s.median_h || 0) / max) * 100} height="10" rx="5" /></svg></td>
              <td>{s.now > 0 && <>{s.now}{s.now_longest.length > 0 && <span className="muted"> · дольше всех: {s.now_longest.slice(0, 3).map((x, i) => <span key={x.deal_id}>{i ? ", " : ""}<Link to={`/deals/${x.deal_id}`}>{x.number}</Link> {dur(x.hours)}</span>)}</span>}</>}</td>
            </tr>))}</tbody>
        </table>
        <p className="muted speed-note">«Обычно» — медиана: половина сделок проходит этап быстрее. «КП отправлено» — время решения клиента, в узкое место не входит. Исполнение контракта не учитывается.</p>
      </div>
      <div className="card">
        <h2>Задачи по сотрудникам</h2>
        <table className="tbl">
          <thead><tr><th>Сотрудник</th><th className="num">Выполнено</th><th className="num">Обычно на задачу</th><th className="num">В срок</th><th className="num">Открыто</th><th className="num">Просрочено</th></tr></thead>
          <tbody>{d.people.map((p) => (
            <tr key={p.user_id}><td>{p.name}</td><td className="num">{p.done || ""}</td><td className="num">{dur(p.median_h)}</td>
              <td className="num">{p.on_time_pct != null ? `${p.on_time_pct} %` : "—"}</td><td className="num">{p.open || ""}</td>
              <td className={"num" + (p.overdue ? " txt-bad" : "")}>{p.overdue || ""}</td></tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}
