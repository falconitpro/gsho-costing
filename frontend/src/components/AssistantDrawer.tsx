import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";

/** «Помощник CRM» (docs/operations/06 §10): только чтение. Ответы — из данных CRM, доступных пользователю, и из справки.
 *  Источники под ответом формирует сервер; переход по ним снова проверяет права. Действий «оплатить / изменить» нет. */
type Src = { type: string; id: string; label: string; url: string };
type Msg = { id: string; role: "user" | "assistant" | "system_notice"; mode: string; text: string; status: string; provider: string; sources: Src[]; warnings: string[]; at: string | null; purged?: boolean };
type Status = { modes: Record<string, boolean>; mode_titles: Record<string, string>; provider: string; notice: string; general_off_text: string; max_text: number; retention_days: number };
type Conv = { id: string; title: string; last_message_at: string | null };

const QUICK = ["Что требует моего решения?", "Мои задачи на неделю", "Как создать обращение в поддержку?"];
const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const NOTICE_KEY = "gsho.assistantNoticeSeen";

export default function AssistantDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState("crm");
  const [conv, setConv] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Conv[] | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [notice, setNotice] = useState(() => { try { return !localStorage.getItem(NOTICE_KEY); } catch { return true; } });
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (open && !st) api.get<Status>("/api/assistant/status").then(setSt).catch((e) => setErr(e instanceof ApiError ? e.message : "Помощник недоступен")); }, [open, st]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 50); }, [open]);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [msgs, busy]);
  useEffect(() => {
    if (!open) return;
    const k = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") onClose(); };   // черновик остаётся в поле
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  const send = useCallback(async (q: string) => {
    const t = q.trim();
    if (!t || busy) return;
    setBusy(true); setErr("");
    const crid = newId();
    const tmp: Msg = { id: crid, role: "user", mode, text: t, status: "complete", provider: "", sources: [], warnings: [], at: new Date().toISOString() };
    setMsgs((m) => [...m, tmp]); setText("");
    try {
      const r = await api.post<{ conversation_id: string; question: Msg; answer: Msg | null }>("/api/assistant/messages", { text: t, mode, conversation_id: conv, client_request_id: crid });
      setConv(r.conversation_id);
      setMsgs((m) => [...m.filter((x) => x.id !== crid), r.question, ...(r.answer ? [r.answer] : [])]);
    } catch (e) {
      setMsgs((m) => m.filter((x) => x.id !== crid)); setText(t);   // вопрос возвращается в поле — можно повторить
      setErr(e instanceof ApiError ? e.message : "Не удалось отправить — проверьте связь и повторите");
    } finally { setBusy(false); }
  }, [busy, conv, mode]);
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } };
  const openHistory = () => api.get<{ items: Conv[] }>("/api/assistant/conversations").then((r) => setHistory(r.items)).catch(() => setHistory([]));
  const load = (id: string) => api.get<{ id: string; messages: Msg[]; default_mode: string }>(`/api/assistant/conversations/${id}`).then((r) => {
    setConv(r.id); setMsgs(r.messages); setMode(r.default_mode || "crm"); setHistory(null); }).catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось открыть"));
  const del = async () => { if (!conv) return; await api.del(`/api/assistant/conversations/${conv}`).catch(() => undefined); setConv(null); setMsgs([]); setConfirmDel(false); };
  const go = (u: string) => { if (u.startsWith("/")) { nav(u); if (window.innerWidth < 768) onClose(); } };
  const generalOff = mode === "general" && st && !st.modes.general;
  return (
    <aside className={`ai-drawer ${open ? "open" : ""}`} aria-hidden={!open} aria-label="Помощник CRM" role="dialog">
      <div className="ai-head">
        <b>Помощник CRM</b>
        <span className="muted" style={{ fontSize: 12 }}>{st ? (st.provider === "none" ? "без внешней модели" : `модель: ${st.provider}`) : ""}</span>
        <button className="btn ghost" onClick={onClose} aria-label="Закрыть помощника">✕</button>
      </div>
      <div className="ai-tools row">
        <button className="btn ghost" onClick={() => { setConv(null); setMsgs([]); setHistory(null); }}>Новый диалог</button>
        <button className="btn ghost" onClick={openHistory}>История</button>
        {conv && <button className="btn ghost" onClick={() => setConfirmDel(true)}>Удалить диалог</button>}
      </div>
      {confirmDel && <div className="alert warn ai-confirm">Удалить этот диалог? Он исчезнет из истории, содержимое сотрётся.
        <div className="row" style={{ marginTop: 6 }}><button className="btn" onClick={del}>Удалить</button><button className="btn secondary" onClick={() => setConfirmDel(false)}>Отмена</button></div></div>}
      {notice && st && <div className="alert info ai-notice">{st.notice}<div><button className="btn ghost" onClick={() => { setNotice(false); try { localStorage.setItem(NOTICE_KEY, "1"); } catch { /* приватный режим */ } }}>Понятно</button></div></div>}
      {history ? <div className="ai-list">
        <h4 style={{ margin: "4px 0 8px" }}>История (хранится {st?.retention_days ?? 30} дн.)</h4>
        {history.length === 0 ? <p className="muted">Диалогов пока нет.</p> : history.map((h) => <button key={h.id} className="ai-hist" onClick={() => load(h.id)}>
          <span>{h.title}</span><small className="muted">{h.last_message_at ? new Date(h.last_message_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}</small></button>)}
        <button className="btn ghost" onClick={() => setHistory(null)}>← Назад</button>
      </div> : <div className="ai-list" ref={listRef} aria-live="polite">
        {msgs.length === 0 && <div className="ai-empty"><p className="muted">Спросите о своих задачах, сделках, сроках — или как что-то сделать в CRM.</p>
          {QUICK.map((q) => <button key={q} className="btn secondary ai-quick" onClick={() => { if (q.startsWith("Как")) setMode("help"); send(q); }} disabled={busy}>{q}</button>)}</div>}
        {msgs.map((m) => <div key={m.id} className={`ai-msg ${m.role}`}>
          <div className="ai-text">{m.purged ? <i className="muted">содержимое удалено по сроку хранения</i> : m.text}</div>
          {m.warnings?.length > 0 && <ul className="ai-warn">{m.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          {m.sources?.length > 0 && <div className="ai-src">{m.sources.map((s) => <button key={s.type + s.id} className="ai-chip" onClick={() => go(s.url)} title={s.url}>{s.label}</button>)}</div>}
        </div>)}
        {busy && <div className="ai-msg assistant muted">Ищу в данных CRM…</div>}
      </div>}
      {err && <div className="alert error ai-err" role="alert">{err}</div>}
      <div className="ai-input">
        <div className="row" style={{ gap: 6, alignItems: "center" }}>
          <label className="muted" style={{ fontSize: 12 }}>Режим <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Режим помощника">
            {st && Object.entries(st.mode_titles).map(([k, t]) => <option key={k} value={k}>{t}{!st.modes[k] ? " (выключен)" : ""}</option>)}</select></label>
          {mode === "general" && <span className="muted" style={{ fontSize: 12 }}>Контекст CRM не используется</span>}
        </div>
        {generalOff && <div className="muted" style={{ fontSize: 12 }}>{st!.general_off_text}</div>}
        <textarea ref={inputRef} rows={2} value={text} maxLength={st?.max_text ?? 4000} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          placeholder="Вопрос… (Enter — отправить, Shift+Enter — новая строка)" aria-label="Вопрос помощнику" />
        <div className="row" style={{ justifyContent: "space-between" }}>
          <a href="/support" onClick={(e) => { e.preventDefault(); go("/support"); }} style={{ fontSize: 12 }}>Открыть поддержку</a>
          <button className="btn" onClick={() => send(text)} disabled={busy || !text.trim()}>{busy ? "…" : "Спросить"}</button>
        </div>
      </div>
    </aside>
  );
}
