import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { Me } from "../../types";
import SecurityModal from "../SecurityModal";
import AssignmentDialog, { type AsgPrefill } from "./AssignmentDialog";
import DirectionsDialog from "./DirectionsDialog";
import { Loading } from "../States";
import RecentActivity from "./RecentActivity";
import { useNewUiFlag } from "../../uiMode";

// Рабочее место ЗГД, вид «Пульт» (D-153; v2 — D-154 по ТЗ «Berghaus CRM 2.0»).
// Данные — GET /api/workplace/executive. KPI: единица, период, время расчёта, качество данных, расшифровка тем же условием.
// Очередь вмешательства → «Поручить» / «Запросить пояснение» (поручения ЗГД, /assignments). Направления — руководитель из оргструктуры.
// Неизвестное — «нет данных» с причиной, не 0.

type Quality = "confirmed" | "partial" | "stale" | "unavailable" | "unverified";
type Kpi = { key: string; title: string; value: number | string | null; sub: string; url: string; tone: string; data_quality: Quality; quality_note: string; period: string; source: string;
  unit: string; currency: string; calculated_at: string; drilldown: { url: string; list: string | null }; extra: { label: string; value: number }[];
  method: string; reference: { amount: number; text: string; rates: { currency: string; rate: number; on: string }[] } | null };
type Prio = "critical" | "high" | "normal" | "info";
type Esc = { key: string; group: string; priority: Prio; title: string; reason: string; impact: { amount: number; currency: string; rub_ref?: string } | null; owner: string; owner_role: string; head: string; direction: string;
  object_type: string; object_number: string; next_action: string; url: string; due: string | null; days: number; source: string; react_by: string | null;
  assignment: { number: string; status: string; url: string } | null };
type Head = { id: string; name: string; position: string; deputy: { id: string; name: string; temporary: boolean; to: string | null } | null } | null;
type Dir = { key: string; title: string; head: Head; people: string[]; staff_count: number; tone: string; results: string[]; deviations: string[]; url: string; card_url: string };
type AsgItem = { id: string; number: string; title: string; assignee: string; executor: string; due_on: string; overdue: boolean; status: string; status_title: string; priority: string; priority_title: string };
type Asg = { open: number; overdue: number; today: number; review: number; items: AsgItem[]; url: string; create_url: string; source: string; office: { open: number; overdue: number; url: string } };
type Drill = { title: string; footer?: string; rows: { title: string; url: string; value: string | null; note: string }[] } | null;
type Deadline = { on: string; title: string; sub: string; type: string; who: string; url: string; urgency: "overdue" | "today" | "soon" | "later" };
type SigHist = { key: string; priority: Prio; title: string; object_type: string; object_number: string; direction: string; url: string; first_seen: string; resolved_at: string;
  hours: number; times: number; assignment: { id: string; number: string; status: string } | null };
type Exec = { meta: { generated_at: string; today: string; period: string; timezone: string; quality_titles: Record<Quality, string> }; kpis: Kpi[]; escalations: Esc[];
  directions: Dir[]; assignments: Asg; drill: Record<string, Drill>; can_assign: boolean; deadlines: Deadline[] };
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

