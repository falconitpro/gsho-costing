import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";
import AssignmentDialog from "../components/workplace/AssignmentDialog";

// Карточка направления (D-155, ТЗ «Рабочее место ЗГД» v2 §4.5): руководитель и замещение, сотрудники и нагрузка,
// объекты с отклонениями, поручения, история. Данные — те же, что на «Пульте» (GET /api/workplace/executive/direction/:key).

type Obj = { kind: string; title: string; url: string; note: string; bad: boolean };
type Staff = { id: string; name: string; position: string; is_head: boolean; tasks_open: number; tasks_overdue: number; assignments_open: number };
type A = { id: string; number: string; title: string; assignee: string; executor: string; status: string; status_title: string; due_on: string; overdue: boolean; priority: string; priority_title: string };
type Dir = { key: string; title: string; tone: string; head: { id: string; name: string; position: string; deputy: { name: string; temporary: boolean; to: string | null } | null } | null;
  results: string[]; deviations: string[]; url: string; objects: Obj[]; staff: Staff[]; assignments: A[]; history: { at: string; who: string; text: string; url: string }[];
  meta: { generated_at: string } };
const ddmm = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const PR: Record<string, string> = { critical: "bad", high: "warn", normal: "info" };

export default function DirectionPage({ me }: { me: Me }) {
  const { key = "" } = useParams();
  const [d, setD] = useState<Dir | null>(null);
  const [err, setErr] = useState("");
  const [asg, setAsg] = useState<{ assignee_id?: string } | null>(null);
  const load = () => api.get<Dir>(`/api/workplace/executive/direction/${key}`).then(setD).catch((e: Error) => setErr(e.message));
  useEffect(() => { load(); }, [key, me.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="dir-page"><p className="bad">{err}</p><Link to="/">← Пульт</Link></div>;
  if (!d) return <div className="dir-page"><p className="muted">Загрузка…</p></div>;
  const open = d.assignments.filter((a) => !["done", "cancelled"].includes(a.status));
  return (
    <div className="dir-page">
      <div className="muted dir-crumbs"><Link to="/">Пульт</Link> → Направления</div>
      <header className="dir-head">
        <div><h1>{d.title}</h1>
          <div className="muted">{d.head ? <>Руководитель: <b>{d.head.name}</b>{d.head.position ? `, ${d.head.position}` : ""}</> : "Руководитель не назначен — назначьте на пульте («Направления → Руководители»)"}
            {d.head?.deputy && <span className="wp-exec-dep"> · {d.head.deputy.temporary ? `замещение${d.head.deputy.to ? ` до ${ddmm(d.head.deputy.to)}` : ""}` : "заместитель"}: {d.head.deputy.name}</span>}</div></div>
        <div className="dir-act">
          {d.head && <button className="btn add" onClick={() => setAsg({ assignee_id: d.head!.id })}>Поручить руководителю</button>}
          <Link className="btn ghost" to={d.url}>Реестр →</Link>
        </div>
      </header>
      <section className="dir-grid">
        <div className="card"><h2>Результат</h2><ul className="wp-exec-mini">{d.results.map((r) => <li key={r}>{r}</li>)}</ul></div>
        <div className={`card ${d.deviations.length ? "dir-bad" : ""}`}><h2>Отклонения</h2>
          {d.deviations.length ? <ul className="wp-exec-mini">{d.deviations.map((r) => <li key={r} className="bad">{r}</li>)}</ul> : <p className="muted">Нет.</p>}</div>
      </section>
      <section className="card">
        <h2>Объекты с отклонениями <span className="muted">{d.objects.length}</span></h2>
        {d.objects.length ? <ul className="wp-exec-drill">{d.objects.map((o, i) => (
          <li key={i}><span><span className="muted">{o.kind} · </span><Link to={o.url}>{o.title}</Link></span><span className={o.bad ? "bad" : "muted"}>{o.note}</span></li>))}</ul>
          : <p className="muted">Отклонений нет.</p>}
      </section>
      <section className="dir-grid">
        <div className="card"><h2>Сотрудники</h2>
          <table className="asg-table"><thead><tr><th>Сотрудник</th><th>Задачи</th><th>Поручения</th><th></th></tr></thead>
            <tbody>{d.staff.map((u) => (
              <tr key={u.id}><td>{u.name}{u.is_head ? <span className="muted"> · руководитель</span> : null}{u.position && <div className="muted">{u.position}</div>}</td>
                <td>{u.tasks_open}{u.tasks_overdue ? <span className="bad"> · просрочено {u.tasks_overdue}</span> : null}</td><td>{u.assignments_open}</td>
                <td><button className="btn ghost" onClick={() => setAsg({ assignee_id: u.id })}>Поручить</button></td></tr>))}
              {!d.staff.length && <tr><td colSpan={4} className="muted">Сотрудников с ролью направления нет.</td></tr>}</tbody></table>
        </div>
        <div className="card"><h2>Поручения <span className="muted">открыто {open.length}</span></h2>
          <ul className="wp-exec-mini">{d.assignments.slice(0, 12).map((a) => (
            <li key={a.id}><Link to={`/assignments?open=${a.id}`}><span className={`wp-exec-prio ${PR[a.priority]}`}>{a.priority_title}</span> {a.title}</Link>
              <span className={a.overdue ? "bad" : "muted"}>{a.number} · {a.executor || a.assignee} · {a.status_title} · до {ddmm(a.due_on)}</span></li>))}
            {!d.assignments.length && <li className="muted">Поручений не было.</li>}</ul>
        </div>
      </section>
      <section className="card"><h2>История</h2>
        <ol className="asg-hist">{d.history.map((h, i) => (
          <li key={i}><span className="muted">{new Date(h.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span> <b>{h.who}</b>: {h.url ? <Link to={h.url}>{h.text}</Link> : h.text}</li>))}
          {!d.history.length && <li className="muted">Пока пусто.</li>}</ol>
      </section>
      <p className="muted wp-exec-note">Обновлено {new Date(d.meta.generated_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК.</p>
      {asg && <AssignmentDialog prefill={{ ...asg, source_url: `/directions/${d.key}`, source_title: `Направление «${d.title}»` }} onClose={() => setAsg(null)} onDone={() => { setAsg(null); load(); }} />}
    </div>);
}
