import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";

/** «Настройки → Резервные копии» (docs/operations/10 §8): только факты из статуса хоста и истории запусков.
 *  Ручного запуска из CRM нет (D-026) — показываем фиксированную команду для администратора сервера. Секретов здесь нет и быть не может. */

type Run = {
  run_key: string; reason: string; status: "running" | "success" | "failed"; started_at: string | null; finished_at: string | null;
  snapshot_id: string | null; git_commit: string | null; schema_revision: string | null; storage_provider: string;
  database_bytes: number | null; files_bytes: number | null; files_count: number | null; check_status: string; checked_at: string | null;
  retention_status: string; error_code: string; error_text: string; error_summary: string;
};
type Status = {
  state: "not_configured" | "running" | "ok" | "warning" | "failed" | "stale"; configured: boolean; stale: boolean; stale_after_hours: number;
  host_status_available: boolean; status_errors: string[]; last_run: Run | null; last_success: Run | null; next_run_at: string;
  schedule: string; retention: string; timeweb_cutover_allowed: boolean; manual_command: string; runbook: string;
};

const REASON: Record<string, string> = { daily: "ежедневная", pre_deploy: "перед обновлением", manual: "ручная", restore_check: "проверка восстановления" };
const STATE: Record<Status["state"], [string, string]> = {
  not_configured: ["Не настроено", ""], running: ["Выполняется", "info"], ok: ["В порядке", "ok"], warning: ["Есть предупреждение", "warn"],
  failed: ["Ошибка", "bad"], stale: ["Нет свежей копии", "bad"],
};
const RUN: Record<Run["status"], [string, string]> = { running: ["выполняется", "info"], success: ["успешно", "ok"], failed: ["ошибка", "bad"] };
const fmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
const when = (iso: string | null) => (iso ? `${fmt.format(new Date(iso))} МСК` : "—");
const size = (b: number | null) => {
  if (b == null) return "—";
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ"]; let v = b, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: i ? 1 : 0 })} ${u[i]}`;
};
const ago = (iso: string | null) => {
  if (!iso) return "";
  const h = (Date.now() - new Date(iso).getTime()) / 3600_000;
  return h < 1 ? "меньше часа назад" : h < 48 ? `${Math.floor(h)} ч назад` : `${Math.floor(h / 24)} дн. назад`;
};
const sub = (s: string) => (s === "ok" ? <span className="badge ok">ok</span> : s === "failed" ? <span className="badge bad">ошибка</span> : s === "skipped" ? <span className="badge">пропущено</span> : <span className="muted">—</span>);

