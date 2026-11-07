import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";

// Рабочее место — главная страница под роль. Каркас «командный центр»: показатели → «главное сейчас» → три колонки
// (сегодня и задачи · сделки, согласования, деньги · внимание, уведомления, сроки, команда). Набор блоков — по роли,
// сотрудник скрывает и переставляет блоки, ЗГД закрепляет обязательные для роли.

type Ev = { on: string; time: string; title: string; sub: string; tone: string; url: string; kind: string };
type TaskRow = { id: string; title: string; due: string; days_left: number; overdue: boolean; deal_id: string; deal_number: string; client_name: string; priority: string; as_deputy: boolean };
type DealRow = { id: string; number: string; client_name: string; site: string; equipment: string; status: string; status_title: string; price_rub: number | null; manager_name: string; action: string; tone: string; exec: { done: number; total: number; delay: number } | null; priority: string };
type Approval = { kind: string; title: string; sub: string; url: string; waiting_since: string | null; tone: string };
type Att = { title: string; sub: string; url: string; tone: string };
type Data = {
  user: { name: string; first_name: string; role: string; role_title: string; greeting: string };
  date: { iso: string; text: string };
  layout: { order: string[]; hidden: string[]; required: string[]; available: { code: string; title: string }[]; can_lock: boolean; role_required: Record<string, string[]>; roles: Record<string, string>; role_blocks: Record<string, string[]>; all_blocks: Record<string, string> };
  kpis: { title: string; value: string; sub: string; tone: string }[];
  focus: { title: string; text: string; action: string; url: string; tone: string } | null;
  today: { events: Ev[]; week: { date: string; wd: string; day: number; today: boolean; count: number; tones: string[] }[] };
  upcoming: Ev[]; tasks: TaskRow[]; tasks_total: number; tasks_overdue: number; deals: DealRow[]; approvals: Approval[]; attention: Att[];
  money: { curve: { week: string; label: string; in: number; out: number; net: number; cum: number }[]; receivable_sum: number; receivable_count: number; min_cum: number; min_week: string } | null;
  funnel: { code: string; title: string; count: number }[]; team: { name: string; open: number; exec: number; overdue: number; share: number }[];
  notifications: { id: string; text: string; at: string | null; read: boolean; deal_id: string | null }[];
  fx: Record<string, { rate: number; on: string }>; legal: { lawyer_review: number; commercial_review: number; poa_active: number; poa_expiring: number; poa_no_scan: number } | null;
  fin: boolean; quick: { title: string; url: string }[];
};

