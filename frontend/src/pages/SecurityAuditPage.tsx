import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import SecurityModal from "../components/SecurityModal";

/** «Настройки → Журнал и предупреждения» (docs/operations/10 «Алерты», 04 §7): предупреждения безопасности и журнал событий —
 *  входы, права, скачивания и выгрузки. Без содержимого файлов: только кто, что, когда, формат, размер, число строк. */
type Alert = { id: string; rule_key: string; rule_title: string; severity: string; severity_title: string; status: string; subject_user_id: string | null; subject_name: string | null;
  first_seen_at: string; last_seen_at: string; occurrence_count: number; summary: string; details: Record<string, unknown>;
  acknowledged_by_name: string | null; acknowledged_at: string | null; ack_comment: string; resolved_by_name: string | null; resolved_at: string | null; resolution_comment: string };
type Ev = { id: string; at: string; type: string; type_title: string; action: string; result: string; actor_name: string | null; subject_name: string | null; subject_user_id: string | null;
  actor_user_id: string | null; ip_prefix: string; request_id: string; entity_type: string; entity_id: string; deal_id: string | null; format: string; row_count: number | null;
  byte_size: number | null; alert_id: string | null; meta: Record<string, unknown> };
type AlertList = { total: number; items: Alert[]; open_by_severity: Record<string, number>; rules: { key: string; title: string }[]; rules_version: string; can_act: boolean };
type AlertCard = Alert & { events: Ev[]; can_act: boolean };

const SEV: Record<string, string> = { low: "", medium: "warn", high: "bad", critical: "bad" };
const STATUS: Record<string, [string, string]> = { new: ["новое", "bad"], acknowledged: ["в работе", "warn"], resolved: ["закрыто", "ok"] };
const RESULT: Record<string, [string, string]> = { success: ["успех", "ok"], denied: ["отказ", "bad"], error: ["ошибка", "warn"] };
const ACTIONS: Record<string, string> = {
  login: "вход", logout: "выход", step_up: "подтверждение действия", password_ok_mfa_pending: "пароль верный, ждём код", download: "скачивание", view: "просмотр",
  export: "выгрузка", revoke: "завершена сессия", revoke_others: "завершены другие сессии", admin_revoke: "администратор завершил сессию", admin_revoke_all: "администратор завершил все сессии",
  setup: "настройка кода", setup_started: "начата настройка кода", enabled: "код включён", disabled: "код выключен", recovery_regenerated: "новые коды восстановления",
  totp_failed: "неверный код", recovery_failed: "неверный код восстановления", admin_reset: "сброс администратором", change: "смена пароля", admin_temporary: "временный пароль",
  offboard: "доступ отключён", reactivate: "доступ включён", rule_changed: "уровень роли изменён", override_set: "исключение прав задано", override_deleted: "исключение прав удалено",
  roles_changed: "роли изменены", acknowledged: "предупреждение взято в работу", resolved: "предупреждение закрыто",
  review_created: "создан пересмотр прав", review_keep: "пересмотр: оставить", review_change: "пересмотр: изменено", review_disable: "пересмотр: отключить",
  review_skipped: "пересмотр: пропуск", review_reopened: "пересмотр: решение отменено", review_completed: "пересмотр завершён", review_cancelled: "пересмотр отменён",
};
const DETAIL: Record<string, string> = { count_10min: "событий за 10 минут", threshold: "порог", rows: "строк в выгрузке", group: "группа прав", role: "роль", level: "уровень",
  before: "было", after: "стало", roles_added: "добавлены роли", roles_removed: "сняты роли", action: "действие" };
const dt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Не удалось выполнить действие");
const size = (b: number | null) => (b == null ? "" : b < 1024 ? `${b} Б` : b < 1048576 ? `${Math.round(b / 1024)} КБ` : `${(b / 1048576).toFixed(1)} МБ`);
const qs = (o: Record<string, string>) => Object.entries(o).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

function fmtDetail(k: string, v: unknown) {
  if (Array.isArray(v)) return v.join(", ") || "—";
  if (k === "action") return ACTIONS[String(v)] || String(v);
  if (v === null || v === undefined) return "—";
  return String(v);
}

function EventsTable({ items, compact = false }: { items: Ev[]; compact?: boolean }) {
  if (!items.length) return <p className="muted">Событий нет.</p>;
  return (
    <div className="backup-table"><table className="aud-table">
      <thead><tr><th>Когда</th><th>Событие</th><th>Результат</th><th>Кто</th>{!compact && <th>О ком</th>}<th>Объект</th>{!compact && <th>Адрес</th>}</tr></thead>
      <tbody>{items.map((e) => {
        const r = RESULT[e.result] || [e.result, ""];
        const obj = [e.format && e.format.toUpperCase(), e.row_count != null && `${e.row_count} строк`, size(e.byte_size), e.meta?.inline ? "как страница" : "",
          e.meta?.delivery ? "ссылка на 5 мин" : ""].filter(Boolean).join(" · ");
        return <tr key={e.id}>
          <td style={{ whiteSpace: "nowrap" }}>{dt(e.at)}</td>
          <td><b>{e.type_title}</b><div className="muted" style={{ fontSize: 12 }}>{ACTIONS[e.action] || e.action}</div></td>
          <td><span className={`badge ${r[1]}`}>{r[0]}</span></td>
          <td>{e.actor_name || <span className="muted">не вошёл</span>}</td>
          {!compact && <td>{e.subject_name && e.subject_user_id !== e.actor_user_id ? e.subject_name : ""}</td>}
          <td className="aud-obj">{obj}{e.meta?.route ? <div className="muted" style={{ fontSize: 12 }}>{String(e.meta.route)}</div> : null}</td>
          {!compact && <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{e.ip_prefix}</td>}
        </tr>;
      })}</tbody>
    </table></div>
  );
}

