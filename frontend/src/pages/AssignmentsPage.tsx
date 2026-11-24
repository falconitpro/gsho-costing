import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";
import AssignmentDialog, { type AsgPrefill } from "../components/workplace/AssignmentDialog";
import { Loading, Empty } from "../components/States";
import { useNewUiFlag } from "../uiMode";

// Поручения ЗГД (D-154, ТЗ «Рабочее место ЗГД» v2 §5): реестр «На контроле», «Мне», «Моему подразделению»; карточка с историей.
// Полный цикл: новое → принято → в работе → заблокировано / на проверке → на доработке → выполнено; отменено.

type A = { id: string; number: string; kind: string; kind_title: string; title: string; body: string; expected_result: string; priority: string; priority_title: string; mode: string; direction: string;
  author: string; assignee: string; executor: string; head: string; status: string; status_title: string; overdue: boolean; overdue_days: number; due_on: string; first_due_on: string;
  rescheduled: boolean; source_url: string; source_title: string; report: string; block_reason: string; actions: string[]; my_role: string[] };
type H = { at: string; by: string; action: string; action_title: string; from: string; to: string; comment: string; data: Record<string, string> };
type F = { id: string; name: string; size: number; by: string; at: string; url: string; can_remove: boolean };
type Full = A & { history: H[]; files: F[]; can_attach: boolean };
type U = { id: string; name: string; position: string };

const ddmm = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
const BTN: Record<string, string> = { accept: "Принять", start: "Начать", block: "Заблокировано", unblock: "Снять блокировку", submit: "Сдать на проверку", approve: "Принять результат",
  return: "Вернуть на доработку", reschedule: "Перенести срок", delegate: "Передать исполнителю", cancel: "Отменить", comment: "Комментарий" };
const NEED: Record<string, "comment" | "report" | "date" | "user" | ""> = { block: "comment", submit: "report", return: "comment", reschedule: "date", delegate: "user", cancel: "comment", comment: "comment" };
const PR_CLS: Record<string, string> = { critical: "bad", high: "warn", normal: "info" };
const STATUS_F: [string, string][] = [["open", "открытые"], ["inwork", "в работе"], ["today", "срок сегодня"], ["overdue", "просроченные"], ["review", "на проверке"],
  ["blocked", "заблокированные"], ["done", "выполненные"], ["cancelled", "отменённые"], ["any", "все"]];
