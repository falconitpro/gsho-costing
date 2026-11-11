import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, getLastError } from "../api";
import SecurityModal from "../components/SecurityModal";

/** Поддержка внутри CRM (docs/operations/05, 10 §5): «Мои обращения», карточка с перепиской, очередь специалиста.
 *  Только «в системе» — никакой почты. Тексты выводятся как текст (React экранирует), HTML не вставляется. */
type Att = { id: string; file_name: string; size: number; scan_status: string; url: string | null };
type Msg = { id: string; kind: "user" | "specialist" | "system"; author_name: string | null; body: string; at: string; attachments: Att[] };
type Sla = { hours: number; due_at: string | null; answered: boolean; overdue: boolean; note: string };
type Ticket = { id: string; number: string; subject: string; category: string; category_title: string; requested_urgency: string; urgency_title: string;
  priority: string; priority_title: string; status: string; status_title: string; reporter_name: string; assignee_id: string | null; assignee_name: string | null;
  created_at: string; last_activity_at: string; first_response_at: string | null; sla: Sla; unread: number; mine: boolean };
type Full = Ticket & { description: string; current_route: string; app_commit: string; correlation_id: string; client_metadata: Record<string, string>;
  messages: Msg[]; actions: string[]; assignees?: { id: string; name: string }[] };
type Meta = { categories: Record<string, string>; urgency: Record<string, string>; priority: Record<string, string>; status: Record<string, string>;
  sla_hours: Record<string, number>; sla_note: string; max_files: number; max_file_mb: number; unread: number; is_admin: boolean; can_edit: boolean };

const ST: Record<string, string> = { new: "info", in_progress: "warn", waiting_reporter: "bad", resolved: "ok", closed: "" };
const PR: Record<string, string> = { critical: "bad", high: "warn", normal: "", low: "" };
const dt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Не удалось выполнить действие — проверьте связь и повторите");
const size = (b: number) => (b < 1024 ? `${b} Б` : b < 1048576 ? `${Math.round(b / 1024)} КБ` : `${(b / 1048576).toFixed(1)} МБ`);
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

function useMeta() {
  const [m, setM] = useState<Meta | null>(null);
  useEffect(() => { api.get<Meta>("/api/support/meta").then(setM).catch(() => undefined); }, []);
  return m;
}

function clientInfo() {
  const ua = navigator.userAgent;
  const browser = (/Edg\/(\d+)/.exec(ua) && `Edge ${/Edg\/(\d+)/.exec(ua)![1]}`) || (/YaBrowser\/(\d+)/.exec(ua) && `Яндекс Браузер ${/YaBrowser\/(\d+)/.exec(ua)![1]}`)
    || (/Firefox\/(\d+)/.exec(ua) && `Firefox ${/Firefox\/(\d+)/.exec(ua)![1]}`) || (/Chrome\/(\d+)/.exec(ua) && `Chrome ${/Chrome\/(\d+)/.exec(ua)![1]}`)
    || (/Version\/(\d+).*Safari/.exec(ua) && `Safari ${/Version\/(\d+).*Safari/.exec(ua)![1]}`) || "браузер";
  const os = /Windows/.test(ua) ? "Windows" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /Linux/.test(ua) ? "Linux" : "";
  return { browser, os, viewport: `${window.innerWidth}x${window.innerHeight}`, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, local_time: new Date().toLocaleString("ru-RU") };
}