function AlertDialog({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [a, setA] = useState<AlertCard | null>(null);
  const [comment, setComment] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setA(await api.get<AlertCard>(`/api/security/alerts/${id}`)); } catch (x) { setErr(errText(x)); } }, [id]);
  useEffect(() => { load(); }, [load]);
  async function act(kind: "ack" | "resolve") {
    setBusy(true); setErr("");
    try { await api.post(`/api/security/alerts/${id}/${kind}`, { comment }); setComment(""); await load(); onChanged(); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  const st = a ? STATUS[a.status] : null;
  return (
    <SecurityModal title={a ? a.rule_title : "Предупреждение"} onClose={onClose} width={760}>
      {!a ? (err ? <div className="alert error">{err}</div> : <span className="muted">Загрузка…</span>) : <>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className={`badge ${SEV[a.severity]}`}>{a.severity_title}</span>{st && <span className={`badge ${st[1]}`}>{st[0]}</span>}
          {a.subject_name && <span>Сотрудник: <b>{a.subject_name}</b></span>}
        </div>
        <p style={{ marginBottom: 4 }}>{a.summary}</p>
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Впервые {dt(a.first_seen_at)} · последний раз {dt(a.last_seen_at)} · повторов {a.occurrence_count}</p>
        {Object.keys(a.details).length > 0 && <dl className="aud-dl">{Object.entries(a.details).filter(([k]) => k in DETAIL).map(([k, v]) =>
          <div key={k}><dt>{DETAIL[k]}</dt><dd>{fmtDetail(k, v)}</dd></div>)}</dl>}
        {a.ack_comment && <p><b>Взял в работу</b> {a.acknowledged_by_name}, {dt(a.acknowledged_at)}: {a.ack_comment}</p>}
        {a.resolution_comment && <p><b>Закрыл</b> {a.resolved_by_name}, {dt(a.resolved_at)}: {a.resolution_comment}</p>}
        <h4 style={{ marginBottom: 6 }}>Связанные события</h4>
        <EventsTable items={a.events} compact />
        {err && <div className="alert error" role="alert">{err}</div>}
        {a.can_act && a.status !== "resolved" && <div className="ar-form">
          <label className="sec-field">Комментарий — что проверено и что решили
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Например: выгрузка для квартального отчёта, согласовано с ФД" /></label>
          {a.severity === "critical" && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Критическое предупреждение закрывается с подтверждением кодом из приложения или паролем.</p>}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            {a.status === "new" && <button className="btn secondary" disabled={busy || comment.trim().length < 3} onClick={() => act("ack")}>Взять в работу</button>}
            <button className="btn" disabled={busy || comment.trim().length < 3} onClick={() => act("resolve")}>Закрыть предупреждение</button>
          </div>
        </div>}
        {!a.can_act && a.status !== "resolved" && <p className="muted" style={{ fontSize: 13 }}>Отметить предупреждение может администратор безопасности.</p>}
      </>}
    </SecurityModal>
  );
}

function AlertsTab({ onUser }: { onUser: (id: string, name: string) => void }) {
  const [f, setF] = useState({ status: "open", severity: "", rule: "", date_from: "", date_to: "" });
  const [data, setData] = useState<AlertList | null>(null);
  const [err, setErr] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const load = useCallback(async () => { try { setData(await api.get<AlertList>(`/api/security/alerts?${qs({ ...f, limit: "100" })}`)); setErr(""); } catch (x) { setErr(errText(x)); } }, [f]);
  useEffect(() => { load(); }, [load]);
  const ob = data?.open_by_severity || {};
  return (
    <>
      <div className="aud-summary">
        {(["critical", "high", "medium"] as const).map((s) => <button key={s} className={`aud-chip ${ob[s] ? SEV[s] : ""}`} onClick={() => setF({ ...f, status: "open", severity: f.severity === s ? "" : s })}
          aria-pressed={f.severity === s}><b>{ob[s] || 0}</b> {({ critical: "критических", high: "высоких", medium: "средних" })[s]}</button>)}
        <span className="muted" style={{ fontSize: 13 }}>открытых предупреждений</span>
      </div>
      <div className="aud-filters">
        <label className="sec-field">Статус<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="open">открытые</option><option value="new">новые</option><option value="acknowledged">в работе</option><option value="resolved">закрытые</option><option value="">все</option></select></label>
        <label className="sec-field">Важность<select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
          <option value="">любая</option><option value="critical">критическая</option><option value="high">высокая</option><option value="medium">средняя</option></select></label>
        <label className="sec-field">Правило<select value={f.rule} onChange={(e) => setF({ ...f, rule: e.target.value })}>
          <option value="">любое</option>{data?.rules.map((r) => <option key={r.key} value={r.key}>{r.title}</option>)}</select></label>
        <label className="sec-field">С<input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></label>
        <label className="sec-field">По<input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></label>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && (data.items.length === 0 ? <p className="muted">{f.status === "open" ? "Открытых предупреждений нет." : "Ничего не найдено."}</p> :
        <div className="backup-table"><table className="aud-table">
          <thead><tr><th>Важность</th><th>Что случилось</th><th>Сотрудник</th><th>Повторов</th><th>Последний раз</th><th>Статус</th><th></th></tr></thead>
          <tbody>{data.items.map((a) => <tr key={a.id}>
            <td><span className={`badge ${SEV[a.severity]}`}>{a.severity_title}</span></td>
            <td><b>{a.rule_title}</b><div className="muted" style={{ fontSize: 12 }}>{a.summary}</div></td>
            <td>{a.subject_name ? <button className="btn ghost aud-link" onClick={() => onUser(a.subject_user_id!, a.subject_name!)} title="События сотрудника">{a.subject_name}</button> : <span className="muted">все сотрудники роли</span>}</td>
            <td>{a.occurrence_count}</td>
            <td style={{ whiteSpace: "nowrap" }}>{dt(a.last_seen_at)}</td>
            <td><span className={`badge ${STATUS[a.status][1]}`}>{STATUS[a.status][0]}</span></td>
            <td><button className="btn secondary" onClick={() => setOpenId(a.id)}>Проверить</button></td>
          </tr>)}</tbody>
        </table></div>)}
      {data && <p className="muted" style={{ fontSize: 12 }}>Правила {data.rules_version}: больше 5 выгрузок или 20 закрытых файлов за 10 минут, выгрузка от 1000 строк, 10 отказов за 10 минут,
        любое изменение прав, сброс кода, временный пароль, отключение доступа. Предупреждения ничего не блокируют.</p>}
      {openId && <AlertDialog id={openId} onClose={() => setOpenId(null)} onChanged={load} />}
    </>
  );
}

