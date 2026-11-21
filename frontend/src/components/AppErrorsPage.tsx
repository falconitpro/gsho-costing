import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import { askConfirm } from "./Dialogs";

type Row = { id: string; source: string; kind: string; message: string; where: string; trace: string; count: number; users: string[]; request_id: string;
  release: string; first_seen: string | null; last_seen: string | null; resolved_at: string | null; resolved_by: string };

/** D-117: «Настройки → Ошибки системы» — необработанные ошибки сервера и страниц. Видят администратор и ЗГД. */
type Jobs = { worker_alive: boolean; last_cycle_ok: string | null; stale: boolean; failing: string[];
  jobs: { job: string; last_ok: string | null; last_error: string; error_at: string | null; duration_ms: number | null }[] };
const JOB_TITLES: Record<string, string> = { "fx rates": "курсы ЦБ", "parts SLA check": "запчасти: сроки", "parts TO reminders": "запчасти: ТО машин",
  "overdue check": "просроченные задачи", "notification cleanup": "уведомления", "office due watch": "Канцелярия: сроки и ознакомление",
  "fd visa escalation": "виза ФД → ЗГД", "draft watch": "черновики сделок", "poa expiry check": "доверенности", "contracts expiry check": "договоры: сроки",
  "claims ladder": "претензии", "access review reminders": "пересмотр прав", "assistant purge": "помощник: срок хранения",
  "backup status refresh": "резервные копии", "one-time data repairs": "разовые исправления данных", "scanner check": "антивирус" };

/** D-143: состояние фоновых заданий — над списком ошибок. */
function JobsBox() {
  const [j, setJ] = useState<Jobs | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { api.get<Jobs>("/api/admin/jobs").then(setJ).catch(() => setJ(null)); }, []);
  if (!j) return null;
  const dt = (s: string | null) => (s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
  return (
    <div className={"alert " + (j.stale ? "error" : j.failing.length ? "warn" : "ok") + " jobs-box"}>
      <div><b>Фоновые задания</b> (напоминания, курсы ЦБ, сроки писем, визы): {j.stale ? "не выполнялись больше 2 часов" : `последний цикл ${dt(j.last_cycle_ok)}`}
        {" · "}{j.worker_alive ? "выполняет отдельный процесс" : "выполняет основной процесс (отдельный не запущен)"}
        {j.failing.length > 0 && <> · с ошибкой: {j.failing.map((x) => JOB_TITLES[x] || x).join(", ")}</>}
        {" "}<a href="#" onClick={(e) => { e.preventDefault(); setOpen(!open); }}>{open ? "скрыть" : "по заданиям"}</a></div>
      {open && <table className="tbl jobs-tbl"><tbody>{j.jobs.map((x) => (
        <tr key={x.job}><td>{JOB_TITLES[x.job] || x.job}</td><td>{dt(x.last_ok)}</td><td className="muted">{x.duration_ms != null ? `${(x.duration_ms / 1000).toFixed(1)} с` : ""}</td>
          <td className={x.last_error ? "txt-bad" : "muted"}>{x.last_error ? `${dt(x.error_at)}: ${x.last_error}` : "ок"}</td></tr>))}</tbody></table>}
    </div>
  );
}

export default function AppErrorsPage() {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.get<{ rows: Row[] }>(`/api/admin/app-errors?status=${status}`).then((r) => setRows(r.rows)).catch((e) => setErr((e as Error).message));
  useEffect(() => { setRows(null); load(); }, [status]);
  const dt = (s: string | null) => (s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
  async function resolve(r: Row) {
    if (!(await askConfirm(`Отметить «${r.kind} — ${r.where}» исправленной? Если ошибка повторится, запись откроется снова и придёт уведомление.`))) return;
    api.post(`/api/admin/app-errors/${r.id}/resolve`).then(load).catch((e) => setErr((e as Error).message));
  }
  return (
    <div className="card">
      <JobsBox />
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div className="seg" role="group" aria-label="Ошибки">
          <button type="button" className={status === "open" ? "on" : ""} onClick={() => setStatus("open")}>Открытые</button>
          <button type="button" className={status === "resolved" ? "on" : ""} onClick={() => setStatus("resolved")}>Исправленные</button>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>Ошибки сервера и страниц у сотрудников. Без содержимого запросов и файлов. Новая ошибка — уведомление администратору и ЗГД.</span>
      </div>
      {err && <div className="alert error">{err}</div>}
      {!rows ? <div className="muted" style={{ marginTop: 10 }}>Загрузка…</div>
        : rows.length === 0 ? <div className="muted" style={{ marginTop: 10 }}>{status === "open" ? "Открытых ошибок нет." : "Исправленных ошибок пока нет."}</div>
        : <table style={{ marginTop: 10 }}>
            <thead><tr><th>Последний раз</th><th>Где</th><th>Ошибка</th><th className="num">Раз</th><th>У кого</th><th /></tr></thead>
            <tbody>{rows.map((r) => [
              <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
                <td style={{ whiteSpace: "nowrap" }}>{dt(r.last_seen)}<div className="muted" style={{ fontSize: 11 }}>впервые {fmtDate(r.first_seen)}</div></td>
                <td><span className="badge">{r.source === "server" ? "сервер" : "страница"}</span> <code style={{ fontSize: 12 }}>{r.where || "—"}</code></td>
                <td><b>{r.kind}</b><div className="muted" style={{ fontSize: 12 }}>{r.message.slice(0, 160)}</div></td>
                <td className="num">{r.count}</td>
                <td style={{ fontSize: 12 }}>{r.users.join(", ") || "—"}</td>
                <td>{status === "open" ? <button className="btn ghost" onClick={(e) => { e.stopPropagation(); resolve(r); }}>Исправлено</button>
                  : <span className="muted" style={{ fontSize: 12 }}>{dt(r.resolved_at)} · {r.resolved_by}</span>}</td>
              </tr>,
              open === r.id && <tr key={r.id + "-d"}><td colSpan={6} style={{ background: "#fafafa" }}>
                <div style={{ fontSize: 12 }}>Номер запроса: <code>{r.request_id || "—"}</code>{r.release ? <> · версия {r.release}</> : null}</div>
                <div style={{ fontSize: 12, marginTop: 4 }}>{r.message}</div>
                {r.trace && <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", margin: "6px 0 0" }}>{r.trace}</pre>}
              </td></tr>,
            ])}</tbody>
          </table>}
    </div>);
}