const CLOSED = ["done", "cancelled"];
/** Срок словами относительно сегодня (МСК): «сегодня», «завтра», «через N дн.», «просрочено N дн.». */
const rel = (a: { due_on: string; overdue: boolean; overdue_days: number; status: string }) => {
  if (CLOSED.includes(a.status)) return "";
  if (a.overdue) return `просрочено ${a.overdue_days} дн.`;
  const today = new Date(new Date().toLocaleString("en-US", { timeZone: "Europe/Moscow" }));
  const n = Math.round((Date.UTC(+a.due_on.slice(0, 4), +a.due_on.slice(5, 7) - 1, +a.due_on.slice(8, 10)) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 864e5);
  return n <= 0 ? "сегодня" : n === 1 ? "завтра" : `через ${n} дн.`;
};

function Card({ id, onChanged, onClose }: { id: string; onChanged: () => void; onClose: () => void }) {
  const nv = useNewUiFlag();
  const [a, setA] = useState<Full | null>(null);
  const [err, setErr] = useState("");          // ошибка действия — показывается в карточке, карточка не пропадает
  const [loadErr, setLoadErr] = useState("");
  const [act, setAct] = useState("");
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [user, setUser] = useState("");
  const [users, setUsers] = useState<U[]>([]);
  useEffect(() => { setA(null); setErr(""); setLoadErr(""); api.get<Full>(`/api/management/assignments/${id}`).then(setA).catch((e: Error) => setLoadErr(e.message)); }, [id]);
  useEffect(() => { if (act === "delegate" && !users.length) api.get<{ users: U[] }>("/api/management/assignees").then((r) => setUsers(r.users)).catch(() => undefined); }, [act, users.length]);
  if (loadErr) return <aside className="card asg-card"><p className="bad">{loadErr}</p></aside>;
  if (!a) return <aside className="card asg-card"><Loading p /></aside>;
  const run = (action: string) => {
    const need = NEED[action] || "";
    if (need && act !== action) { setAct(action); setText(""); setDue(a.due_on); setUser(""); setErr(""); return; }
    api.post<Full>(`/api/management/assignments/${a.id}/action`, { action, comment: need === "comment" || need === "date" ? text : "", report: need === "report" ? text : "",
      due_on: need === "date" ? due : null, executor_id: need === "user" ? user : null })
      .then((r) => { setA(r); setAct(""); setErr(""); onChanged(); }).catch((e: Error) => setErr(e.message));
  };
  const need = NEED[act] || "";
  const attach = (list: FileList | null) => {
    if (!list || !list.length) return;
    const fd = new FormData();
    Array.from(list).forEach((f) => fd.append("files", f));
    api.form<Full>(`/api/management/assignments/${a.id}/files`, fd).then((r) => { setA(r); setErr(""); onChanged(); }).catch((e: Error) => setErr(e.message));
  };
  const unattach = (f: F) => api.del<Full>(`/api/management/assignments/${a.id}/files/${f.id}`).then((r) => { setA(r); onChanged(); }).catch((e: Error) => setErr(e.message));
  const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1).replace(".", ",")} МБ` : `${Math.max(1, Math.round(n / 1024))} КБ`);
  return (
    <aside className="card asg-card" aria-label={`Поручение ${a.number}`}>
      <div className="asg-card-head"><b>{a.number}</b> <span className={`wp-exec-prio ${PR_CLS[a.priority]}`}>{a.priority_title}</span> <span className="asg-st">{a.status_title}</span>
        {a.overdue && <span className="bad"> · просрочено {a.overdue_days} дн.</span>}<button className="btn ghost asg-x" onClick={onClose} aria-label="Закрыть">✕</button></div>
      <h2>{a.kind === "explain" ? "Пояснение: " : ""}{a.title}</h2>
      {a.body && <p className="asg-body">{a.body}</p>}
      {nv ? <>
        {a.status === "review" && <div className="alert info asg-flag">Результат сдан и ждёт проверки.</div>}
        {a.block_reason && <div className="alert warn asg-flag"><b>Заблокировано:</b> {a.block_reason}</div>}
        {a.expected_result && <div className="asg-expected"><span>Ожидаемый результат</span>{a.expected_result}</div>}
        {a.report && <div className="asg-report"><span>Отчёт исполнителя</span>{a.report}</div>}
        <dl className="asg-dl">
          <dt>Кому поручено</dt><dd>{a.assignee}{a.mode === "head" ? ` — руководитель${a.direction ? ` («${a.direction}»)` : ""}` : ""}</dd>
          <dt>Исполнитель</dt><dd>{a.executor || <span className="muted">{a.mode === "head" ? "руководитель ещё не передал" : "тот же сотрудник"}</span>}</dd>
          {a.head && <><dt>Руководитель видит</dt><dd>{a.head}</dd></>}
          <dt>Автор</dt><dd>{a.author}</dd>
          <dt>Срок</dt><dd>{ddmm(a.due_on)}{rel(a) && <span className={a.overdue ? "bad" : "muted"}> · {rel(a)}</span>}{a.rescheduled && <div className="muted">перенесён, первоначально {ddmm(a.first_due_on)}</div>}</dd>
          {a.source_url && <><dt>Источник</dt><dd><Link to={a.source_url}>{a.source_title || "открыть"}</Link></dd></>}
        </dl></> : <>
      <dl className="asg-dl">
        {a.expected_result && <><dt>Ожидаемый результат</dt><dd><b>{a.expected_result}</b></dd></>}
        <dt>Кому</dt><dd>{a.assignee}{a.mode === "head" ? ` — руководитель${a.direction ? ` («${a.direction}»)` : ""}` : ""}</dd>
        {a.executor && <><dt>Исполнитель</dt><dd>{a.executor}</dd></>}
        {a.head && <><dt>Руководитель видит</dt><dd>{a.head}</dd></>}
        <dt>Автор</dt><dd>{a.author}</dd>
        <dt>Срок</dt><dd>{ddmm(a.due_on)}{a.rescheduled ? ` (первоначально ${ddmm(a.first_due_on)})` : ""}</dd>
        {a.source_url && <><dt>Объект</dt><dd><Link to={a.source_url}>{a.source_title || "открыть"}</Link></dd></>}
        {a.block_reason && <><dt>Блокировка</dt><dd className="bad">{a.block_reason}</dd></>}
        {a.report && <><dt>Отчёт</dt><dd>{a.report}</dd></>}
      </dl></>}
      {(a.files.length > 0 || a.can_attach) && <div className="asg-files">
        <b>Подтверждения</b>
        <ul>{a.files.map((f) => (
          <li key={f.id}><button className="asg-num" onClick={() => api.download(f.url, f.name).catch((e: Error) => setErr(e.message))}>{f.name}</button>
            <span className="muted"> {kb(f.size)} · {f.by}{nv && f.at ? ` · ${new Date(f.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}</span>{f.can_remove && <button className="btn ghost asg-rm" onClick={() => unattach(f)} aria-label={`Убрать ${f.name}`}>✕</button>}</li>))}
          {!a.files.length && <li className="muted">Файлов нет.</li>}</ul>
        {a.can_attach && <label className="btn ghost asg-up">Приложить файл<input type="file" multiple onChange={(e) => { attach(e.target.files); e.target.value = ""; }} /></label>}
      </div>}
      {a.actions.length > 0 && <div className="asg-acts">{a.actions.map((x) => (
        <button key={x} className={`btn ${x === "approve" || x === "submit" || x === "accept" ? "add" : "ghost"}${act === x ? " cur" : ""}`} onClick={() => run(x)}>{BTN[x]}</button>))}</div>}
      {err && <p className="bad asg-err" role="alert">{err}</p>}
      {need && <div className="asg-form">
        {(need === "comment" || need === "report" || need === "date") && <label className="f"><span>{need === "report" ? "Результат: что сделано (файлы-подтверждения приложите выше)" : act === "comment" ? "Комментарий" : "Причина"}</span>
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} /></label>}
        {need === "date" && <label className="f"><span>Новый срок</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>}
        {need === "user" && <label className="f"><span>Кому передать выполнение</span><select value={user} onChange={(e) => setUser(e.target.value)}>
          <option value="">выберите</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
        <div className="asg-actions"><button className="btn ghost" onClick={() => setAct("")}>Отмена</button><button className="btn add" onClick={() => run(act)}>{BTN[act]}</button></div>
      </div>}
      <h3>История</h3>
      <ol className={"asg-hist" + (nv ? " nv-hist" : "")}>{a.history.map((h, i) => (
        <li key={i}><span className="muted">{new Date(h.at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span> <b>{h.by}</b>: {h.action_title}
          {h.action === "reschedule" && h.data?.from ? ` ${ddmm(h.data.from)} → ${ddmm(h.data.to)}` : ""}{h.comment ? ` — ${h.comment}` : ""}</li>))}</ol>
    </aside>);
}