export default function BackupStatusBlock() {
  const [st, setSt] = useState<Status | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try {
      const [s, r] = await Promise.all([api.get<Status>("/api/operations/backups/status"), api.get<{ total: number; items: Run[] }>("/api/operations/backups/runs?limit=30")]);
      setSt(s); setRuns(r.items); setTotal(r.total);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 403 ? "Нет доступа к статусу резервных копий" : (e as Error).message || "Не удалось загрузить статус");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { setCopied(false); }
  };

  if (loading && !st) return <div className="card muted">Загрузка статуса резервных копий…</div>;
  if (err) return <div className="card"><div className="alert error" role="alert">{err}</div><button className="btn secondary" onClick={load}>Повторить</button></div>;
  if (!st) return null;
  const [stTitle, stTone] = STATE[st.state];
  const lr = st.last_run, ls = st.last_success;

  return (
    <>
      <div className="card backup-status">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Резервные копии <span className={`badge ${stTone}`} style={{ verticalAlign: "middle", marginLeft: 6 }}>{stTitle}</span></h2>
          <button className="btn secondary" onClick={load} disabled={loading}>{loading ? "Обновление…" : "Обновить"}</button>
        </div>
        <p className="muted" style={{ margin: "6px 0 10px" }}>
          Зашифрованные копии базы и вложений в Yandex Object Storage: {st.schedule}, а также перед обновлением. Хранение: {st.retention}.
        </p>

        {st.state === "not_configured" && (
          <div className="alert info">Копирование в Яндекс ещё не настроено на сервере: статуса запусков нет. Старые копии Timeweb продолжают работать.
            Установка — <code>deploy/backup/install.md</code>.</div>)}
        {st.state === "failed" && lr && (
          <div className="alert error" role="alert"><b>Последняя копия не создана.</b> {lr.error_text || "Ошибка без описания"}{lr.error_summary && <> ({lr.error_summary})</>}.
            {ls ? <> Последняя успешная — {when(ls.finished_at)}.</> : " Успешных копий ещё не было."} Порядок действий — {st.runbook}, раздел 6.</div>)}
        {st.state === "stale" && (
          <div className="alert error" role="alert"><b>Нет успешной копии дольше {st.stale_after_hours} ч.</b>{" "}
            {ls ? <>Последняя успешная — {when(ls.finished_at)} ({ago(ls.finished_at)}).</> : "Успешных копий ещё не было."} Проверьте таймер и журнал на сервере.</div>)}
        {st.state === "warning" && lr && (
          <div className="alert warn">{lr.retention_status === "failed" ? "Копия создана, но очистка старых снимков не прошла" : "Проверка целостности репозитория не прошла"} — см. {st.runbook}, раздел 6.</div>)}
        {st.status_errors.length > 0 && (
          <div className="alert warn">Файл статуса на сервере не прошёл проверку: {st.status_errors.join("; ")}</div>)}

        {st.configured && (
          <div className="grid backup-kpis">
            <div className="kpi"><span className="v">{ls ? when(ls.finished_at) : "—"}</span><span className="l">последняя успешная копия{ls ? ` · ${ago(ls.finished_at)}` : ""}</span></div>
            <div className="kpi"><span className="v">{lr ? <span className={`badge ${RUN[lr.status][1]}`}>{RUN[lr.status][0]}</span> : "—"}</span>
              <span className="l">последний запуск · {lr ? `${REASON[lr.reason] || lr.reason}, ${when(lr.started_at)}` : "—"}</span></div>
            <div className="kpi"><span className="v"><code>{ls?.snapshot_id || "—"}</code></span><span className="l">снимок restic</span></div>
            <div className="kpi"><span className="v">{size(ls?.database_bytes ?? null)} / {size(ls?.files_bytes ?? null)}</span>
              <span className="l">база / вложения{ls?.files_count != null ? ` (${ls.files_count.toLocaleString("ru-RU")} файлов)` : ""}</span></div>
            <div className="kpi"><span className="v">{sub(ls?.check_status || "")}</span><span className="l">проверка целостности</span></div>
            <div className="kpi"><span className="v">{when(st.next_run_at)}</span><span className="l">следующий запуск по расписанию</span></div>
          </div>)}

        <div className="backup-cmd">
          <div className="muted" style={{ fontSize: 13, marginBottom: 4 }}>Ручная копия — команда для администратора сервера (из CRM копия не запускается):</div>
          <div className="row">
            <code className="backup-code">{st.manual_command}</code>
            <button className="btn secondary" onClick={() => copy(st.manual_command)} aria-label="Скопировать команду">{copied ? "Скопировано" : "Скопировать"}</button>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          Отключение копий Timeweb: <b>{st.timeweb_cutover_allowed ? "разрешено владельцем" : "не разрешено"}</b> — только после проверки восстановления на отдельной ВМ и явного подтверждения владельца.
        </div>
      </div>

      {st.configured && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>История запусков <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>{total > runs.length ? `последние ${runs.length} из ${total}` : total}</span></h2>
          {runs.length === 0 ? <div className="muted">Запусков пока нет.</div> : (
            <div className="backup-table">
              <table>
                <thead><tr><th>Начало</th><th>Тип</th><th>Итог</th><th>Снимок</th><th>База</th><th>Вложения</th><th>Проверка</th><th>Очистка</th><th>Ошибка</th></tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.run_key}>
                      <td style={{ whiteSpace: "nowrap" }}>{when(r.started_at)}</td>
                      <td>{REASON[r.reason] || r.reason}</td>
                      <td><span className={`badge ${RUN[r.status][1]}`}>{RUN[r.status][0]}</span></td>
                      <td><code>{r.snapshot_id || "—"}</code></td>
                      <td style={{ whiteSpace: "nowrap" }}>{size(r.database_bytes)}</td>
                      <td style={{ whiteSpace: "nowrap" }}>{size(r.files_bytes)}</td>
                      <td>{sub(r.check_status)}</td>
                      <td>{sub(r.retention_status)}</td>
                      <td>{r.error_text || <span className="muted">—</span>}</td>
                    </tr>))}
                </tbody>
              </table>
            </div>)}
        </div>)}
    </>
  );
}