function EventsTab({ user, clearUser }: { user: { id: string; name: string } | null; clearUser: () => void }) {
  const [f, setF] = useState({ event_type: "", result: "", date_from: "", date_to: "" });
  const [data, setData] = useState<{ total: number; items: Ev[]; types: Record<string, string> } | null>(null);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState("");
  const PAGE = 50;
  const load = useCallback(async () => {
    try { setData(await api.get(`/api/security/events?${qs({ ...f, user_id: user?.id || "", limit: String(PAGE), offset: String(page * PAGE) })}`)); setErr(""); } catch (x) { setErr(errText(x)); }
  }, [f, user, page]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(0); }, [f, user]);
  return (
    <>
      <div className="aud-filters">
        <label className="sec-field">Тип<select value={f.event_type} onChange={(e) => setF({ ...f, event_type: e.target.value })}>
          <option value="">все</option>{data && Object.entries(data.types).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="sec-field">Результат<select value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}>
          <option value="">любой</option><option value="success">успех</option><option value="denied">отказ</option><option value="error">ошибка</option></select></label>
        <label className="sec-field">С<input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></label>
        <label className="sec-field">По<input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></label>
        {user && <span className="badge info aud-userchip">{user.name} <button className="btn ghost" onClick={clearUser} aria-label="Показать всех">×</button></span>}
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && <><EventsTable items={data.items} />
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>Всего {data.total}</span>
          <div className="row"><button className="btn ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>← Новее</button>
            <button className="btn ghost" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>Старше →</button></div>
        </div></>}
    </>
  );
}

export default function SecurityAuditPage() {
  const [tab, setTab] = useState<"alerts" | "events">("alerts");
  const [user, setUser] = useState<{ id: string; name: string } | null>(null);
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Журнал и предупреждения</h2>
      <p className="muted" style={{ marginTop: -6 }}>Кто входил, менял права, скачивал файлы и делал выгрузки. Содержимое файлов и выгрузок здесь не хранится.</p>
      <div className="row aud-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "alerts"} className={`btn ${tab === "alerts" ? "" : "secondary"}`} onClick={() => setTab("alerts")}>Предупреждения</button>
        <button role="tab" aria-selected={tab === "events"} className={`btn ${tab === "events" ? "" : "secondary"}`} onClick={() => setTab("events")}>Журнал событий</button>
      </div>
      {tab === "alerts" ? <AlertsTab onUser={(id, name) => { setUser({ id, name }); setTab("events"); }} /> : <EventsTab user={user} clearUser={() => setUser(null)} />}
    </div>
  );
}
