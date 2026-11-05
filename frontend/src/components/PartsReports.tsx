import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";

// Отчёты по запчастям (ТЗ запчастей, раздел 11). Себестоимость и маржу сервер отдаёт только КД, ФД и ЗГД.

type Kind = "sales" | "service" | "lead" | "stock" | "missing" | "warranty" | "shipments";
const TITLES: Record<Kind, string> = { sales: "Продажи и маржа", service: "Сервис клиентам", lead: "Сроки поставщиков", stock: "Неликвиды и оборачиваемость",
  missing: "Спрос без склада", warranty: "Гарантия", shipments: "История отгрузок" };
type Row = Record<string, unknown>;

const yearAgo = () => { const d = new Date(); d.setFullYear(d.getFullYear() - 1); return d.toISOString().slice(0, 10); };

export default function PartsReports() {
  const [kind, setKind] = useState<Kind>("sales");
  const [by, setBy] = useState("client");
  const [from, setFrom] = useState(yearAgo());
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10));
  const [idle, setIdle] = useState(180);
  const [data, setData] = useState<Row | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const p = `date_from=${from}&date_to=${to}`;
    const url = { sales: `/api/parts/reports/sales?by=${by}&${p}`, service: `/api/parts/reports/service?${p}`, lead: `/api/parts/reports/lead-accuracy?${p}`,
      stock: `/api/parts/reports/stock?idle_days=${idle}`, missing: `/api/parts/reports/missing?${p}`, warranty: "/api/parts/reports/warranty", shipments: `/api/parts/reports/shipments?${p}` }[kind];
    setData(null); setErr("");
    api.get<Row>(url).then(setData).catch((e) => setErr((e as Error).message));
  }, [kind, by, from, to, idle]);
  const rows = (k = "rows") => ((data?.[k] as Row[]) || []);
  const n = (v: unknown, d = 0) => (v == null ? "—" : fmtRub(Number(v), d));
  return (
    <div className="card">
      <div className="row" style={{ flexWrap: "wrap" }}>{(Object.keys(TITLES) as Kind[]).map((k) => <button key={k} className={`btn ${kind === k ? "" : "ghost"}`} onClick={() => setKind(k)}>{TITLES[k]}</button>)}</div>
      <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
        {!["stock", "warranty"].includes(kind) && <><label className="row">с<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label><label className="row">по<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label></>}
        {kind === "sales" && <select value={by} onChange={(e) => setBy(e.target.value)}><option value="client">по клиентам</option><option value="group">по группам товара</option><option value="supplier">по поставщикам</option></select>}
        {kind === "stock" && <label className="row">без движения дольше<input type="number" style={{ width: 70 }} value={idle} onChange={(e) => setIdle(Number(e.target.value))} />дн.</label>}
      </div>
      {err && <div className="alert info">{err}</div>}
      {!data && !err && <div className="muted" style={{ marginTop: 8 }}>Загружаю…</div>}
      {data && kind === "sales" && (() => { const t = data.total as Row; const cost = rows()[0] && "margin_pct" in rows()[0]; return (
        <table style={{ marginTop: 8 }}><thead><tr><th>{by === "client" ? "Клиент" : by === "group" ? "Группа" : "Поставщик"}</th><th className="num">Строк</th><th className="num">Выручка, ₽</th>{cost && <><th className="num">Себестоимость, ₽</th><th className="num">Маржа, ₽</th><th className="num">Маржа, %</th></>}</tr></thead>
          <tbody>{rows().map((r) => <tr key={String(r.key)}><td>{String(r.name)}</td><td className="num">{String(r.lines)}</td><td className="num">{n(r.revenue_rub)}</td>
            {cost && <><td className="num">{n(r.cost_rub)}{Number(r.uncosted) > 0 && <span className="muted" title="строк без себестоимости"> *{String(r.uncosted)}</span>}</td><td className="num">{n(r.margin_rub)}</td><td className="num">{r.margin_pct != null ? `${r.margin_pct} %` : "—"}</td></>}</tr>)}
            <tr><td><b>Итого</b></td><td></td><td className="num"><b>{n(t.revenue_rub)}</b></td>{cost && <><td></td><td className="num"><b>{n(t.margin_rub)}</b></td><td className="num"><b>{t.margin_pct != null ? `${t.margin_pct} %` : "—"}</b></td></>}</tr></tbody></table>); })()}
      {data && kind === "service" && (
        <div className="grid cols-3" style={{ marginTop: 8 }}>
          <div>Заявок: <b>{String(data.requests)}</b> (аварийных {String(data.urgent)})</div><div>Строк: <b>{String(data.lines)}</b></div>
          <div>Со склада: <b>{data.from_stock_pct != null ? `${data.from_stock_pct} %` : "—"}</b></div>
          <div>Ушло в КП: <b>{data.in_kp_pct != null ? `${data.in_kp_pct} %` : "—"}</b></div>
          <div>Средний срок до первого КП: <b>{data.avg_hours_to_kp != null ? `${data.avg_hours_to_kp} ч` : "—"}</b> (по {String(data.kp_count)} заявкам)</div>
        </div>)}
      {data && kind === "lead" && (
        <table style={{ marginTop: 8 }}><thead><tr><th>Поставщик</th><th className="num">Строк принято</th><th className="num">Вовремя, %</th><th className="num">Отклонение в среднем, дн</th><th className="num">Наибольшая задержка, дн</th></tr></thead>
          <tbody>{rows().map((r) => <tr key={String(r.supplier_id)}><td>{String(r.supplier_name)}</td><td className="num">{String(r.lines)}</td><td className="num">{String(r.on_time_pct)}</td><td className="num">{String(r.avg_delay_days)}</td><td className="num">{String(r.max_delay_days)}</td></tr>)}
            {rows().length === 0 && <tr><td colSpan={5} className="muted">Нет принятых строк за период</td></tr>}</tbody></table>)}
      {data && kind === "stock" && (
        <>
          <p className="muted">Позиций с остатком: {String(data.positions)}, без движения {String(data.idle_days)}+ дн.: {String(data.idle_count)}.</p>
          <table><thead><tr><th>Артикул</th><th>Наименование</th><th>Склад</th><th className="num">Остаток</th><th className="num">Без движения, дн</th><th className="num">Отгружено за год</th><th className="num">Закупочная стоимость</th></tr></thead>
            <tbody>{rows("idle").map((r, i) => <tr key={i}><td>{String(r.article)}</td><td>{String(r.name)}</td><td>{String(r.warehouse)}</td><td className="num">{String(r.qty)}</td><td className="num">{r.idle_days != null ? String(r.idle_days) : "нет движений"}</td>
              <td className="num">{String(r.shipped_year)}</td><td className="num">{r.purchase_value != null ? `${n(r.purchase_value)} ${String(r.currency || "")}` : "—"}</td></tr>)}</tbody></table>
        </>)}
      {data && kind === "missing" && (
        <table style={{ marginTop: 8 }}><thead><tr><th>Артикул</th><th>Наименование</th><th>В каталоге</th><th className="num">Заявок</th><th className="num">Клиентов</th><th className="num">Кол-во</th></tr></thead>
          <tbody>{rows().map((r, i) => <tr key={i}><td>{String(r.article)}</td><td>{String(r.name)}</td><td>{r.in_catalog ? "да" : <span className="badge bad">нет</span>}</td><td className="num">{String(r.requests)}</td><td className="num">{String(r.clients)}</td><td className="num">{String(r.qty)}</td></tr>)}</tbody></table>)}
      {data && kind === "warranty" && (
        <table style={{ marginTop: 8 }}><thead><tr><th>Завод</th><th className="num">Претензий</th><th className="num">С ответом</th><th className="num">Признано, %</th><th className="num">Отказов</th><th className="num">Срок компенсации, дн</th><th className="num">Расход, ₽</th><th className="num">Компенсировано, ₽</th></tr></thead>
          <tbody>{rows().map((r) => <tr key={String(r.supplier_id)}><td>{String(r.supplier_name)}</td><td className="num">{String(r.claims)}</td><td className="num">{String(r.answered)}</td><td className="num">{r.accepted_pct != null ? String(r.accepted_pct) : "—"}</td>
            <td className="num">{String(r.rejected)}</td><td className="num">{r.avg_days_to_compensation != null ? String(r.avg_days_to_compensation) : "—"}</td><td className="num">{n(r.cost_rub)}</td><td className="num">{n(r.compensated_rub)}</td></tr>)}</tbody></table>)}
      {data && kind === "shipments" && (
        <table style={{ marginTop: 8 }}><thead><tr><th>Дата</th><th>Отгрузка</th><th>Заявка</th><th>Клиент</th><th>Склад</th><th>Артикул</th><th>Наименование</th><th className="num">Кол-во</th><th className="num">Сумма, ₽</th></tr></thead>
          <tbody>{rows().map((r, i) => <tr key={i}><td>{fmtDate(String(r.shipped_on))}</td><td>{String(r.number)}</td><td>{String(r.request_number)}</td><td>{String(r.client_name)}</td><td>{String(r.warehouse)}</td>
            <td>{String(r.article)}</td><td>{String(r.name)}</td><td className="num">{String(r.qty)}</td><td className="num">{n(r.sum_rub)}</td></tr>)}</tbody></table>)}
    </div>
  );
}
