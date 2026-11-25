import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../api";
import { Loading } from "./States";

// D-167 (ТЗ UX/UI пакет C, решение владельца 25.11): «План / факт по месяцам» и сравнение с прошлым периодом — только «Новый вид».
// Все суммы, отклонения и проценты считает сервер (/api/finance/planfact); здесь только показ. Валюты не смешиваются.

type Row = { period: string; plan: string; forecast: string; fact: string | null; fact_provisional: string | null; deviation: string | null; plan_count: number; fact_count: number };
type Series = { currency: string; direction: "in" | "out"; rows: Row[]; compare: { current: string; previous: string; delta: string; delta_pct: string | null } };
type Period = { key: string; title: string; from: string; to: string; state: "past" | "current" | "future" };
type PF = { as_of: string; periods: Period[]; series: Series[]; compare_window: { from: string; to: string; prev_from: string; prev_to: string }; definitions: Record<string, string> };
type Item = { obligation_id: string; transaction_id?: string; date: string; title: string; counterparty: string; amount: string; currency: string; forecast?: boolean; provisional?: boolean; deal_id: string | null; deal_number: string | null };
type Items = { items: Item[]; count: number; total: string };

const DIR = { in: "Поступления (нам платят)", out: "Платежи (мы платим)" } as const;
// высота столбика — от данных (доля от максимума); это не оформление, поэтому не класс
const hgt = (pct: number) => ({ height: `${Math.max(0, Math.min(100, pct))}%` });
const n = (v: string | null | undefined) => (v == null ? 0 : Number(v));
const signed = (v: string | null, c: string) => (v == null ? "—" : `${n(v) > 0 ? "+" : n(v) < 0 ? "−" : ""}${fmtMoney(String(Math.abs(n(v))), c)}`);