const TONE_BG: Record<string, string> = { bad: "var(--bad-bg)", warn: "var(--warn-bg)", info: "var(--info-bg)", ok: "var(--ok-bg)", "": "#fafafa" };
const TONE_FG: Record<string, string> = { bad: "var(--bad)", warn: "var(--warn)", info: "var(--info)", ok: "var(--ok)", "": "var(--muted)" };
const TONE_DOT: Record<string, string> = { bad: "#ff3131", warn: "#f59e0b", info: "#2563eb", ok: "#15803d", "": "#a1a1aa" };
const COLS: Record<string, 0 | 1 | 2> = { today: 0, tasks: 0, focus: 1, deals: 1, approvals: 1, money: 1, funnel: 1, attention: 2, notifications: 2, deadlines: 2, team: 1, legal: 2, quick: 2 };
const dShort = (iso: string) => { const d = new Date(iso); return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`; };
const ago = (iso: string | null) => { if (!iso) return ""; const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000); return m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : `${Math.round(m / 1440)} дн. назад`; };
const money = (v: number) => Math.abs(v) >= 1e6 ? `${(v / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн` : `${Math.round(v / 1e3).toLocaleString("ru-RU")} тыс.`;

export default function WorkplacePage({ me }: { me: Me }) {
  void me;
  const nav = useNavigate();
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [setup, setSetup] = useState(false);
  const load = () => api.get<Data>("/api/workplace").then(setD).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, []);
  const visible = useMemo(() => d ? d.layout.order.filter((b) => !d.layout.hidden.includes(b)) : [], [d]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted" style={{ padding: 24 }}>Загрузка рабочего места…</div>;
  const col = (n: 0 | 1 | 2) => visible.filter((b) => COLS[b] === n);
  const done = (t: TaskRow) => api.post(`/api/tasks/${t.id}/done`, { comment: "с рабочего места" }).then(load).catch((e) => setErr((e as Error).message));

  const block = (code: string) => {
    switch (code) {
      case "focus": return d.focus && (
        <div key={code} className="wp-card wp-focus" style={{ borderColor: TONE_DOT[d.focus.tone] || "#141413" }}>
          <div className="wp-h" style={{ color: "#a1a1aa" }}>Главное сейчас</div>
          <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.2, marginTop: 6 }}>{d.focus.title}</div>
          <div style={{ color: "#d4d4d8", marginTop: 6 }}>{d.focus.text}</div>
          <div className="row" style={{ marginTop: 14, gap: 8 }}><button className="btn" onClick={() => nav(d.focus!.url)}>{d.focus.action}</button></div>
        </div>);
      case "today": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Сегодня</h2><Link to="/tasks" className="muted">неделя →</Link></div>
          <div className="wp-week">{d.today.week.map((w) => (
            <div key={w.date} className={`wp-day ${w.today ? "cur" : ""}`}><span className="wd">{w.wd}</span><span className="n">{w.day}</span>
              <span className="dots">{w.tones.slice(0, 3).map((t) => <i key={t} style={{ background: TONE_DOT[t] }} />)}{w.count === 0 && <i style={{ background: "transparent" }} />}</span></div>))}</div>
          {d.today.events.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Событий на сегодня нет — сроки по сделкам и задачам появятся здесь.</div>}
          <div className="wp-list">{d.today.events.map((e, i) => (
            <Link key={i} to={e.url} className="wp-ev" style={{ background: TONE_BG[e.tone] || "#fafafa", borderLeftColor: TONE_DOT[e.tone] }}>
              <span className="t">{e.time || (e.kind === "task" ? "задача" : e.kind === "kp" ? "срок" : "")}</span><span><b>{e.title}</b><small>{e.sub}</small></span></Link>))}</div>
        </div>);
      case "tasks": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Задачи и поручения <span className="muted">· {d.tasks_total}</span>{d.tasks_overdue > 0 && <span className="badge bad" style={{ marginLeft: 6 }}>{d.tasks_overdue} просроч.</span>}</h2><Link to="/tasks" className="muted">все →</Link></div>
          {d.tasks.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Открытых задач нет.</div>}
          <ul className="wp-tasks">{d.tasks.map((t) => (
            <li key={t.id}><input type="checkbox" aria-label="Выполнено" onChange={() => { if (window.confirm(`Закрыть задачу «${t.title}»?`)) done(t); }} />
              <Link to={`/deals/${t.deal_id}`} style={{ flex: 1, minWidth: 0 }}><div style={{ fontWeight: 500 }}>{t.title}{t.as_deputy && <span className="badge" style={{ marginLeft: 6, fontSize: 10 }}>замещение</span>}</div>
                <small style={{ color: t.overdue ? "var(--bad)" : "var(--muted)" }}>{t.overdue ? `просрочено ${-t.days_left} дн.` : t.days_left === 0 ? "сегодня" : t.days_left === 1 ? "завтра" : dShort(t.due)} · {t.deal_number} {t.client_name}</small></Link>
              {t.priority !== "normal" && <span className={`badge ${t.priority === "urgent" ? "bad" : "warn"}`}>{t.priority === "urgent" ? "срочно" : "важно"}</span>}</li>))}</ul>
        </div>);
      case "deals": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Сделки — требуют действия</h2><Link to="/deals" className="muted">все сделки →</Link></div>
          {d.deals.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Сделок, ждущих действия, нет.</div>}
          {d.deals.length > 0 && <div style={{ overflowX: "auto" }}><table className="wp-table">
            <thead><tr><th>Сделка</th><th>Клиент</th><th>Статус</th>{d.fin && <th className="num">Сумма</th>}<th>Что сделать</th></tr></thead>
            <tbody>{d.deals.map((r) => (
              <tr key={r.id} onClick={() => nav(`/deals/${r.id}`)}>
                <td><b>{r.number}</b>{r.exec && <div className="muted" style={{ fontSize: 11 }}>этапы {r.exec.done}/{r.exec.total}{r.exec.delay > 0 ? ` · +${r.exec.delay} дн.` : ""}</div>}</td>
                <td>{r.client_name}<div className="muted" style={{ fontSize: 11 }}>{r.equipment}</div></td>
                <td><span className="badge" style={{ fontSize: 11 }}>{r.status_title}</span></td>
                {d.fin && <td className="num">{r.price_rub ? money(r.price_rub) : "—"}</td>}
                <td style={{ color: TONE_FG[r.tone] || "var(--ink)" }}>{r.action || <span className="muted">—</span>}</td></tr>))}</tbody></table></div>}
        </div>);
      case "approvals": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Ждут моего решения {d.approvals.length > 0 && <span style={{ color: "var(--accent)" }}>· {d.approvals.length}</span>}</h2></div>
          {d.approvals.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Ничего не ждёт — всё согласовано.</div>}
          <div className="wp-list">{d.approvals.map((a, i) => (
            <div key={i} className="wp-appr"><div style={{ minWidth: 0 }}><div style={{ fontWeight: 600 }}>{a.title}</div><small className="muted">{a.sub}{a.waiting_since ? ` · ждёт ${ago(a.waiting_since)}` : ""}</small></div>
              <button className="btn ghost" onClick={() => nav(a.url)}>Рассмотреть</button></div>))}</div>
        </div>);
      case "attention": return d.attention.length > 0 ? (
        <div key={code} className="wp-card" style={{ borderColor: "#f3b4b4" }}>
          <div className="wp-head"><h2 style={{ color: "var(--bad)" }}>Требует вмешательства</h2></div>
          <div className="wp-list">{d.attention.map((a, i) => <Link key={i} to={a.url} className="wp-att"><b>{a.title}</b><small className="muted">{a.sub}</small></Link>)}</div>
        </div>) : null;
      case "money": return d.money && (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Денежный поток, 8 недель</h2><Link to="/summary?kind=money" className="muted">подробно →</Link></div>
          <div className="row muted" style={{ fontSize: 12, gap: 14, marginBottom: 8 }}><span>дебиторка просрочена: <b style={{ color: d.money.receivable_count ? "var(--bad)" : "inherit" }}>{d.money.receivable_count ? `${money(d.money.receivable_sum)} ₽ · ${d.money.receivable_count}` : "нет"}</b></span>
            {d.money.min_cum < 0 && <span>минимум потока: <b style={{ color: "var(--warn)" }}>{money(d.money.min_cum)} ₽ · {d.money.min_week}</b></span>}</div>
          <Bars curve={d.money.curve} />
        </div>);
      case "funnel": { const mx = Math.max(1, ...d.funnel.map((f) => f.count)); return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Воронка квартала</h2><span className="muted" style={{ fontSize: 12 }}>{d.user.role === "manager" ? "по моим сделкам" : "по всем сделкам"}</span></div>
          <div className="wp-funnel">{d.funnel.map((f) => <div key={f.code}><b>{f.count}</b><div className="bar" style={{ height: `${Math.max(6, Math.round(80 * f.count / mx))}px`, background: f.code === "won" ? "#15803d" : f.code === "approval" ? "#ff3131" : "#52525b" }} /><small>{f.title}</small></div>)}</div>
        </div>); }
      case "team": return d.team.length > 0 ? (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Команда · нагрузка</h2></div>
          <div className="wp-list" style={{ gap: 6 }}>{d.team.map((t) => (
            <div key={t.name} className="wp-team"><span>{t.name}</span><div className="track"><div style={{ width: `${Math.round(t.share * 100)}%`, background: t.overdue ? "#ff3131" : "#141413" }} /></div><small className="muted">{t.open + t.exec} сд.{t.overdue ? ` · ${t.overdue} проср.` : ""}</small></div>))}</div>
        </div>) : null;
      case "notifications": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Уведомления</h2></div>
          {d.notifications.length === 0 && <div className="muted" style={{ fontSize: 13 }}>Новых уведомлений нет.</div>}
          <div className="wp-list" style={{ gap: 8 }}>{d.notifications.slice(0, 6).map((n) => (
            <div key={n.id} className="wp-note" onClick={() => n.deal_id && nav(`/deals/${n.deal_id}`)} style={{ cursor: n.deal_id ? "pointer" : "default", opacity: n.read ? .7 : 1 }}>
              <i style={{ background: n.read ? "#d4d4d8" : "#ff3131" }} /><div><div style={{ fontSize: 13 }}>{n.text}</div><small className="muted">{ago(n.at)}</small></div></div>))}</div>
        </div>);
      case "deadlines": return (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Ближайшие сроки</h2></div>
          {d.upcoming.length === 0 && <div className="muted" style={{ fontSize: 13 }}>В ближайшие две недели сроков нет.</div>}
          <div className="wp-list" style={{ gap: 6 }}>{d.upcoming.map((e, i) => <Link key={i} to={e.url} className="wp-dl"><span>{e.title}<small className="muted">{e.sub}</small></span><b style={{ color: TONE_FG[e.tone] }}>{dShort(e.on)}</b></Link>)}</div>
        </div>);
      case "legal": return d.legal && (
        <div key={code} className="wp-card">
          <div className="wp-head"><h2>Юридический блок</h2><Link to="/legal" className="muted">открыть →</Link></div>
          <div className="wp-grid2">
            <Link to="/legal?tab=packages" className="wp-mini"><b style={{ color: d.legal.lawyer_review ? "var(--warn)" : "inherit" }}>{d.legal.lawyer_review}</b><small>на проверке юриста</small></Link>
            <Link to="/legal?tab=packages" className="wp-mini"><b>{d.legal.commercial_review}</b><small>коммерческое согласование</small></Link>
            <Link to="/legal?tab=poa" className="wp-mini"><b style={{ color: d.legal.poa_expiring ? "var(--warn)" : "inherit" }}>{d.legal.poa_expiring}</b><small>доверенностей истекает</small></Link>
            <Link to="/legal?tab=poa" className="wp-mini"><b>{d.legal.poa_no_scan}</b><small>без подписанного скана</small></Link>
          </div>
        </div>);
      case "quick": return (
        <div key={code} className="wp-card wp-dark">
          <div className="wp-head"><h2 style={{ color: "#fff" }}>Быстрые действия</h2></div>
          <div className="wp-grid2">{d.quick.map((q) => <button key={q.url} className="wp-qa" onClick={() => nav(q.url)}>{q.title}</button>)}</div>
          {Object.keys(d.fx).length > 0 && <div style={{ fontSize: 12, color: "#a1a1aa", marginTop: 12 }}>Курс ЦБ: {Object.entries(d.fx).map(([k, v]) => `${k} ${v.rate.toLocaleString("ru-RU")}`).join(" · ")}</div>}
        </div>);
      default: return null;
    }
  };

  return (
    <div className="wp">
      <header className="wp-top">
        <div><h1>{d.user.greeting}, {d.user.first_name || d.user.name}</h1><div className="muted" style={{ fontSize: 13 }}>{d.date.text} · {d.user.role_title} · {d.today.events.length} событий сегодня{d.tasks_overdue ? ` · ${d.tasks_overdue} задач просрочено` : ""}</div></div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn ghost" onClick={() => setSetup(!setup)}>Настроить блоки</button>
          {d.quick[0] && <button className="btn add" onClick={() => nav(d.quick[0].url)}>{d.quick[0].title}</button>}
        </div>
      </header>
      {setup && <Setup d={d} onClose={() => setSetup(false)} onSaved={() => { setSetup(false); load(); }} />}
      {visible.includes("kpis") && (
        <section className="wp-kpis">{d.kpis.map((k) => <div key={k.title} className="wp-kpi"><small>{k.title}</small><b style={{ color: TONE_FG[k.tone] && k.tone ? TONE_FG[k.tone] : "inherit" }}>{k.value}</b><span className="muted">{k.sub}</span></div>)}</section>)}
      <section className="wp-cols">
        <div className="wp-col">{col(0).map(block)}</div>
        <div className="wp-col">{col(1).map(block)}</div>
        <div className="wp-col">{col(2).map(block)}</div>
      </section>
    </div>
  );
}

function Bars({ curve }: { curve: { label: string; in: number; out: number; net: number; cum: number }[] }) {
  const mx = Math.max(1, ...curve.map((c) => Math.max(c.in, c.out)));
  return (
    <div>
      <div className="wp-bars">{curve.map((c) => (
        <div key={c.label} title={`поступления ${money(c.in)} · платежи ${money(c.out)} · накопленно ${money(c.cum)}`}>
          <small style={{ color: c.net < 0 ? "var(--bad)" : "var(--ok)" }}>{c.in || c.out ? (c.net >= 0 ? "+" : "−") + money(Math.abs(c.net)) : ""}</small>
          <div className="pair"><div style={{ height: `${Math.round(70 * c.in / mx)}px`, background: "#15803d" }} /><div style={{ height: `${Math.round(70 * c.out / mx)}px`, background: "#ff3131", opacity: .85 }} /></div>
          <small className="muted">{c.label}</small></div>))}</div>
      <div className="row muted" style={{ fontSize: 11, gap: 12, marginTop: 6 }}><span><i className="wp-sw" style={{ background: "#15803d" }} />поступления от клиентов</span><span><i className="wp-sw" style={{ background: "#ff3131" }} />платежи заводам</span><span>RUB, план по этапам исполнения</span></div>
    </div>);
}

function Setup({ d, onClose, onSaved }: { d: Data; onClose: () => void; onSaved: () => void }) {
  const [order, setOrder] = useState(d.layout.order);
  const [hidden, setHidden] = useState(d.layout.hidden);
  const [role, setRole] = useState(d.user.role);
  const [req, setReq] = useState<Record<string, string[]>>({ ...d.layout.role_required, [d.user.role]: d.layout.required });
  const [msg, setMsg] = useState("");
  const titles = d.layout.all_blocks;
  const move = (b: string, dir: -1 | 1) => { const i = order.indexOf(b); const j = i + dir; if (j < 0 || j >= order.length) return; const n = [...order]; [n[i], n[j]] = [n[j], n[i]]; setOrder(n); };
  const save = () => api.put("/api/workplace/layout", { order, hidden }).then(onSaved).catch((e) => setMsg((e as Error).message));
  const saveRole = () => api.put("/api/workplace/role-blocks", { role, required: req[role] || [] }).then(() => setMsg(`Обязательные блоки для роли «${d.layout.roles[role]}» сохранены`)).catch((e) => setMsg((e as Error).message));
  return (
    <div className="wp-card" style={{ marginBottom: 14 }}>
      <div className="wp-head"><h2>Настройка главной страницы</h2><button className="btn ghost" onClick={onClose}>✕</button></div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="wp-setup">
        <div>
          <b style={{ fontSize: 13 }}>Мои блоки — порядок и видимость</b>
          <ul>{order.map((b) => { const locked = d.layout.required.includes(b); return (
            <li key={b}><label className="row" style={{ gap: 8, flex: 1 }}><input type="checkbox" checked={!hidden.includes(b)} disabled={locked} onChange={(e) => setHidden(e.target.checked ? hidden.filter((x) => x !== b) : [...hidden, b])} />{titles[b]}{locked && <span className="badge" style={{ fontSize: 10 }}>закреплён ЗГД</span>}</label>
              <span className="row" style={{ gap: 2 }}><button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => move(b, -1)} aria-label="выше">↑</button><button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => move(b, 1)} aria-label="ниже">↓</button></span></li>); })}</ul>
          <div className="row" style={{ gap: 8, marginTop: 8 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => { setOrder(d.layout.available.map((a) => a.code)); setHidden([]); }}>По умолчанию</button></div>
        </div>
        {d.layout.can_lock && (
          <div>
            <b style={{ fontSize: 13 }}>Обязательные блоки по ролям (ЗГД)</b>
            <label className="f" style={{ marginTop: 6 }}><span>Роль</span><select value={role} onChange={(e) => setRole(e.target.value)}>{Object.entries(d.layout.roles).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <ul>{(d.layout.role_blocks[role] || []).map((b) => (
              <li key={b}><label className="row" style={{ gap: 8 }}><input type="checkbox" checked={(req[role] || []).includes(b)} onChange={(e) => setReq({ ...req, [role]: e.target.checked ? [...(req[role] || []), b] : (req[role] || []).filter((x) => x !== b) })} />{titles[b]}</label></li>))}</ul>
            <button className="btn" onClick={saveRole}>Закрепить для роли</button>
            <p className="muted" style={{ fontSize: 12 }}>Закреплённые блоки сотрудники этой роли скрыть не смогут; порядок остаётся на их усмотрение.</p>
          </div>)}
      </div>
    </div>);
}