const PRIO: Record<Prio, [string, string]> = { critical: ["Критично", "bad"], high: ["Высокий", "warn"], normal: ["Обычный", "info"], info: ["Информационно", "muted"] };
const PR_CLS: Record<string, string> = { critical: "bad", high: "warn", normal: "info" };
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
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
  const [prio, setPrio] = useState<"" | Prio>("");
  const [drill, setDrill] = useState<Kpi | null>(null);
  const [asg, setAsg] = useState<AsgPrefill | null>(null);
  const [dirs, setDirs] = useState(false);
  const [hist, setHist] = useState<SigHist[] | null>(null);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const nav = useNavigate();
  const nv = useNewUiFlag();
  const load = () => api.get<Data>("/api/workplace/executive").then(setD).catch((e: Error) => setErr(String(e?.message || e)));
  useEffect(() => { load(); }, [me.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "ops" && hist === null) api.get<{ items: SigHist[] }>("/api/management/signals/history").then((r) => setHist(r.items)).catch(() => setHist([])); }, [tab, hist]);
  const esc = useMemo(() => (d?.executive.escalations || []).filter((e) => !prio || e.priority === prio), [d, prio]);
  if (err) return <div className="wp-exec"><div className="card"><b>Пульт не загрузился.</b> <span className="muted">{err}</span> <button className="btn ghost" onClick={onClassic}>Обычный вид</button></div></div>;
  if (!d) return <div className="wp-exec"><div className="wp-exec-kpis">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="wp-exec-kpi wp-exec-skel" />)}</div></div>;
  const x = d.executive;
  const qt = x.meta.quality_titles;
  const counts = { critical: 0, high: 0, normal: 0, info: 0 } as Record<Prio, number>;
  x.escalations.forEach((e) => { counts[e.priority] += 1; });
  const fromEsc = (e: Esc, kind: "task" | "explain"): AsgPrefill => ({
    kind, priority: e.priority === "info" ? "normal" : e.priority, source_url: e.url, source_key: e.key,
    source_title: e.object_number && e.title.includes(e.object_number) ? `${e.object_type}: ${e.title}` : `${e.object_type}${e.object_number ? ` ${e.object_number}` : ""}: ${e.title}`,
    expected_result: kind === "explain" ? "Письменно: причина, что сделано, новый срок устранения" : `Причина сигнала устранена: ${e.reason}`.slice(0, 500),
    title: kind === "explain" ? `Пояснить: ${e.reason}`.slice(0, 250) : `${e.next_action}: ${e.title}`.slice(0, 250) });

  const escRow = (e: Esc) => (
    <li key={e.key} className={`wp-exec-esc ${PRIO[e.priority][1]}`}>
      <span className={`wp-exec-prio ${PRIO[e.priority][1]}`}>{PRIO[e.priority][0]}</span>
      <div className="wp-exec-esc-main">
        <div className="wp-exec-esc-obj">{e.object_type}{e.object_number ? ` ${e.object_number}` : ""}{e.direction ? ` · ${e.direction}` : ""}</div>
        <Link to={e.url} className="wp-exec-esc-title">{e.title}</Link>
        <div className="muted">{e.reason}</div>
        <div className="wp-exec-esc-meta">
          <span>{e.impact ? <><b>{money(e.impact.amount, e.impact.currency)}</b>{e.impact.rub_ref ? <span className="muted"> ({e.impact.rub_ref})</span> : null}</> : <span className="muted">сумма не определена</span>}</span>
          <span>ответственный: {e.owner || "не назначен"}{e.owner_role && e.owner !== "вы" && !e.owner.includes("(") ? ` (${e.owner_role})` : ""}{e.head ? ` · руководитель: ${e.head}` : ""}</span>
          <span>дальше: {e.next_action}</span>
        </div>
        {e.assignment && <div className="wp-exec-esc-asg">поручение <Link to={e.assignment.url}>{e.assignment.number}</Link> · {e.assignment.status}</div>}
      </div>
      <div className="wp-exec-esc-act">
        <Link className="btn ghost" to={e.url}>Открыть</Link>
        {x.can_assign && !e.assignment && <button className="btn ghost" onClick={() => setAsg(fromEsc(e, "task"))}>Поручить</button>}
        {x.can_assign && !e.assignment && <button className="btn ghost" onClick={() => setAsg(fromEsc(e, "explain"))}>Запросить пояснение</button>}
      </div>
    </li>);

  // разные проблемы одного объекта (сделки) — одна группа: первая (самая важная) видна, остальные — по «ещё N» (DEV-07)
  const grouped = (list: Esc[]) => {
    const order: string[] = []; const by: Record<string, Esc[]> = {};
    list.forEach((e) => { const g = e.group || e.key; if (!by[g]) { by[g] = []; order.push(g); } by[g].push(e); });
    return order.map((g) => ({ g, items: by[g] }));
  };
  const groupRows = (list: Esc[], limit?: number) => grouped(list).slice(0, limit).map(({ g, items }) => (
    <li key={g} className="wp-exec-grp">
      <ul className="wp-exec-list">{escRow(items[0])}{opened[g] && items.slice(1).map(escRow)}</ul>
      {items.length > 1 && <button className="wp-exec-more" onClick={() => setOpened({ ...opened, [g]: !opened[g] })}>
        {opened[g] ? "свернуть" : `ещё ${items.length - 1} ${items.length - 1 === 1 ? "проблема" : "проблемы"} по ${items[0].object_type === "сделка" ? "этой сделке" : "этому объекту"}`}</button>}
    </li>));
  const groupsCount = grouped(x.escalations).length;

  const header = (
    <header className="wp-exec-head">
      <div>
        <h1>{d.user.greeting}, {d.user.first_name || d.user.name}</h1>
        <div className="muted">{d.user.role_title} · {d.date.text} · период показателей: {x.meta.period}{nv && <> · <span className="wp-exec-upd">обновлено {hhmm(x.meta.generated_at)} МСК</span></>}</div>
      </div>
      <div className="wp-exec-head-act">
        <div className="wp-exec-switch" role="group" aria-label="Вид рабочего места">
          <button onClick={onClassic}>Обычный вид</button><button className="cur" aria-pressed="true">Пульт</button>
        </div>
        {x.can_assign && <button className="btn add" onClick={() => setAsg({})}>Создать поручение</button>}
      </div>
    </header>);

  const quick = (
    <section className="wp-exec-quick" aria-label="Быстрые действия">
      {d.quick.map((q) => <Link key={q.url} className="wp-exec-qa" to={q.url}>{q.title}</Link>)}
    </section>);

  const tabs = (
    <nav className="wp-exec-tabs" aria-label="Разделы пульта">
      <button className={tab === "overview" ? "cur" : ""} onClick={() => setTab("overview")}>Обзор</button>
      <button className={tab === "ops" ? "cur" : ""} onClick={() => setTab("ops")}>Все проблемы{x.escalations.length ? ` · ${x.escalations.length}` : ""}</button>
      <Link to="/assignments">Поручения</Link>
      <Link to="/summary">Аналитика</Link>
    </nav>);

  const kpis = (
    <section className="wp-exec-kpis" aria-label="Показатели компании">
      {x.kpis.map((k) => {
        const empty = k.value === null || k.value === undefined;
        const open = () => setDrill(k);
        return (
          <button key={k.key} type="button" onClick={open} className={`wp-exec-kpi ${k.tone}`} title={`${k.method}\nИсточник: ${k.source}`} aria-label={`${k.title}: открыть расшифровку и методику`}>
            <small>{k.title}</small>
            <b>{empty ? "нет данных" : k.value}{!empty && k.unit && k.unit !== "%" ? <em> {k.unit}</em> : null}</b>
            <span>{k.sub}</span>
            {k.reference && <span className="wp-exec-kpi-ref">{k.reference.text}</span>}
            {k.extra.map((e) => <span key={e.label} className="wp-exec-kpi-x">{e.label}: <b>{e.value}</b></span>)}
            {k.data_quality !== "confirmed" && <i className={`wp-exec-q ${k.data_quality}`}>{qt[k.data_quality]}{k.quality_note ? ` — ${k.quality_note}` : ""}</i>}
            <span className="wp-exec-kpi-foot">{k.period} · {hhmm(k.calculated_at)} МСК</span>
          </button>);
      })}
    </section>);

  const modals = (<>
    {drill && (() => { const dr = drill.drilldown.list ? x.drill[drill.drilldown.list] : null; return (
      <SecurityModal title={dr ? `${dr.title}: ${dr.rows.length}` : drill.title} onClose={() => setDrill(null)} width={680}>
        <div className="wp-exec-method">
          <b>Как считается.</b> {drill.method}
          <div className="muted">Источник: {drill.source}. Период: {drill.period}. Рассчитано в {hhmm(drill.calculated_at)} МСК.{drill.data_quality !== "confirmed" ? ` Качество: ${qt[drill.data_quality]}${drill.quality_note ? ` — ${drill.quality_note}` : ""}.` : ""}</div>
        </div>
        {dr && <ul className="wp-exec-drill">{dr.rows.map((r, i) => (
          <li key={i}><Link to={r.url}>{r.title}</Link><span>{r.value ?? <span className="muted">нет данных</span>}</span>{r.note && <small className="muted">{r.note}</small>}</li>))}
          {!dr.rows.length && <li className="muted">Список пуст.</li>}</ul>}
        {dr?.footer && <p className="muted wp-exec-note">Справочно: {dr.footer}. Курс — последний опубликованный ЦБ РФ не позже сегодняшней даты.</p>}
        <div className="asg-actions"><Link className="btn ghost" to={drill.url}>Открыть реестр →</Link></div>
      </SecurityModal>); })()}
    {asg && <AssignmentDialog prefill={asg} onClose={() => setAsg(null)} onDone={() => { setAsg(null); load(); }} />}
    {dirs && <DirectionsDialog onClose={() => setDirs(false)} onSaved={() => { setDirs(false); load(); }} />}
  </>);

  if (tab === "ops") {
    return (
      <div className="wp-exec">
        {header}{quick}{tabs}
        <section className="card">
          <div className="wp-exec-h"><h2>Требует вмешательства</h2>
            <div className="wp-exec-chips">
              {([["", `все ${x.escalations.length}`], ["critical", `критично ${counts.critical}`], ["high", `высокий ${counts.high}`], ["normal", `обычный ${counts.normal}`], ["info", `информационно ${counts.info}`]] as const).map(([k, t]) => (
                <button key={k} className={prio === k ? "cur" : ""} onClick={() => setPrio(k)}>{t}</button>))}
            </div></div>
          {esc.length ? <ul className="wp-exec-list">{groupRows(esc)}</ul> : <p className="muted">Ничего не требует вмешательства.</p>}
          <p className="muted wp-exec-note">Порядок: приоритет → срок реакции → длительность просрочки. Один источник и правило — одна строка. Сигнал уходит из очереди, когда устранена причина в исходной карточке; поручение по сигналу видно в строке.</p>
        </section>
        <section className="card">
          <div className="wp-exec-h"><h2>Устранено за 30 дней {hist && hist.length > 0 && <span className="muted">{hist.length}</span>}</h2></div>
          {hist === null ? <Loading p /> : hist.length === 0 ? <p className="muted">Пока пусто: история ведётся с этого выпуска.</p> :
            <ul className="wp-exec-drill">{hist.map((h) => (
              <li key={h.key}><span><span className={`wp-exec-prio ${PRIO[h.priority]?.[1] || "info"}`}>{PRIO[h.priority]?.[0] || h.priority}</span> <Link to={h.url}>{h.object_number && h.title.includes(h.object_number) ? h.title : `${h.object_type}${h.object_number ? ` ${h.object_number}` : ""}: ${h.title}`}</Link></span>
                <span className="muted">{h.hours < 48 ? `${Math.max(1, Math.round(h.hours))} ч` : `${Math.round(h.hours / 24)} дн.`}</span>
                <small className="muted">появился {new Date(h.first_seen).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}, устранён {new Date(h.resolved_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}{h.times > 1 ? ` · возникал ${h.times} раза` : ""}{h.assignment ? ` · поручение ${h.assignment.number}` : ""}</small></li>))}</ul>}
          <p className="muted wp-exec-note">Время устранения — с точностью до часа: журнал обновляется при открытии пульта и фоновым заданием.</p>
        </section>
        {modals}
      </div>);
  }

  const a = x.assignments;
  const mx = Math.max(1, ...(d.money?.curve || []).map((c) => Math.max(c.in, c.out)));
  return (
    <div className="wp-exec">
      {header}{quick}{tabs}{kpis}
      <section className="card wp-exec-queue">
        <div className="wp-exec-h"><h2>Требует моего вмешательства {x.escalations.length > 0 && <span className="wp-exec-count">{x.escalations.length}</span>}</h2>
          {groupsCount > 5 && <button className="btn ghost" onClick={() => setTab("ops")}>Все проблемы ({x.escalations.length}) →</button>}</div>
        {x.escalations.length ? <ul className="wp-exec-list">{groupRows(x.escalations, 5)}</ul> : <p className="muted">Ничего не требует вмешательства.</p>}
      </section>
      <section className="wp-exec-grid2">
        <div className="card">
          <div className="wp-exec-h"><h2>Поручения</h2><Link to={a.url + "?scope=control"}>На контроле →</Link></div>
          <div className="wp-exec-asg">
            <Link to="/assignments?scope=all&status=inwork"><b>{a.open}</b><small>в работе</small></Link>
            <Link to="/assignments?scope=all&status=overdue" className={a.overdue ? "bad" : ""}><b>{a.overdue}</b><small>просрочено</small></Link>
            <Link to="/assignments?scope=all&status=today"><b>{a.today}</b><small>срок сегодня</small></Link>
            <Link to="/assignments?scope=all&status=review" className={a.review ? "warn" : ""}><b>{a.review}</b><small>на проверке</small></Link>
          </div>
          <ul className="wp-exec-mini">{a.items.map((i) => (
            <li key={i.id}><Link to={`/assignments?open=${i.id}`}><span className={`wp-exec-prio ${PR_CLS[i.priority]}`}>{i.priority_title}</span> {i.title}</Link>
              <span className={i.overdue ? "bad" : "muted"}>{i.number} · {i.executor || i.assignee} · {i.status_title} · до {ddmm(i.due_on)}</span></li>))}
            {!a.items.length && <li className="muted">Открытых поручений нет.</li>}</ul>
          {a.office.open > 0 && <p className="muted wp-exec-note">Документы Канцелярии на исполнении: {a.office.open}{a.office.overdue ? `, просрочено ${a.office.overdue}` : ""} — <Link to={a.office.url}>контроль</Link></p>}
        </div>
        <div className="card">
          <div className="wp-exec-h"><h2>Направления</h2>{x.can_assign && <button className="btn ghost" onClick={() => setDirs(true)}>Руководители</button>}</div>
          <table className="wp-exec-dirs"><thead><tr><th>Направление</th><th>Руководитель</th><th>Результат</th><th>Отклонения</th><th></th></tr></thead>
            <tbody>{x.directions.map((r) => (
              <tr key={r.key} className={r.tone}>
                <td><Link to={r.card_url} className="wp-exec-dir-link"><b>{r.title}</b></Link><div className="muted">сотрудников: {r.staff_count}</div></td>
                <td>{r.head ? <>{r.head.name}{r.head.position && <div className="muted">{r.head.position}</div>}
                  {r.head.deputy && <div className="wp-exec-dep">{r.head.deputy.temporary ? `замещение${r.head.deputy.to ? ` до ${ddmm(r.head.deputy.to)}` : ""}: ${r.head.deputy.name}` : `заместитель: ${r.head.deputy.name}`}</div>}</>
                  : <span className="muted">не назначен</span>}</td>
                <td>{r.results.map((l) => <div key={l}>{l}</div>)}</td>
                <td>{r.deviations.length ? r.deviations.map((l) => <div key={l} className={r.tone === "bad" ? "bad" : "warn"}>{l}</div>) : <span className="muted">нет</span>}</td>
                <td><Link to={r.card_url} aria-label={`Карточка направления: ${r.title}`}>→</Link></td>
              </tr>))}</tbody></table>
        </div>
      </section>
      <section className="wp-exec-grid4">
        <div className="card">
          <div className="wp-exec-h"><h2>Денежный поток</h2>{d.money?.url && <Link to={d.money.url}>Финансы →</Link>}</div>
          {d.money && !d.money.curve.some((c) => c.in || c.out) ? <p className="muted">Поступлений и платежей за 8 недель в «Финансах» нет.</p> : d.money ? <>
            <svg className="wp-exec-bars" viewBox={`0 0 ${d.money.curve.length * 40} 110`} role="img" aria-label="Поступления и платежи по неделям, рубли">
              {d.money.curve.map((c, i) => (
                <g key={c.week}><title>{`${c.label}: поступления ${money(c.in)}, платежи ${money(c.out)}`}</title>
                  <rect className="in" x={i * 40 + 6} y={90 - 80 * c.in / mx} width="12" height={80 * c.in / mx} />
                  <rect className="out" x={i * 40 + 20} y={90 - 80 * c.out / mx} width="12" height={80 * c.out / mx} />
                  <text x={i * 40 + 19} y="104" textAnchor="middle">{c.label}</text></g>))}
            </svg>
            <div className="wp-exec-legend"><span><i className="in" />поступления</span><span><i className="out" />платежи</span><span className="muted">₽, 8 недель</span></div>
            {nv && (() => { const sIn = d.money.curve.reduce((n, c) => n + c.in, 0); const sOut = d.money.curve.reduce((n, c) => n + c.out, 0);
              return <p className="wp-exec-note wp-exec-sum">За 8 недель на графике: поступления {money(sIn)}, платежи {money(sOut)}{sIn - sOut !== 0 ? `, разница ${sIn - sOut > 0 ? "+" : "−"}${money(Math.abs(sIn - sOut))}` : ""}.</p>; })()}
            <div className="muted wp-exec-note">{d.money.balance_status === "confirmed" && d.money.min_cum !== null ? `минимум прогнозного остатка ${money(d.money.min_cum)} — ${d.money.min_week}` : "остаток на счетах не подтверждён — прогноз остатка не показывается"}</div>
          </> : <p className="muted">Нет доступа к финансам.</p>}
        </div>
        <div className="card">
          <div className="wp-exec-h"><h2>Согласования</h2><span className="muted">{d.approvals.length}</span></div>
          <ul className="wp-exec-mini">{d.approvals.slice(0, 3).map((p) => <li key={p.url + p.title}><Link to={p.url}>{p.title}</Link><span className="muted">{p.sub}</span></li>)}
            {!d.approvals.length && <li className="muted">Решений не ждёт.</li>}</ul>
        </div>
        <div className="card">
          <div className="wp-exec-h"><h2>Юридический блок</h2><Link to="/legal">Реестр →</Link></div>
          {d.legal ? <ul className="wp-exec-mini">
            <li><Link to="/legal?tab=contracts&flag=no_scan">Без скана</Link><b>{d.legal.contracts_no_scan}</b></li>
            <li><Link to="/legal?tab=contracts">На проверке и подписи</Link><b>{(d.legal.contracts_review || 0) + (d.legal.contracts_signing || 0)}</b></li>
            <li><Link to="/legal?tab=contracts&flag=expiring">Истекают в 30 дней</Link><b>{d.legal.contracts_expiring}</b></li>
            <li><Link to="/legal?tab=claims">Претензии: открыто / срок истёк</Link><b>{d.legal.claims_open ?? 0} / {d.legal.claims_answer_overdue ?? 0}</b></li>
          </ul> : <p className="muted">Нет доступа.</p>}
        </div>
        <div className="card">
          <div className="wp-exec-h"><h2>Ближайшие сроки</h2><span className="muted">14 дней</span></div>
          <ul className="wp-exec-mini">{x.deadlines.slice(0, 5).map((e) => (
            <li key={e.url + e.on + e.title} className={`wp-exec-dl ${e.urgency}`}><Link to={e.url}>{e.title}</Link>
              <span><b className="wp-exec-dl-on">{e.urgency === "overdue" ? `просрочено с ${ddmm(e.on)}` : e.urgency === "today" ? "сегодня" : ddmm(e.on)}</b> · {e.type}{e.who ? ` · ${e.who}` : ""}</span></li>))}
            {!x.deadlines.length && <li className="muted">В ближайшие 14 дней сроков нет.</li>}</ul>
        </div>
      </section>
      {nv && <RecentActivity />}
      <p className="muted wp-exec-note wp-exec-foot">Обновлено {new Date(x.meta.generated_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК. Нажатие на показатель открывает список с теми же условиями; подсказка — источник.</p>
      {modals}
    </div>);
}
