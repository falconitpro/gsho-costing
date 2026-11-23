import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { Me } from "../../types";

// Рабочее место ЗГД, новый вид «Пульт» (D-153; ТЗ «Рабочее место ЗГД: интерфейс» v1, первый релиз PR-1…PR-3).
// Данные — GET /api/workplace/executive: те же списки, что у прежнего рабочего места, плюс блок executive.
// Чего в системе нет (план продаж, оргструктура, управленческие поручения с приёмкой), того нет и на экране:
// направления — по ролям сотрудников, поручения — резолюции Канцелярии с контролем исполнения. Неизвестное — «нет данных», не 0.

type Kpi = { key: string; title: string; value: number | string | null; sub: string; url: string; tone: string; data_quality: "confirmed" | "partial" | "unavailable"; period: string; source: string };
type Esc = { key: string; priority: "critical" | "high" | "normal"; title: string; reason: string; impact: { amount: number; currency: string } | null; owner: string; owner_role: string;
  next_action: string; url: string; due: string | null; days: number; source: string };
type Dir = { key: string; title: string; people: string[]; tone: string; lines: string[]; url: string };
type Asg = { open: number; overdue: number; today: number; items: { number: string; subject: string; executor: string; due_on: string; overdue: boolean; url: string }[]; url: string; create_url: string; source: string };
type Exec = { meta: { generated_at: string; today: string; period: string; timezone: string }; kpis: Kpi[]; escalations: Esc[]; directions: Dir[]; assignments: Asg };
type Approval = { kind: string; title: string; sub: string; url: string; waiting_since: string | null; tone: string };
type Ev = { on: string; title: string; sub: string; tone: string; url: string };
type Data = {
  user: { name: string; first_name: string; role_title: string; greeting: string };
  date: { iso: string; text: string };
  approvals: Approval[]; upcoming: Ev[]; quick: { title: string; url: string }[];
  money: { curve: { week: string; label: string; in: number; out: number; net: number; cum: number | null }[]; min_cum: number | null; min_week: string; balance_status?: string; url?: string } | null;
  legal: Record<string, number> | null;
  executive: Exec;
};