export default function AssignmentsPage({ me }: { me: Me }) {
  const nv = useNewUiFlag();
  const isGd = me.roles.some((r) => r.code === "gd");
  const [sp, setSp] = useSearchParams();
  const [scope, setScope] = useState(sp.get("scope") || (isGd ? "control" : "mine"));
  const [status, setStatus] = useState(sp.get("status") || "open");
  const [rows, setRows] = useState<A[] | null>(null);
  const [err, setErr] = useState("");
  const open = sp.get("open") || "";
  const creating = sp.get("new") === "1";
  useEffect(() => { const st = sp.get("status"); const sc = sp.get("scope"); if (st) setStatus(st); if (sc) setScope(sc); }, [sp]);
  const load = useCallback(() => { api.get<{ items: A[] }>(`/api/management/assignments?scope=${scope}&status=${status}`).then((r) => setRows(r.items)).catch((e: Error) => setErr(e.message)); }, [scope, status]);
  useEffect(load, [load]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n); };
  // новый вид: область и статус — в адресе (ссылку можно переслать, «назад» возвращает отбор)
  const pickScope = (v: string) => { if (nv) { const n = new URLSearchParams(sp); n.set("scope", v); n.delete("open"); setSp(n); } setScope(v); };
  const pickStatus = (v: string) => { if (nv) { const n = new URLSearchParams(sp); n.set("status", v); setSp(n); } setStatus(v); };
  const prefill: AsgPrefill = { title: sp.get("title") || "", source_url: sp.get("src") || "", source_title: sp.get("srct") || "", source_key: sp.get("srck") || "",
    kind: sp.get("kind") === "explain" ? "explain" : "task", priority: sp.get("prio") || "normal" };
  const scopes: [string, string][] = [["mine", "Мне"], ["control", "На контроле"], ["team", "Моему подразделению"], ...(isGd ? [["all", "Все"] as [string, string]] : [])];
  return (
    <div className="asg-page">
      <header className="asg-top"><h1>Поручения</h1>{isGd && <button className="btn add" onClick={() => set("new", "1")}>Создать поручение</button>}</header>
      <div className="asg-filters">
        <nav className="wp-exec-tabs" aria-label="Чьи поручения">{scopes.map(([k, t]) => <button key={k} className={scope === k ? "cur" : ""} aria-pressed={scope === k} onClick={() => pickScope(k)}>{t}</button>)}</nav>
        <select value={status} onChange={(e) => pickStatus(e.target.value)} aria-label="Статус">
          {STATUS_F.map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
      </div>
      {err && <p className="bad">{err}</p>}
      <div className={`asg-layout${open ? " with-card" : ""}`}>
        <div className="card asg-list">
          {rows === null ? <Loading p /> : rows.length === 0 ? (nv
            ? <Empty card={false} title="Поручений нет" hint={`Отбор: ${scopes.find(([k]) => k === scope)?.[1] || scope} · ${STATUS_F.find(([k]) => k === status)?.[1] || status}.`}
                action={status !== "open" ? <button className="btn ghost" onClick={() => pickStatus("open")}>Показать открытые</button> : undefined} />
            : <p className="muted">Поручений нет.</p>) :
            <table className="asg-table"><thead><tr><th>№</th><th>Поручение</th><th>Кому</th><th>Срок</th><th>Статус</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className={`${r.overdue ? "late" : ""}${open === r.id ? " cur" : ""}`} onClick={() => set("open", r.id)}>
                  <td><button className="asg-num" onClick={() => set("open", r.id)}>{r.number}</button></td>
                  <td><span className={`wp-exec-prio ${PR_CLS[r.priority]}`}>{r.priority_title}</span> {r.kind === "explain" ? "Пояснение: " : ""}{r.title}</td>
                  {nv ? <td>{r.assignee}{r.executor && r.executor !== r.assignee ? <div className="muted">исполнитель: {r.executor}</div> : null}{r.head ? <div className="muted">рук.: {r.head}</div> : null}</td>
                    : <td>{r.assignee}{r.executor ? ` → ${r.executor}` : ""}{r.head ? <div className="muted">рук.: {r.head}</div> : null}</td>}
                  {nv ? <td className="asg-due">{ddmm(r.due_on)}{rel(r) && <div className={r.overdue ? "bad" : "muted"}>{rel(r)}</div>}</td>
                    : <td className={r.overdue ? "bad" : ""}>{ddmm(r.due_on)}{r.overdue ? ` · ${r.overdue_days} дн.` : ""}</td>}
                  <td>{nv ? <span className={`badge asg-badge ${r.status}`}>{r.status_title}</span> : r.status_title}</td>
                </tr>))}</tbody></table>}
        </div>
        {open && <Card id={open} onChanged={load} onClose={() => set("open", "")} />}
      </div>
      {creating && <AssignmentDialog prefill={prefill} onClose={() => { const n = new URLSearchParams(sp); ["new", "title", "src", "srct", "srck", "kind", "prio"].forEach((k) => n.delete(k)); setSp(n); }}
        onDone={(id) => { const n = new URLSearchParams(); n.set("open", id); setSp(n); load(); }} />}
    </div>);
}
