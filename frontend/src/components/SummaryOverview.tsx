import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, fmtRub } from "../api";
import { Empty, ErrorState, Loading } from "./States";
import { useTableLabels } from "./useTableLabels";

// D-175 (пакет A2, согласовано владельцем 26.11): «Сводка → Показатели» — только «Новый вид».
// Все числа, сравнения и отклонения считает сервер (/api/summary/overview); здесь — только показ. Валюты не смешиваются.

type Kpi = { key: string; title: string; unit: string; current: number | null; previous: number | null; delta: number | null; delta_pct: number | null;
  url?: string; definition: string; coverage?: string; bad_up?: boolean; finance?: boolean };
type Bucket = { key: string; title: string; from: string; to: string; future: boolean; created: number | null; won: number | null; lost: number | null; won_sum: number | null };
type StructRow = { code: string; title: string; equipment: number; parts: number; total: number };
type TRow = { key: string; title: string; unit: string; plan: number | null; fact: number | null; deviation: number | null; note: string; source: string; url: string };
type Dev = { stage: string; deal_id: string; deal_number: string; client: string; plan: string; refined: string | null; fact: string | null; days: number; responsible: string; url: string };
type Data = {
  kind: "month" | "quarter" | "year"; direction: string; directions: Record<string, string>; finance: boolean; today: string;
  period: { from: string; to: string; title: string; closed: boolean; future: boolean; to_date: string };
  previous: { from: string; to: string; title: string; compare_to: string };
  kpis: Kpi[]; series: Bucket[]; structure: StructRow[]; table: TRow[]; deviations: Dev[]; deviations_total: number; definitions: Record<string, string>;
};

const KIND_T = { month: "Месяц", quarter: "Квартал", year: "Год" } as const;
// высота/ширина столбика — от данных (доля от максимума); это не оформление, поэтому не класс
const hgt = (pct: number) => ({ height: `${Math.max(0, Math.min(100, pct))}%` });
const wdt = (pct: number) => ({ width: `${Math.max(0, Math.min(100, pct))}%` });
const num = (v: number | null, unit: string) => v == null ? "нет данных" : unit === "₽ без НДС" ? `${fmtRub(v)} ₽` : unit === "%" ? `${String(v).replace(".", ",")} %` : String(v);
const money = (v: number | null, unit: string) => v == null ? "—" : unit && unit.length === 3 ? fmtMoney(String(v), unit) : String(v);
const signed = (v: number | null, unit: string) => v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v), unit)}`;
function shift(d: string, kind: Data["kind"], k: number): string {
  const x = new Date(d + "T00:00:00");
  if (kind === "year") x.setFullYear(x.getFullYear() + k);
  else x.setMonth(x.getMonth() + (kind === "quarter" ? 3 * k : k));
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-01`;
}