function Files({ files, setFiles, max, maxMb }: { files: File[]; setFiles: (f: File[]) => void; max: number; maxMb: number }) {
  const ref = useRef<HTMLInputElement>(null);
  const tooBig = files.filter((f) => f.size > maxMb * 1048576);
  return (
    <div className="sup-files">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn secondary" onClick={() => ref.current?.click()} disabled={files.length >= max}>Приложить файл</button>
        <span className="muted" style={{ fontSize: 12 }}>до {max} файлов, каждый до {maxMb} МБ. Не прикладывайте пароли, ключи, токены и документы, не нужные для диагностики.</span>
      </div>
      <input ref={ref} type="file" multiple hidden onChange={(e) => { setFiles([...files, ...Array.from(e.target.files || [])].slice(0, max)); e.target.value = ""; }} />
      {files.length > 0 && <ul className="sup-file-list">{files.map((f, i) => <li key={i}><span>{f.name} <span className="muted">({size(f.size)})</span></span>
        <button type="button" className="btn ghost" aria-label={`Убрать ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button></li>)}</ul>}
      {tooBig.length > 0 && <div className="alert error">Слишком большой файл: {tooBig.map((f) => f.name).join(", ")}</div>}
    </div>
  );
}

function NewTicket({ meta, onClose, onCreated }: { meta: Meta; onClose: () => void; onCreated: (id: string, number: string) => void }) {
  const loc = useLocation();
  const from = new URLSearchParams(loc.search).get("from") || "";
  const last = getLastError();
  const [f, setF] = useState({ category: "error", subject: "", description: "", urgency: "normal" });
  const [files, setFiles] = useState<File[]>([]);
  const [sendTech, setSendTech] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRef(newKey());
  const info = clientInfo();
  async function submit(e: FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    const fd = new FormData();
    Object.entries(f).forEach(([k, v]) => fd.append(k, v));
    if (sendTech) {
      fd.append("route", from); fd.append("correlation_id", last?.requestId || ""); fd.append("client_metadata", JSON.stringify(info));
    }
    files.forEach((x) => fd.append("files", x));
    try { const t = await api.form<Full>("/api/support/tickets", fd, { "Idempotency-Key": key.current }); onCreated(t.id, t.number); }
    catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  const sOk = f.subject.trim().length >= 5, dOk = f.description.trim().length >= 10;
  return (
    <SecurityModal title="Новое обращение" onClose={onClose} width={640}>
      <form onSubmit={submit}>
        <div className="sup-grid2">
          <label className="sec-field">Категория<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            {Object.entries(meta.categories).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
          <label className="sec-field">Срочность<select value={f.urgency} onChange={(e) => setF({ ...f, urgency: e.target.value })}>
            {Object.entries(meta.urgency).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        </div>
        {f.urgency === "critical" && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Критичная — система недоступна или работа остановлена. Для вопросов и неудобств выберите обычную.</p>}
        <label className="sec-field">Тема<input value={f.subject} maxLength={200} onChange={(e) => setF({ ...f, subject: e.target.value })} placeholder="Коротко: что не так" /></label>
        <label className="sec-field">Описание<textarea rows={6} maxLength={8000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })}
          placeholder={f.category === "error" ? "Что делали, что ожидали, что произошло, примерно во сколько" : "Опишите вопрос или предложение"} /></label>
        <Files files={files} setFiles={setFiles} max={meta.max_files} maxMb={meta.max_file_mb} />
        <details className="sup-tech"><summary>Что будет передано вместе с обращением</summary>
          <label className="row" style={{ margin: "6px 0" }}><input type="checkbox" checked={sendTech} onChange={(e) => setSendTech(e.target.checked)} /> передать технические данные</label>
          {sendTech ? <ul className="muted" style={{ fontSize: 13, margin: 0 }}>
            <li>страница: {from || "—"}</li><li>ID последней ошибки: {last?.requestId || "—"}</li>
            <li>{info.browser}, {info.os}, экран {info.viewport}, {info.timezone}, {info.local_time}</li></ul>
            : <p className="muted" style={{ fontSize: 13 }}>Технические данные не передаются.</p>}
        </details>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
          <button type="button" className="btn secondary" onClick={onClose}>Отмена</button>
          <button className="btn" disabled={busy || !sOk || !dOk}>{busy ? "Отправляем…" : "Отправить"}</button>
        </div>
      </form>
    </SecurityModal>
  );
}

function TicketRow({ t, onOpen, admin }: { t: Ticket; onOpen: () => void; admin?: boolean }) {
  return (
    <tr className={t.unread ? "sup-unread" : ""} onClick={onOpen} style={{ cursor: "pointer" }}>
      <td style={{ whiteSpace: "nowrap" }}><b>{t.number}</b>{t.unread > 0 && <span className="badge info" style={{ marginLeft: 6 }}>новое</span>}</td>
      <td><Link to="#" onClick={(e) => { e.preventDefault(); onOpen(); }} className="sup-subj">{t.subject}</Link><div className="muted" style={{ fontSize: 12 }}>{t.category_title}</div></td>
      {admin && <td>{t.reporter_name}</td>}
      <td><span className={`badge ${ST[t.status]}`}>{t.status_title}</span>{t.sla.overdue && <span className="badge bad" style={{ marginLeft: 4 }}>просрочено</span>}</td>
      {admin && <td><span className={`badge ${PR[t.priority]}`}>{t.priority_title}</span></td>}
      {admin && <td>{t.assignee_name || <span className="muted">не назначено</span>}</td>}
      <td style={{ whiteSpace: "nowrap" }}>{dt(t.last_activity_at)}</td>
    </tr>
  );
}

function TicketCards({ items, onOpen, admin }: { items: Ticket[]; onOpen: (t: Ticket) => void; admin?: boolean }) {
  return (
    <>
      <div className="backup-table sup-desktop"><table className="aud-table">
        <thead><tr><th>Номер</th><th>Тема</th>{admin && <th>Автор</th>}<th>Статус</th>{admin && <th>Приоритет</th>}{admin && <th>Назначено</th>}<th>Обновлено</th></tr></thead>
        <tbody>{items.map((t) => <TicketRow key={t.id} t={t} admin={admin} onOpen={() => onOpen(t)} />)}</tbody>
      </table></div>
      <div className="sup-mobile">{items.map((t) => <button key={t.id} className={`sup-card ${t.unread ? "sup-unread" : ""}`} onClick={() => onOpen(t)}>
        <div className="row" style={{ justifyContent: "space-between", gap: 6 }}><b>{t.number}</b><span className={`badge ${ST[t.status]}`}>{t.status_title}</span></div>
        <div className="sup-subj">{t.subject}</div>
        <div className="muted" style={{ fontSize: 12 }}>{admin ? `${t.reporter_name} · ${t.priority_title} · ` : ""}{dt(t.last_activity_at)}{t.sla.overdue ? " · просрочено" : ""}{t.unread ? " · новое" : ""}</div>
      </button>)}</div>
    </>
  );
}

export function SupportList() {
  const meta = useMeta();
  const nav = useNavigate();
  const loc = useLocation();
  const [tab, setTab] = useState("active");
  const [data, setData] = useState<{ items: Ticket[]; tabs: Record<string, number> } | null>(null);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(new URLSearchParams(loc.search).has("new"));
  const [done, setDone] = useState("");
  const load = useCallback(() => api.get<{ items: Ticket[]; tabs: Record<string, number> }>(`/api/support/tickets?tab=${tab === "all" ? "" : tab}`)
    .then((r) => { setData(r); setErr(""); }).catch((x) => setErr(errText(x))), [tab]);
  useEffect(() => { load(); }, [load]);
  const TABS: [string, string][] = [["active", "Активные"], ["waiting", "Ждут меня"], ["resolved", "Решённые"], ["all", "Все"]];
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h1 style={{ margin: 0 }}>Поддержка</h1>
        <div className="row" style={{ gap: 8 }}>
          {meta?.is_admin && <button className="btn secondary" onClick={() => nav("/support/admin")}>Очередь поддержки</button>}
          <button className="btn" onClick={() => setCreating(true)} disabled={!meta}>Новое обращение</button>
        </div>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>Ответ появится здесь и в уведомлениях CRM (колокольчик). Писем не будет.</p>
      {done && <div className="alert ok">Обращение {done} создано. Ответ появится здесь и в уведомлениях CRM.</div>}
      <div className="row aud-tabs" role="tablist">{TABS.map(([k, t]) => <button key={k} role="tab" aria-selected={tab === k} className={`btn ${tab === k ? "" : "secondary"}`}
        onClick={() => setTab(k)}>{t}{data?.tabs[k] ? ` · ${data.tabs[k]}` : ""}</button>)}</div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && (data.items.length === 0 ? <div className="sup-empty"><p>{tab === "active" ? "Активных обращений нет." : "Ничего не найдено."}</p>
        <button className="btn" onClick={() => setCreating(true)}>Сообщить о проблеме</button></div>
        : <TicketCards items={data.items} onOpen={(t) => nav(`/support/${t.id}`)} />)}
      {creating && meta && <NewTicket meta={meta} onClose={() => setCreating(false)} onCreated={(id, number) => { setCreating(false); setDone(number); nav(`/support/${id}?created=${number}`); }} />}
    </div>
  );
}

function Composer({ t, meta, onSent }: { t: Full; meta: Meta; onSent: (x: Full) => void }) {
  const [body, setBody] = useState(() => sessionStorage.getItem(`sup.draft.${t.id}`) || "");
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { try { sessionStorage.setItem(`sup.draft.${t.id}`, body); } catch { /* приватный режим */ } }, [body, t.id]);
  async function send(e: FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    const fd = new FormData(); fd.append("body", body); files.forEach((f) => fd.append("files", f));
    try { const x = await api.form<Full>(`/api/support/tickets/${t.id}/messages`, fd); setBody(""); setFiles([]); sessionStorage.removeItem(`sup.draft.${t.id}`); onSent(x); }
    catch (x) { setErr(errText(x)); }   // черновик остаётся в поле
    finally { setBusy(false); }
  }
  return (
    <form className="sup-composer" onSubmit={send}>
      <label className="sec-field">{t.mine ? "Ваш ответ" : "Ответ автору"}<textarea rows={3} maxLength={8000} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <Files files={files} setFiles={setFiles} max={meta.max_files} maxMb={meta.max_file_mb} />
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" disabled={busy || (!body.trim() && !files.length)}>{busy ? "Отправляем…" : "Отправить"}</button></div>
    </form>
  );
}

const ACTION_TITLES: Record<string, string> = { start: "Взять в работу", wait: "Запросить данные у автора", resolve: "Отметить решённым", reopen: "Проблема осталась", close: "Закрыть обращение" };

export function TicketView({ id, admin = false, onBack }: { id: string; admin?: boolean; onBack: () => void }) {
  const meta = useMeta();
  const loc = useLocation();
  const created = new URLSearchParams(loc.search).get("created");
  const [t, setT] = useState<Full | null>(null);
  const [err, setErr] = useState("");
  const [act, setAct] = useState<string | null>(null);
  const [actBody, setActBody] = useState("");
  const [saved, setSaved] = useState("");
  const load = useCallback(() => api.get<Full>(`/api/support/tickets/${id}`).then((x) => {
    setT(x); setErr(""); window.dispatchEvent(new CustomEvent("gsho:support-read"));   // открыто — отметка на кнопке «Поддержка» обновится сразу
  }).catch((x) => setErr(errText(x))), [id]);
  useEffect(() => { load(); }, [load]);
  if (!t || !meta) return <div className="card">{err ? <><div className="alert error">{err}</div><button className="btn ghost" onClick={onBack}>← Назад</button></> : <span className="muted">Загрузка…</span>}</div>;
  async function doAct(action: string, body = "") {
    setErr("");
    try { const x = await api.post<Full>(`/api/support/tickets/${id}/transition`, { action, body }); setT(x); setAct(null); setActBody(""); } catch (x) { setErr(errText(x)); }
  }
  async function save(kind: "assign" | "priority", value: string) {
    setErr(""); setSaved("");
    try {
      const x = await api.post<Full>(`/api/support/tickets/${id}/${kind}`, kind === "assign" ? { assignee_id: value || null } : { priority: value });
      setT({ ...x, assignees: t!.assignees }); setSaved(kind === "assign" ? "Назначение сохранено" : "Приоритет сохранён");
    } catch (x) { setErr(errText(x)); }
  }
  const statusActs = t.actions.filter((a) => a in ACTION_TITLES && (!t.mine || admin || ["reopen", "close"].includes(a)));
  return (
    <div className="card">
      <button className="btn ghost" onClick={onBack}>← {admin ? "Очередь" : "Мои обращения"}</button>
      {created && <div className="alert ok">Обращение {created} создано. Ответ появится здесь и в уведомлениях CRM.</div>}
      <div className="sup-head">
        <div><h2 style={{ margin: "4px 0" }}><span className="muted">{t.number}</span> {t.subject}</h2>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}><span className={`badge ${ST[t.status]}`}>{t.status_title}</span>
            <span className={`badge ${PR[t.priority]}`}>приоритет: {t.priority_title}</span>{t.sla.overdue && <span className="badge bad">первый ответ просрочен</span>}</div></div>
        <div className="row sup-actions">{statusActs.map((a) => <button key={a} className={`btn ${a === "close" ? "ghost" : "secondary"}`}
          onClick={() => (a === "wait" || a === "resolve" || a === "close" ? setAct(a) : doAct(a))}>{ACTION_TITLES[a]}</button>)}</div>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="sup-layout">
        <div className="sup-thread">
          {t.messages.map((m) => m.kind === "system"
            ? <div key={m.id} className="sup-sys">{m.body} · {dt(m.at)}</div>
            : <div key={m.id} className={`sup-msg ${m.kind}`}>
              <div className="sup-msg-head"><b>{m.author_name}</b> <span className="muted">{m.kind === "specialist" ? "поддержка" : "автор"} · {dt(m.at)}</span></div>
              <div className="sup-body">{m.body}</div>
              {m.attachments.length > 0 && <ul className="sup-file-list">{m.attachments.map((a) => <li key={a.id}>
                {a.url ? <a href={a.url} target="_blank" rel="noopener noreferrer">{a.file_name}</a> : <span>{a.file_name}</span>}
                <span className="muted"> {size(a.size)} · {a.scan_status === "clean" ? "проверен антивирусом" : a.scan_status === "legacy_unscanned" ? "без проверки антивирусом" : "заблокирован"}</span></li>)}</ul>}
            </div>)}
          {t.actions.includes("message") ? <Composer t={t} meta={meta} onSent={setT} /> : <p className="muted">Обращение закрыто. Если проблема повторилась — создайте новое и укажите номер {t.number}.</p>}
        </div>
        <aside className="sup-side">
          <dl className="aud-dl" style={{ gridTemplateColumns: "1fr" }}>
            <div><dt>Автор</dt><dd>{t.reporter_name}</dd></div>
            <div><dt>Категория</dt><dd>{t.category_title}</dd></div>
            <div><dt>Срочность от автора</dt><dd>{t.urgency_title}</dd></div>
            <div><dt>Создано</dt><dd>{dt(t.created_at)}</dd></div>
            <div><dt>Первый ответ</dt><dd>{t.first_response_at ? dt(t.first_response_at) : `ожидается до ${dt(t.sla.due_at)}`}<div className="muted" style={{ fontSize: 11 }}>{t.sla.hours} ч, {t.sla.note}</div></dd></div>
            {!admin && <div><dt>Назначено</dt><dd>{t.assignee_name || "в общей очереди"}</dd></div>}
          </dl>
          {admin && t.actions.includes("assign") && <div className="sup-admin-box">
            <label className="sec-field">Назначено<select value={t.assignee_id || ""} onChange={(e) => save("assign", e.target.value)}>
              <option value="">— в общей очереди —</option>{(t.assignees || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
            <label className="sec-field">Приоритет<select value={t.priority} onChange={(e) => save("priority", e.target.value)}>
              {Object.entries(meta.priority).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            {saved && <div className="alert ok" role="status">{saved}</div>}
          </div>}
          {(admin || t.mine) && (t.current_route || t.correlation_id || Object.keys(t.client_metadata).length > 0) && <details className="sup-tech" open={admin}>
            <summary>Технические данные</summary>
            <ul className="muted" style={{ fontSize: 12, margin: 0, paddingLeft: 16 }}>
              {t.current_route && <li>страница: {t.current_route}</li>}{t.correlation_id && <li>ID ошибки: {t.correlation_id}</li>}
              {Object.entries(t.client_metadata).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul></details>}
        </aside>
      </div>
      {act && <SecurityModal title={ACTION_TITLES[act]} onClose={() => setAct(null)} width={520}>
        {act === "close" ? <p>Обращение будет закрыто. Писать в него будет нельзя; при повторе проблемы — новое обращение. Это не удаление: история сохранится.</p>
          : <label className="sec-field">{act === "wait" ? "Что нужно от автора" : "Решение, обход или версия с исправлением"}
            <textarea rows={3} value={actBody} onChange={(e) => setActBody(e.target.value)} /></label>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setAct(null)}>Отмена</button>
          <button className="btn" disabled={act !== "close" && actBody.trim().length < 5} onClick={() => doAct(act, actBody)}>{ACTION_TITLES[act]}</button></div>
      </SecurityModal>}
    </div>
  );
}

export function SupportTicketRoute() {
  const { id } = useParams();
  const nav = useNavigate();
  return <TicketView id={id!} onBack={() => nav("/support")} />;
}

export function SupportAdmin() {
  const meta = useMeta();
  const nav = useNavigate();
  const loc = useLocation();
  const open = new URLSearchParams(loc.search).get("ticket");
  const [f, setF] = useState({ status: "open", category: "", priority: "", assignee: "", q: "", overdue: false });
  const [data, setData] = useState<{ items: Ticket[]; total: number } | null>(null);
  const [m, setM] = useState<Record<string, number | string> | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    const qs = Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
    api.get<{ items: Ticket[]; total: number }>(`/api/support/admin/tickets?${qs}`).then((r) => { setData(r); setErr(""); }).catch((x) => setErr(errText(x)));
    api.get<Record<string, number | string>>("/api/support/admin/metrics").then(setM).catch(() => undefined);
  }, [f]);
  useEffect(() => { if (!open) load(); }, [load, open]);
  if (meta && !meta.is_admin) return <div className="card"><div className="alert error">Очередь поддержки доступна специалистам поддержки.</div></div>;
  if (open) return <TicketView id={open} admin onBack={() => nav("/support/admin")} />;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h1 style={{ margin: 0 }}>Очередь поддержки</h1>
        <button className="btn secondary" onClick={() => nav("/support")}>Мои обращения</button>
      </div>
      {m && <div className="sup-kpis">
        {([["new", "новые"], ["no_first_response", "без первого ответа"], ["waiting_reporter", "ждут автора"], ["overdue", "просрочены"], ["resolved_period", `решено за ${m.days} дн.`]] as [string, string][])
          .map(([k, l]) => <button key={k} className={`kpi sup-kpi ${k === "overdue" && Number(m[k]) > 0 ? "bad" : ""}`}
            onClick={() => k === "overdue" ? setF({ ...f, overdue: !f.overdue }) : k === "new" ? setF({ ...f, status: f.status === "new" ? "open" : "new" }) : k === "waiting_reporter" ? setF({ ...f, status: "waiting_reporter" }) : undefined}>
            <span className="v">{m[k]}</span><span className="l">{l}</span></button>)}
      </div>}
      {m && <p className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>Сроки первого ответа: критичная 4 ч, высокая 24 ч, обычная 48 ч — {String(m.sla_note)}.</p>}
      <div className="aud-filters">
        <label className="sec-field">Статус<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="open">открытые</option>{meta && Object.entries(meta.status).map(([k, v]) => <option key={k} value={k}>{v}</option>)}<option value="">все</option></select></label>
        <label className="sec-field">Категория<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
          <option value="">любая</option>{meta && Object.entries(meta.categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label className="sec-field">Приоритет<select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>
          <option value="">любой</option>{meta && Object.entries(meta.priority).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label className="sec-field">Назначено<select value={f.assignee} onChange={(e) => setF({ ...f, assignee: e.target.value })}>
          <option value="">все</option><option value="me">мне</option><option value="none">не назначено</option></select></label>
        <label className="sec-field">Поиск<input value={f.q} placeholder="номер или тема" onChange={(e) => setF({ ...f, q: e.target.value })} /></label>
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={f.overdue} onChange={(e) => setF({ ...f, overdue: e.target.checked })} /> просроченные</label>
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {data && (data.items.length === 0 ? <p className="muted">Обращений нет.</p> : <TicketCards items={data.items} admin onOpen={(t) => nav(`/support/admin?ticket=${t.id}`)} />)}
    </div>
  );
}

export function SupportButton() {
  const [n, setN] = useState(0);
  const nav = useNavigate();
  useEffect(() => {
    const poll = () => api.bg<{ unread: number }>("/api/support/meta").then((r) => setN(r.unread)).catch(() => undefined);
    poll(); const t = setInterval(poll, 60000);
    window.addEventListener("gsho:support-read", poll);
    return () => { clearInterval(t); window.removeEventListener("gsho:support-read", poll); };
  }, []);
  return (
    <button className="btn ghost sup-btn" onClick={() => nav(`/support?from=${encodeURIComponent(window.location.pathname + window.location.search)}`)}
      title="Поддержка" aria-label={`Поддержка${n ? `, новых ответов: ${n}` : ""}`}>
      Поддержка{n > 0 && <span className="sup-badge">{n > 99 ? "99+" : n}</span>}
    </button>
  );
}