export default function FinancePlanFact({ stamp }: { stamp: number }) {
  const [d, setD] = useState<PF | null>(null);
  const [err, setErr] = useState("");
  const [ccy, setCcy] = useState("");
  const [dir, setDir] = useState<"in" | "out">("in");
  const [cell, setCell] = useState<{ period: string; kind: "plan" | "fact" } | null>(null);
  const [items, setItems] = useState<Items | null>(null);
  useEffect(() => { setErr(""); api.get<PF>("/api/finance/planfact").then(setD).catch((e) => setErr((e as Error).message)); }, [stamp]);
  const ccys = d ? [...new Set(d.series.map((s) => s.currency))] : [];
  const cur = ccys.includes(ccy) ? ccy : ccys.includes("RUB") ? "RUB" : ccys[0] || "";
  const s = d?.series.find((x) => x.currency === cur && x.direction === dir);
  useEffect(() => {
    if (!cell || !cur) { setItems(null); return; }
    setItems(null);
    api.get<Items>(`/api/finance/planfact/items?period=${cell.period}&currency=${cur}&direction=${dir}&kind=${cell.kind}`).then(setItems).catch((e) => setErr((e as Error).message));
  }, [cell, cur, dir]);
  if (err) return <div className="alert error">План / факт: {err}</div>;
  if (!d) return <Loading card text="Загружаю план / факт…" />;
  if (!d.series.length) return null;
  const per = (k: string) => d.periods.find((p) => p.key === k)!;
  const max = Math.max(1, ...(s?.rows.flatMap((r) => [n(r.plan) + n(r.forecast), n(r.fact) + n(r.fact_provisional)]) || [1]));
  const w = d.compare_window;
  const pick = (period: string, kind: "plan" | "fact") => setCell(cell && cell.period === period && cell.kind === kind ? null : { period, kind });
  return (
    <section className="card pf" aria-labelledby="pf-h">
      <div className="pf-head">
        <h2 id="pf-h">План / факт по месяцам</h2>
        <div className="pf-ctl">
          <div className="dc-groups" role="group" aria-label="Направление">
            {(["in", "out"] as const).map((k) => <button key={k} type="button" className={dir === k ? "on" : ""} aria-pressed={dir === k} onClick={() => { setDir(k); setCell(null); }}>{k === "in" ? "Поступления" : "Платежи"}</button>)}
          </div>
          {ccys.length > 1 && <select aria-label="Валюта" value={cur} onChange={(e) => { setCcy(e.target.value); setCell(null); }}>{ccys.map((c) => <option key={c}>{c}</option>)}</select>}
        </div>
      </div>
      {/* сравнение с прошлым периодом: одинаковые отрезки, одна методика */}
      <div className="pf-cmp">
        {(["in", "out"] as const).map((k) => {
          const x = d.series.find((y) => y.currency === cur && y.direction === k);
          if (!x) return null;
          const dl = n(x.compare.delta);
          return (
            <div key={k} className="pf-cmp-card">
              <span className="pf-cmp-l">{k === "in" ? "Поступило" : "Оплачено"} с {fmtDate(w.from)} по {fmtDate(w.to)}</span>
              <b>{fmtMoney(x.compare.current, cur)}</b>
              <span className="pf-cmp-prev">за {fmtDate(w.prev_from)}–{fmtDate(w.prev_to)}: {fmtMoney(x.compare.previous, cur)}</span>
              <span className={"pf-delta " + (dl > 0 ? "up" : dl < 0 ? "down" : "")}>{dl === 0 ? "без изменений" : `${dl > 0 ? "▲ больше на" : "▼ меньше на"} ${fmtMoney(String(Math.abs(dl)), cur)}`}
                {x.compare.delta_pct != null && ` (${dl > 0 ? "+" : "−"}${Math.abs(n(x.compare.delta_pct)).toLocaleString("ru-RU")} %)`}{x.compare.delta_pct == null && n(x.compare.previous) === 0 && dl !== 0 && " (в прошлом периоде оплат не было)"}</span>
            </div>);
        })}
      </div>
      {!s ? <p className="muted">По направлению «{DIR[dir]}» в {cur} обязательств и оплат за эти месяцы нет.</p> : <>
        <div className="pf-chart" role="img" aria-label={`${DIR[dir]}, ${cur}: план и факт по месяцам`}>
          {s.rows.map((r) => {
            const p = per(r.period);
            const plan = n(r.plan), fc = n(r.forecast), fact = n(r.fact), prov = n(r.fact_provisional);
            return (
              <div key={r.period} className={"pf-col " + p.state}>
                <div className="pf-bars">
                  <button type="button" className="pf-bar plan" title={`план ${fmtMoney(r.plan, cur)}${fc ? `, прогноз ${fmtMoney(r.forecast, cur)}` : ""} — показать обязательства`}
                    style={hgt(((plan + fc) / max) * 100)} onClick={() => pick(r.period, "plan")} disabled={!r.plan_count}>
                    {fc > 0 && <i className="pf-fc" style={hgt((fc / (plan + fc)) * 100)} />}</button>
                  {p.state === "future" ? <span className="pf-bar nofact" title="факта ещё нет" /> :
                    <button type="button" className="pf-bar fact" title={`факт ${fmtMoney(String(fact + prov), cur)}${prov ? `, из них временно ${fmtMoney(r.fact_provisional, cur)}` : ""} — показать оплаты`}
                      style={hgt(((fact + prov) / max) * 100)} onClick={() => pick(r.period, "fact")} disabled={!r.fact_count}>
                      {prov > 0 && <i className="pf-prov" style={hgt((prov / (fact + prov)) * 100)} />}</button>}
                </div>
                <span className="pf-lbl">{p.title.split(" ")[0].slice(0, 3)}{p.state === "current" ? " •" : ""}</span>
              </div>);
          })}
        </div>
        <div className="pf-legend"><span><i className="plan" />план</span><span><i className="fc" />прогноз по расчёту</span><span><i className="fact" />факт</span><span><i className="prov" />временно разнесено</span><span className="muted">• — текущий месяц, факт по {fmtDate(d.as_of)}; столбик открывает расшифровку</span></div>
        <div className="pf-tbl-wrap">
          <table className="pf-tbl">
            <thead><tr><th>Месяц</th><th className="num">План</th><th className="num">Прогноз</th><th className="num">Факт</th><th className="num">в т. ч. временно</th><th className="num">Отклонение</th></tr></thead>
            <tbody>{s.rows.map((r) => {
              const p = per(r.period);
              return (
                <tr key={r.period} className={p.state}>
                  <td>{p.title}{p.state === "current" && <span className="badge info">идёт</span>}</td>
                  <td className="num">{r.plan_count ? <button type="button" className="pf-link" aria-expanded={cell?.period === r.period && cell.kind === "plan"} onClick={() => pick(r.period, "plan")}>{fmtMoney(r.plan, cur)}</button> : <span className="muted">—</span>}</td>
                  <td className="num">{n(r.forecast) ? fmtMoney(r.forecast, cur) : <span className="muted">—</span>}</td>
                  <td className="num">{r.fact == null ? <span className="muted" title="месяц ещё не наступил">нет факта</span> : r.fact_count ? <button type="button" className="pf-link" aria-expanded={cell?.period === r.period && cell.kind === "fact"} onClick={() => pick(r.period, "fact")}>{fmtMoney(String(n(r.fact) + n(r.fact_provisional)), cur)}</button> : fmtMoney("0", cur)}</td>
                  <td className="num">{r.fact_provisional == null ? "—" : n(r.fact_provisional) ? fmtMoney(r.fact_provisional, cur) : <span className="muted">—</span>}</td>
                  <td className={"num " + (r.deviation == null ? "" : n(r.deviation) < 0 ? "bad" : n(r.deviation) > 0 ? "ok" : "")}>{signed(r.deviation, cur)}</td>
                </tr>);
            })}</tbody>
          </table>
        </div>
        {cell && (
          <div className="pf-items" aria-live="polite">
            <div className="pf-items-h"><b>{cell.kind === "plan" ? "Обязательства плана" : "Оплаты (разнесения)"} · {per(cell.period).title} · {cur}</b>
              <button type="button" className="btn ghost" onClick={() => setCell(null)}>Скрыть</button></div>
            {!items ? <Loading /> : items.items.length === 0 ? <p className="muted">Нет записей.</p> : (
              <table className="pf-tbl"><thead><tr><th>{cell.kind === "plan" ? "Срок" : "Дата оплаты"}</th><th>Контрагент</th><th>Обязательство</th><th>Сделка</th><th className="num">Сумма</th></tr></thead>
                <tbody>{items.items.map((i) => (
                  <tr key={(i.transaction_id || "") + i.obligation_id}>
                    <td>{fmtDate(i.date)}</td><td>{i.counterparty}</td>
                    <td><Link to={`/finance?tab=calendar&open=${i.obligation_id}`}>{i.title}</Link>{i.forecast && <span className="badge info">прогноз</span>}{i.provisional && <span className="badge warn">временно</span>}</td>
                    <td>{i.deal_id ? <Link to={`/deals/${i.deal_id}`}>{i.deal_number || "сделка"}</Link> : <span className="muted">—</span>}</td>
                    <td className="num">{fmtMoney(i.amount, i.currency)}</td>
                  </tr>))}</tbody>
                <tfoot><tr><td colSpan={4}>Итого{items.count > items.items.length ? ` (показано ${items.items.length} из ${items.count})` : ""}</td><td className="num"><b>{fmtMoney(items.total, cur)}</b></td></tr></tfoot>
              </table>)}
          </div>)}
      </>}
      <details className="pf-defs"><summary>Как считается</summary><ul>{Object.entries(d.definitions).map(([k, t]) => <li key={k}>{t}</li>)}</ul></details>
    </section>
  );
}