export default function SummaryOverview() {
  const [sp, setSp] = useSearchParams();
  const kind = (["month", "quarter", "year"].includes(sp.get("p") || "") ? sp.get("p") : "quarter") as Data["kind"];
  const at = sp.get("at") || "";
  const dir = sp.get("dir") || "all";
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  const load = () => {
    setErr(""); setLoading(true);
    api.get<Data>(`/api/summary/overview?kind=${kind}&direction=${dir}${at ? `&anchor=${at}` : ""}`).then(setD).catch((e) => setErr((e as Error).message)).finally(() => setLoading(false));
  };
  useEffect(load, [kind, at, dir]);   // eslint-disable-line react-hooks/exhaustive-deps
  const tRef = useRef<HTMLTableElement>(null);
  const dRef = useRef<HTMLTableElement>(null);
  useTableLabels(tRef, true);
  useTableLabels(dRef, true);
  if (err && !d) return <ErrorState title="Показатели не загрузились" hint={err} onRetry={load} />;
  if (!d) return <Loading card text="Считаю показатели…" />;

  const p = d.period;
  const maxWon = Math.max(1, ...d.series.map((b) => Math.max(b.won || 0, b.created || 0)));
  const peak = d.series.filter((b) => !b.future && (b.won || 0) > 0).sort((x, y) => (y.won || 0) - (x.won || 0))[0];
  const maxSt = Math.max(1, ...d.structure.map((s) => s.total));
  const nowFut = !p.future && !p.closed;
  return (
    <div className={"so" + (loading ? " so-loading" : "")}>
      <div className="card so-params" role="group" aria-label="Параметры отчёта">
        <div className="dc-groups" role="group" aria-label="Длина периода">
          {(Object.keys(KIND_T) as Data["kind"][]).map((k) => <button key={k} type="button" className={kind === k ? "on" : ""} aria-pressed={kind === k} onClick={() => set("p", k)}>{KIND_T[k]}</button>)}
        </div>
        <div className="so-nav">
          <button type="button" className="btn ghost" aria-label="Предыдущий период" onClick={() => set("at", shift(p.from, kind, -1))}>‹</button>
          <b className="so-title">{p.title}</b>
          <button type="button" className="btn ghost" aria-label="Следующий период" onClick={() => set("at", shift(p.from, kind, 1))}>›</button>
          {at && <button type="button" className="btn ghost so-now" onClick={() => set("at", "")}>текущий</button>}
        </div>
        <label className="so-dir">Направление
          <select value={dir} onChange={(e) => set("dir", e.target.value === "all" ? "" : e.target.value)}>
            {Object.entries(d.directions).map(([k, t]) => <option key={k} value={k}>{t}</option>)}
          </select>
        </label>
        <span className="muted so-asof">{p.future ? "период ещё не начался" : p.closed ? `${fmtDate(p.from)} – ${fmtDate(p.to)}` : `с ${fmtDate(p.from)} по сегодня, ${fmtDate(p.to_date)}`}</span>
      </div>

      {p.future ? <Empty title="Период ещё не начался" hint="Показатели появятся, когда в нём будут события. Выберите текущий или прошедший период." /> : <>
        <section className="so-kpis" aria-label="Ключевые показатели">
          {d.kpis.map((k) => {
            const up = (k.delta || 0) > 0, dn = (k.delta || 0) < 0;
            const tone = k.delta == null || k.delta === 0 ? "" : (up !== !!k.bad_up) ? "good" : "bad";
            const body = <>
              <span className="so-kt">{k.title}</span>
              <b className="so-kv">{num(k.current, k.unit)}{k.unit && k.unit !== "%" && k.unit !== "₽ без НДС" && k.current != null && <small> {k.unit}</small>}</b>
              <span className={"so-kc " + tone}>
                {k.previous == null || k.current == null ? "сравнения нет" : k.delta === 0 ? `как в ${d.previous.title}` :
                  <>{up ? "▲ +" : dn ? "▼ −" : ""}{k.unit === "₽ без НДС" ? `${fmtRub(Math.abs(k.delta || 0))} ₽` : String(Math.abs(k.delta || 0)).replace(".", ",")}{k.unit === "%" ? " п.п." : ""}{k.delta_pct != null && k.unit !== "%" ? ` (${k.delta_pct > 0 ? "+" : ""}${String(k.delta_pct).replace(".", ",")} %)` : ""} · было {num(k.previous, k.unit)}</>}
              </span>
              {k.coverage && <span className="so-kn">{k.coverage}</span>}
              <span className="so-kd">{k.definition}</span>
            </>;
            return k.url ? <Link key={k.key} to={k.url} className="card so-kpi" title={k.definition}>{body}</Link> : <div key={k.key} className="card so-kpi" title={k.definition}>{body}</div>;
          })}
        </section>
        <p className="muted so-cmp">Сравнение: {nowFut ? `${p.title} по сегодня против того же числа дней — ${fmtDate(d.previous.from)}–${fmtDate(d.previous.compare_to)}` : `${p.title} против ${d.previous.title}`}. {d.definitions.parts_dates}</p>

        <div className="so-grid">
          <section className="card so-chart" aria-label="Динамика за период">
            <h2>Динамика: новые и выигранные</h2>
            <div className="so-legend"><span className="so-sw created" /> новые <span className="so-sw won" /> выигранные</div>
            <div className="so-bars" role="img" aria-label={peak ? `Больше всего выиграно: ${peak.title} — ${peak.won}` : "Выигранных в периоде нет"}>
              {d.series.map((b) => (
                <div key={b.key} className={"so-col" + (b.future ? " future" : "")} title={b.future ? `${b.title}: ещё не наступило` : `${b.title}: новых ${b.created}, выиграно ${b.won}, проиграно ${b.lost}`}>
                  <div className="so-pair">
                    <span className="so-bar created" style={hgt(((b.created || 0) / maxWon) * 100)} />
                    <span className="so-bar won" style={hgt(((b.won || 0) / maxWon) * 100)} />
                  </div>
                  <span className="so-x">{b.title}</span>
                  <span className="so-y">{b.future ? "—" : `${b.created}/${b.won}`}</span>
                </div>))}
            </div>
            <p className="so-say">{peak ? `Больше всего выиграно — ${peak.title}: ${peak.won}${peak.won_sum != null ? ` на ${fmtRub(peak.won_sum)}` : ""}.` : "В периоде нет выигранных сделок."}</p>
          </section>
          <section className="card so-struct" aria-label="Структура: открытые сейчас по этапам">
            <h2>Открытые сейчас по этапам</h2>
            {d.structure.every((s) => !s.total) ? <p className="muted">Открытых сделок нет.</p> :
              <ul>{d.structure.map((s) => <li key={s.code}>
                <span className="so-st">{s.title}</span>
                <span className="so-track"><span className="so-fill" style={wdt((s.total / maxSt) * 100)} /></span>
                <b>{s.total}</b>{s.parts > 0 && <small className="muted"> ЗЧ {s.parts}</small>}
              </li>)}</ul>}
            <p className="muted so-note">Снимок на сегодня, от периода не зависит.</p>
          </section>
        </div>

        <section className="card so-table" aria-label="План, факт и отклонение">
          <h2>План / факт / отклонение</h2>
          <table ref={tRef} className="nv-cards">
            <thead><tr><th>Показатель</th><th>План</th><th>Факт</th><th>Отклонение</th><th>Пояснение</th><th>Источник</th></tr></thead>
            <tbody>{d.table.map((r) => <tr key={r.key}>
              <td><b>{r.title}</b></td>
              <td className="num">{r.plan == null ? "не задан" : money(r.plan, r.unit)}</td>
              <td className="num">{money(r.fact, r.unit)}</td>
              <td className={"num " + (r.deviation == null || r.key.startsWith("money_out") ? "" : r.deviation < 0 ? "so-neg" : r.deviation > 0 ? "so-pos" : "")}>{signed(r.deviation, r.unit)}</td>
              <td className="muted">{r.note || "—"}</td>
              <td>{r.url ? <Link to={r.url}>{r.source || "открыть"}</Link> : r.source || "—"}</td>
            </tr>)}</tbody>
          </table>
        </section>

        <section className="card so-devs" aria-label="Отклонения по сделкам">
          <h2>Отклонения по этапам исполнения <span className="muted">{d.deviations_total ? `· ${d.deviations_total}` : ""}</span></h2>
          {!d.deviations.length ? <p className="muted">В периоде нет этапов с отклонением от базового плана.</p> :
            <table ref={dRef} className="nv-cards">
              <thead><tr><th>Этап</th><th>Сделка</th><th>План</th><th>Факт</th><th>Отклонение</th><th>Ответственный</th></tr></thead>
              <tbody>{d.deviations.map((r, i) => <tr key={i}>
                <td><Link to={r.url}>{r.stage}</Link></td>
                <td>{r.deal_number}<div className="muted">{r.client}</div></td>
                <td>{fmtDate(r.plan)}{r.refined && <div className="muted">уточнено {fmtDate(r.refined)}</div>}</td>
                <td>{r.fact ? fmtDate(r.fact) : <span className="badge bad">нет факта</span>}</td>
                <td className="num so-neg">+{r.days} дн.</td>
                <td>{r.responsible || "—"}</td>
              </tr>)}</tbody>
            </table>}
          {d.deviations_total > d.deviations.length && <p className="muted">Показаны первые {d.deviations.length}; остальные — в сделках с фильтром «просрочено».</p>}
        </section>
      </>}
    </div>);
}
