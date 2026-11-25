import { Crumbs } from "../components/Levels";
import { useEffect, useState } from "react";
import { toneOf } from "../colors";
import { blockTitle } from "../labels";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { STATUS_TITLES, type Task } from "../types";
import { askPrompt } from "../components/Dialogs";
import { useNewUiFlag } from "../uiMode";
import { Empty } from "../components/States";

// Мои задачи: карточками (по умолчанию) или таблицей. Карточки сгруппированы по сроку: просрочено · сегодня · неделя · позже.

type View = "cards" | "table";
const load = <T,>(k: string, d: T): T => { try { return (JSON.parse(localStorage.getItem(k) || "") as T) ?? d; } catch { return d; } };
const save = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* нет хранилища */ } };

const GROUPS: { key: string; title: string; test: (t: Task) => boolean }[] = [
  { key: "late", title: "Просрочено", test: (t) => (t.days_left ?? 0) < 0 },
  { key: "today", title: "Сегодня", test: (t) => (t.days_left ?? 0) === 0 },
  { key: "week", title: "На этой неделе", test: (t) => (t.days_left ?? 0) > 0 && (t.days_left ?? 0) <= 7 },
  { key: "later", title: "Позже", test: (t) => (t.days_left ?? 0) > 7 },
];

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [view, setViewState] = useState<View>(() => load<View>("gsho.tasksView", "cards"));
  const setView = (v: View) => { setViewState(v); save("gsho.tasksView", v); };
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);
  const nv = useNewUiFlag();
  const reload = () => api.get<Task[]>("/api/tasks/my").then(setTasks).catch((e) => setMsg((e as Error).message));
  useEffect(() => { reload(); }, []);
  const shown = tasks.filter((t) => !q || [t.deal_number, t.client_name, t.title, t.equipment].some((x) => (x || "").toLowerCase().includes(q.toLowerCase())));
  async function done(t: Task) {
    const c = (await askPrompt(`Закрыть задачу «${t.title}»? Комментарий (необязательно):`, ""));
    if (c === null) return;
    try { await api.post(`/api/tasks/${t.id}/done`, { comment: c }); setMsg(`Задача закрыта: ${t.title}`); setOk(true); reload(); } catch (e) { setMsg((e as Error).message); setOk(false); }
  }
  const dueText = (t: Task) => {
    const n = t.days_left ?? 0;
    return n < 0 ? `просрочено ${-n} дн.` : n === 0 ? "сегодня" : n === 1 ? "завтра" : `через ${n} дн.`;
  };
  const href = (t: Task) => t.url || `/deals/${t.deal_id}?tab=${t.tab || "main"}`;   // уточнение «Финансов» — свой адрес (D-141)
  // новый вид: срок → задача → что сделать → объект → контекст; «Открыть» и «Выполнено» — вторичные кнопки, красным только просрочка
  const nvCard = (t: Task) => {
    const n = t.days_left ?? 0;
    return (
      <article key={t.id} className={"tk-card" + (n < 0 ? " late" : n === 0 ? " today" : "")}>
        <div className="tk-top"><Link to={href(t)} className="tk-num">{t.deal_number}</Link><span className="tk-due">{fmtDate(t.due)} · <b>{dueText(t)}</b></span></div>
        <div className="tk-title">{t.title}</div>
        {t.hint && <div className="tk-hint">Что сделать: {t.hint}</div>}
        <div className="tk-ctx"><b>{t.client_name}</b>{t.site && <> · {t.site}</>}{t.equipment && <div className="tk-eq">{t.equipment}</div>}</div>
        <div className="tk-foot">
          <span className="tk-tags"><span className="badge">{blockTitle(t.block)}</span>{t.deal_status && <span className="badge">{STATUS_TITLES[t.deal_status] || t.deal_status}</span>}</span>
          <span className="tk-acts"><Link to={href(t)} className="btn ghost">Открыть</Link>
            {t.can_close && <button className="btn ghost" onClick={() => done(t)} title="задача сделана вне системы">✓ Выполнено</button>}</span>
        </div>
      </article>);
  };
  const card = (t: Task) => {
    if (nv) return nvCard(t);
    const late = (t.days_left ?? 0) < 0;
    return (
      <div key={t.id} style={{ background: "#fff", border: "1px solid var(--line)", borderLeft: `4px solid ${late ? "var(--bad)" : (t.days_left ?? 0) === 0 ? "var(--warn)" : "var(--line)"}`,
        borderRadius: 8, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
          <Link to={href(t)} style={{ fontWeight: 700, textDecoration: "none" }}>{t.deal_number}</Link>
          <span style={{ fontSize: 12, fontWeight: 600, color: late ? "var(--bad)" : "var(--muted)", whiteSpace: "nowrap" }}>{fmtDate(t.due)} · {dueText(t)}</span>
        </div>
        <div style={{ fontSize: 13 }}><b>{t.client_name}</b>{t.site && <span className="muted"> · {t.site}</span>}</div>
        {t.equipment && <div className="muted" style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.equipment}</div>}
        <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.3 }}>{t.title}</div>
        {t.hint && <div className="muted" style={{ fontSize: 12 }}>Что сделать: {t.hint}</div>}
        <div className="row" style={{ justifyContent: "space-between", marginTop: 2, gap: 8, flexWrap: "wrap" }}>
          <span className="row" style={{ gap: 6 }}><span className="badge">{blockTitle(t.block)}</span>{t.deal_status && <span className="badge" style={{ background: toneOf(t.deal_status)[0], color: toneOf(t.deal_status)[1], fontWeight: 600 }}>{STATUS_TITLES[t.deal_status] || t.deal_status}</span>}</span>
          <span className="row" style={{ gap: 6 }}>
            <Link to={href(t)} className="btn" style={{ padding: "4px 12px", fontSize: 12, textDecoration: "none" }}>Открыть →</Link>
            {t.can_close && <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => done(t)} title="задача сделана вне системы">Выполнено</button>}
          </span>
        </div>
      </div>);
  };
  return (
    <>
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Мои задачи" }]} />
      <div className={"row" + (nv ? " tk-head" : "")} style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ margin: 0 }}>Мои задачи <span className="muted" style={{ fontSize: 16, fontWeight: 400 }}>{tasks.length}{tasks.some((t) => (t.days_left ?? 0) < 0) && <> · <span style={{ color: "var(--bad)" }}>просрочено {tasks.filter((t) => (t.days_left ?? 0) < 0).length}</span></>}</span></h1>
        <div className="row" style={{ gap: 8 }}>
          <input className="tk-search" placeholder="🔍 сделка, клиент, задача" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} aria-label="Поиск задач" />
          <div className="tabs" style={{ margin: 0 }}>
            <button className={view === "cards" ? "active" : ""} onClick={() => setView("cards")}>Карточки</button>
            <button className={view === "table" ? "active" : ""} onClick={() => setView("table")}>Таблица</button>
          </div>
        </div>
      </div>
      {msg && <div className={`alert ${nv && ok ? "ok" : "info"}`} role="status">{msg}</div>}
      {shown.length === 0 && (nv
        ? <Empty title={q ? `По запросу «${q}» задач нет` : "Задач нет"} hint={q ? "Поиск — по номеру сделки, клиенту, задаче и оборудованию." : "Новые задачи появятся здесь, когда сделки дойдут до вашего этапа."}
            action={q ? <button className="btn ghost" onClick={() => setQ("")}>Сбросить поиск</button> : undefined} />
        : <div className="card muted">Задач нет{q ? " по запросу" : ""}.</div>)}
      {view === "cards" && GROUPS.map((g) => {
        const items = shown.filter(g.test);
        if (!items.length) return null;
        return (
          nv ? <section key={g.key} className={"tk-group " + g.key}>
            <h2>{g.title} <span className="tk-count">{items.length}</span></h2>
            <div className="tk-grid">{items.map(card)}</div>
          </section> :
          <section key={g.key} style={{ marginTop: 14 }}>
            <h2 style={{ margin: "0 0 8px", fontSize: 14, color: g.key === "late" ? "var(--bad)" : "var(--muted)", letterSpacing: ".4px", textTransform: "uppercase" }}>{g.title} · {items.length}</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 10 }}>{items.map(card)}</div>
          </section>);
      })}
      {view === "table" && shown.length > 0 && (
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <table>
            <thead><tr><th>Сделка</th><th>Клиент</th><th>Задача</th><th>Блок</th><th>Срок</th><th>Статус сделки</th><th /></tr></thead>
            <tbody>
              {shown.map((t) => (
                <tr key={t.id} style={(t.days_left ?? 0) < 0 ? { boxShadow: "inset 3px 0 0 var(--bad)" } : undefined}>
                  <td><Link to={href(t)}>{t.deal_number}</Link></td><td>{t.client_name}{t.site && <div className="muted" style={{ fontSize: 11 }}>{t.site}</div>}</td>
                  <td>{t.title}{t.hint && <div className="muted" style={{ fontSize: 11 }}>{t.hint}</div>}</td><td>{blockTitle(t.block)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{fmtDate(t.due)} <span style={{ color: (t.days_left ?? 0) < 0 ? "var(--bad)" : "var(--muted)", fontSize: 12 }}>{dueText(t)}</span></td>
                  <td>{t.deal_status ? STATUS_TITLES[t.deal_status] || t.deal_status : "—"}</td>
                  <td>{t.can_close && <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => done(t)}>Выполнено</button>}</td>
                </tr>))}
            </tbody>
          </table>
        </div>)}
    </>
  );
}