const PRIO: Record<Esc["priority"], [string, string]> = { critical: ["Критично", "bad"], high: ["Высокий", "warn"], normal: ["Обычный", "info"] };
const SIGN: Record<string, string> = { RUB: "₽", CNY: "¥", USD: "$", EUR: "€" };
const money = (v: number, ccy = "RUB") => {
  const s = SIGN[ccy] || ccy;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1).replace(".", ",")} млн ${s}`;
  if (Math.abs(v) >= 1e3) return `${Math.round(v / 1e3).toLocaleString("ru-RU")} тыс. ${s}`;
  return `${Math.round(v).toLocaleString("ru-RU")} ${s}`;
};
const ddmm = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;

export default function ExecWorkplace({ me, onClassic }: { me: Me; onClassic: () => void }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"overview" | "ops">("overview");
  const [prio, setPrio] = useState<"" | Esc["priority"]>("");
  const nav = useNavigate();
  useEffect(() => { api.get<Data>("/api/workplace/executive").then(setD).catch((e: Error) => setErr(String(e?.message || e))); }, [me.id]);
  const esc = useMemo(() => (d?.executive.escalations || []).filter((e) => !prio || e.priority === prio), [d, prio]);
  if (err) return <div className="wpx"><div className="card"><b>Пульт не загрузился.</b> <span className="muted">{err}</span> <button className="btn ghost" onClick={onClassic}>Обычный вид</button></div></div>;
  if (!d) return <div className="wpx"><div className="wpx-kpis">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="wpx-kpi wpx-skel" />)}</div></div>;
  const x = d.executive;
  const counts = { critical: 0, high: 0, normal: 0 } as Record<Esc["priority"], number>;
  x.escalations.forEach((e) => { counts[e.priority] += 1; });

  const escRow = (e: Esc) => (
    <li key={e.key} className={`wpx-esc ${PRIO[e.priority][1]}`}>
      <span className={`wpx-prio ${PRIO[e.priority][1]}`}>{PRIO[e.priority][0]}</span>
      <div className="wpx-esc-main">
        <Link to={e.url} className="wpx-esc-title">{e.title}</Link>
        <div className="muted">{e.reason}</div>
        <div className="wpx-esc-meta">
          {e.impact && <span><b>{money(e.impact.amount, e.impact.currency)}</b></span>}
          <span>ответственный: {e.owner || "не назначен"}{e.owner_role && e.owner !== "вы" && !e.owner.includes("(") ? ` (${e.owner_role})` : ""}</span>
          <span>дальше: {e.next_action}</span>
        </div>
      </div>
      <Link className="btn ghost wpx-open" to={e.url}>Открыть</Link>
    </li>);

  const header = (
    <header className="wpx-head">
      <div>
        <h1>{d.user.greeting}, {d.user.first_name || d.user.name}</h1>
        <div className="muted">{d.user.role_title} · {d.date.text} · период показателей: {x.meta.period}</div>
      </div>
      <div className="wpx-head-act">
        <div className="wpx-switch" role="group" aria-label="Вид рабочего места">
          <button onClick={onClassic}>Обычный вид</button><button className="cur" aria-pressed="true">Пульт</button>
        </div>
        <button className="btn add" onClick={() => nav(x.assignments.create_url)} title="Поручение оформляется резолюцией в Канцелярии">Создать поручение</button>
      </div>
    </header>);

  const tabs = (
    <nav className="wpx-tabs" aria-label="Разделы пульта">
      <button className={tab === "overview" ? "cur" : ""} onClick={() => setTab("overview")}>Обзор</button>
      <button className={tab === "ops" ? "cur" : ""} onClick={() => setTab("ops")}>Оперативно{x.escalations.length ? ` · ${x.escalations.length}` : ""}</button>
      <Link to="/summary">Аналитика</Link>
    </nav>);

  const kpis = (
    <section className="wpx-kpis" aria-label="Показатели компании">
      {x.kpis.map((k) => (
        <Link key={k.key} to={k.url} className={`wpx-kpi ${k.tone}`} title={`Источник: ${k.source}${k.period ? ` · ${k.period}` : ""}`}>
          <small>{k.title}</small>
          <b>{k.value === null || k.value === undefined ? "нет данных" : k.value}</b>
          <span>{k.sub}</span>
          {k.data_quality !== "confirmed" && <i className="wpx-q">{k.data_quality === "partial" ? "данные частичные" : "источник не заполнен"}</i>}
        </Link>))}
    </section>);

  if (tab === "ops") {
    return (
      <div className="wpx">
        {header}{tabs}
        <section className="card">
          <div className="wpx-h"><h2>Требует вмешательства</h2>
            <div className="wpx-chips">
              {([["", `все ${x.escalations.length}`], ["critical", `критично ${counts.critical}`], ["high", `высокий ${counts.high}`], ["normal", `обычный ${counts.normal}`]] as const).map(([k, t]) => (
                <button key={k} className={prio === k ? "cur" : ""} onClick={() => setPrio(k)}>{t}</button>))}
            </div></div>
          {esc.length ? <ul className="wpx-list">{esc.map(escRow)}</ul> : <p className="muted">Ничего не требует вмешательства.</p>}
          <p className="muted wpx-note">Очередь собирается из сделок, этапов исполнения, «Финансов», согласований, юр. блока и Канцелярии; один источник — одна строка. Закрывается в исходной карточке.</p>
        </section>
      </div>);
  }

  const a = x.assignments;
  const mx = Math.max(1, ...(d.money?.curve || []).map((c) => Math.max(c.in, c.out)));
  return (
    <div className="wpx">
      {header}{tabs}{kpis}
      <section className="wpx-grid">
        <div className="card wpx-wide">
          <div className="wpx-h"><h2>Требует моего вмешательства {x.escalations.length > 0 && <span className="wpx-count">{x.escalations.length}</span>}</h2>
            {x.escalations.length > 5 && <button className="btn ghost" onClick={() => setTab("ops")}>Все {x.escalations.length} →</button>}</div>
          {x.escalations.length ? <ul className="wpx-list">{x.escalations.slice(0, 5).map(escRow)}</ul> : <p className="muted">Ничего не требует вмешательства.</p>}
        </div>
        <div className="card">
          <div className="wpx-h"><h2>Поручения</h2><Link to={a.url}>На контроле →</Link></div>
          <div className="wpx-asg">
            <div><b>{a.open}</b><small>в работе</small></div>
            <div className={a.overdue ? "bad" : ""}><b>{a.overdue}</b><small>просрочено</small></div>
            <div><b>{a.today}</b><small>срок сегодня</small></div>
          </div>
          <ul className="wpx-mini">{a.items.map((i) => (
            <li key={i.url}><Link to={i.url}>{i.subject}</Link><span className={i.overdue ? "bad" : "muted"}>{i.executor} · до {ddmm(i.due_on)}</span></li>))}
            {!a.items.length && <li className="muted">Поручений со сроком нет.</li>}</ul>
          <p className="muted wpx-note">Источник: {a.source}. Отдельных управленческих поручений с приёмкой результата в системе пока нет.</p>
        </div>
        <div className="card wpx-wide">
          <div className="wpx-h"><h2>Направления</h2><span className="muted">по ролям сотрудников</span></div>
          <table className="wpx-dirs"><thead><tr><th>Направление</th><th>Сотрудники</th><th>Состояние</th><th></th></tr></thead>
            <tbody>{x.directions.map((r) => (
              <tr key={r.key} className={r.tone}>
                <td><b>{r.title}</b></td><td className="muted">{r.people.join(", ") || "не назначены"}</td>
                <td>{r.lines.map((l) => <div key={l}>{l}</div>)}</td>
                <td><span className={`wpx-dot ${r.tone}`} aria-label={r.tone === "bad" ? "есть просрочки" : r.tone === "warn" ? "требует внимания" : "в норме"}>{r.tone === "bad" ? "просрочки" : r.tone === "warn" ? "внимание" : "норма"}</span> <Link to={r.url}>→</Link></td>
              </tr>))}</tbody></table>
        </div>
        <div className="card">
          <div className="wpx-h"><h2>Денежный поток</h2>{d.money?.url && <Link to={d.money.url}>Финансы →</Link>}</div>
          {d.money ? <>
            <svg className="wpx-bars" viewBox={`0 0 ${d.money.curve.length * 40} 110`} role="img" aria-label="Поступления и платежи по неделям, рубли">
              {d.money.curve.map((c, i) => (
                <g key={c.week}><title>{`${c.label}: поступления ${money(c.in)}, платежи ${money(c.out)}`}</title>
                  <rect className="in" x={i * 40 + 6} y={90 - 80 * c.in / mx} width="12" height={80 * c.in / mx} />
                  <rect className="out" x={i * 40 + 20} y={90 - 80 * c.out / mx} width="12" height={80 * c.out / mx} />
                  <text x={i * 40 + 19} y="104" textAnchor="middle">{c.label}</text></g>))}
            </svg>
            <div className="wpx-legend"><span><i className="in" />поступления</span><span><i className="out" />платежи</span><span className="muted">RUB, 8 недель</span></div>
            <div className="muted wpx-note">{d.money.balance_status === "confirmed" && d.money.min_cum !== null ? `минимум прогнозного остатка ${money(d.money.min_cum)} — ${d.money.min_week}` : "остаток на счетах не подтверждён — прогноз остатка не показывается"}</div>
          </> : <p className="muted">Нет доступа к финансам.</p>}
        </div>
        <div className="card">
          <div className="wpx-h"><h2>Согласования</h2><span className="muted">{d.approvals.length}</span></div>
          <ul className="wpx-mini">{d.approvals.slice(0, 5).map((p) => <li key={p.url + p.title}><Link to={p.url}>{p.title}</Link><span className="muted">{p.sub}</span></li>)}
            {!d.approvals.length && <li className="muted">Решений не ждёт.</li>}</ul>
        </div>
        <div className="card">
          <div className="wpx-h"><h2>Юридический блок</h2><Link to="/legal">Открыть →</Link></div>
          {d.legal ? <ul className="wpx-mini">
            <li><Link to="/legal?tab=contracts&flag=no_scan">Договоры без скана</Link><b>{d.legal.contracts_no_scan}</b></li>
            <li><Link to="/legal?tab=contracts">На проверке и подписи</Link><b>{(d.legal.contracts_review || 0) + (d.legal.contracts_signing || 0)}</b></li>
            <li><Link to="/legal?tab=contracts&flag=expiring">Истекают в 30 дней</Link><b>{d.legal.contracts_expiring}</b></li>
            <li><Link to="/legal?tab=claims">Претензии открыты / срок ответа истёк</Link><b>{d.legal.claims_open ?? 0} / {d.legal.claims_answer_overdue ?? 0}</b></li>
          </ul> : <p className="muted">Нет доступа.</p>}
        </div>
        <div className="card">
          <div className="wpx-h"><h2>Ближайшие сроки</h2></div>
          <ul className="wpx-mini">{d.upcoming.slice(0, 5).map((e) => <li key={e.url + e.on + e.title}><Link to={e.url}>{e.title}</Link><span className="muted">{ddmm(e.on)} · {e.sub}</span></li>)}
            {!d.upcoming.length && <li className="muted">В ближайшие 14 дней сроков нет.</li>}</ul>
        </div>
      </section>
      <section className="wpx-quick" aria-label="Быстрые действия"><b>Быстрые действия</b>
        {d.quick.map((q) => <Link key={q.url} className="btn ghost" to={q.url}>{q.title}</Link>)}
      </section>
      <p className="muted wpx-note">Обновлено {new Date(x.meta.generated_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК. Каждая цифра открывает список с теми же условиями; подсказка на карточке — источник.</p>
    </div>);
}
