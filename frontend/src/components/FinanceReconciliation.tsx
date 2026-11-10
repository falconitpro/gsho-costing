import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney } from "../api";
import FinanceExportButton from "./FinanceExportButton";

// «Финансы → Сверка и уточнения» (docs/finance/05, раздел 13). Очереди проблем со ссылкой на место исправления
// и «Запросить уточнение»: ответственный получает только показанный перед отправкой контекст.

type Obj = { type: string; id: string; url: string; title: string };
type Item = { queue: string; severity: "high" | "medium" | "info"; object: Obj; deal: { id: string; number: string; url: string } | null; problem: string;
  amount: string | null; currency: string; on: string | null; counterparty: string; responsible: string; action: { title: string; url: string };
  open_questions: number; can_ask: boolean };
type Data = { queues: { code: string; title: string; count: number }[]; items: Item[]; total: number; severity_titles: Record<string, string> };
type Q = { id: string; question: string; context: { title: string; fields: Record<string, string> }; status: string; status_title: string; answer: string; answered_at: string | null;
  due_on: string | null; asked_by: string; assignee: { id: string; name: string }; review_comment: string; reviewed_by: string; source_update_ref: string; source_updated_at: string | null;
  subject_url: string | null; deal: { id: string; number: string } | null; files: { id: string; name: string; size: number }[];
  history: { at: string; by: string; action: string; comment: string }[]; created_at: string };

const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);
const SEV: Record<string, string> = { high: "bad", medium: "warn", info: "" };
const Q_BADGE: Record<string, string> = { open: "warn", answered: "info", verified: "ok", rejected: "bad", closed: "" };
const ACTION: Record<string, string> = { create: "вопрос задан", answer: "ответ", rejected: "ответ отклонён", verified: "ответ проверен", close: "закрыт" };

export default function FinanceReconciliation({ sp, go, stamp, canEdit }: { sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number; canEdit: boolean }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [ask, setAsk] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const queue = sp.get("queue") || "";
  const qid = sp.get("question");
  useEffect(() => {
    api.get<Data>(`/api/finance/reconciliation${queue ? `?queue=${queue}` : ""}`).then((x) => { setD(x); setErr(""); }).catch((e) => setErr(errText(e)));
  }, [queue, stamp, reload]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted">Загружаю…</div>;
  const key = (i: Item) => `${i.queue}:${i.object.type}:${i.object.id}`;
  return (
    <>
      {qid && <QuestionCard id={qid} canEdit={canEdit} onClose={() => go({ question: null })} onChanged={() => setReload((x) => x + 1)} />}
      <div className="tabs" style={{ flexWrap: "wrap" }}>
        <button className={!queue ? "active" : ""} onClick={() => go({ queue: null })}>Все · {d.queues.reduce((a, q) => a + q.count, 0)}</button>
        {d.queues.map((q) => <button key={q.code} className={queue === q.code ? "active" : ""} onClick={() => go({ queue: q.code })} disabled={!q.count && queue !== q.code}
          style={!q.count ? { opacity: 0.5 } : undefined}>{q.title} · {q.count}</button>)}
        <span style={{ marginLeft: "auto" }}><FinanceExportButton kind="reconciliation" params={{ queue }} /></span>
      </div>
      {d.items.length === 0 ? <div className="card muted">{queue ? "В этой очереди проблем нет." : "Расхождений нет: всё разнесено, подтверждено и с датами."}</div> : (
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <table style={{ minWidth: 1000 }}>
            <thead><tr><th>Важность</th><th>Объект</th><th>Что не так</th><th className="num">Сумма</th><th>Дата</th><th>Ответственный</th><th /></tr></thead>
            <tbody>{d.items.map((i) => (
              <Fragment key={key(i)}>
                <tr>
                  <td><span className={`badge ${SEV[i.severity]}`}>{d.severity_titles[i.severity]}</span></td>
                  <td><Link to={i.object.url}>{i.object.title}</Link><div className="muted" style={{ fontSize: 11 }}>{i.counterparty}{i.deal ? <> · <Link to={i.deal.url}>{i.deal.number}</Link></> : null}</div></td>
                  <td style={{ fontSize: 13 }}>{i.problem}{i.open_questions > 0 && <div><span className="badge info">открытых уточнений: {i.open_questions}</span></div>}</td>
                  <td className="num">{i.amount ? fmtMoney(i.amount, i.currency) : "—"}</td><td>{fmtDate(i.on)}</td><td>{i.responsible || <span className="muted">—</span>}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{i.object.type === "question"
                    ? <button className="btn ghost" onClick={() => go({ question: i.object.id })}>{i.action.title}</button>
                    : <Link className="btn ghost" to={i.action.url} style={{ textDecoration: "none" }}>{i.action.title}</Link>}
                    {i.can_ask && <button className="btn ghost" onClick={() => setAsk(ask === key(i) ? null : key(i))}>{ask === key(i) ? "Отмена" : "Запросить уточнение"}</button>}</td>
                </tr>
                {ask === key(i) && <tr><td colSpan={7}><AskForm obj={i.object} onDone={(id) => { setAsk(null); setReload((x) => x + 1); go({ question: id }); }} /></td></tr>}
              </Fragment>))}</tbody>
          </table>
        </div>)}
    </>
  );
}

export function AskForm({ obj, onDone }: { obj: Pick<Obj, "type" | "id">; onDone: (id: string) => void }) {
  const [users, setUsers] = useState<{ id: string; name: string }[]>([]);
  const [ctx, setCtx] = useState<{ title: string; fields: Record<string, string> } | null>(null);
  const [f, setF] = useState({ assignee_id: "", due_on: "", question: "" });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<{ items: { id: string; name: string }[] }>("/api/finance/questions/assignees").then((r) => setUsers(r.items)).catch((e) => setMsg(errText(e)));
    api.get<typeof ctx>(`/api/finance/questions/context?subject_type=${obj.type}&subject_id=${encodeURIComponent(obj.id)}`).then(setCtx).catch((e) => setMsg(errText(e)));
  }, [obj.type, obj.id]);
  async function send() {
    setBusy(true); setMsg("");
    try {
      const q = await api.post<{ id: string }>("/api/finance/questions", { subject_type: obj.type, subject_id: obj.id, assignee_id: f.assignee_id, question: f.question, due_on: f.due_on || null });
      onDone(q.id);
    } catch (e) { setMsg(errText(e)); } finally { setBusy(false); }
  }
  return (
    <div style={{ padding: "6px 4px" }}>
      {msg && <div className="alert error">{msg}</div>}
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-start" }}>
        <label className="row">Ответственный <select value={f.assignee_id} onChange={(e) => setF({ ...f, assignee_id: e.target.value })}><option value="">— выберите —</option>
          {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
        <label className="row">Срок <input type="date" value={f.due_on} onChange={(e) => setF({ ...f, due_on: e.target.value })} /></label>
        <textarea placeholder="Вопрос — что нужно уточнить" value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} rows={2} style={{ flex: "1 1 320px", minWidth: 240 }} />
        <button className="btn" disabled={busy || !f.assignee_id || f.question.trim().length < 5} onClick={send}>Отправить</button>
      </div>
      {ctx && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}><b>Ответственный увидит:</b> {ctx.title} · {Object.entries(ctx.fields).map(([k, v]) => `${k}: ${v}`).join(" · ")}.
        Остальные данные раздела ему недоступны.</div>}
    </div>
  );
}

function QuestionCard({ id, canEdit, onClose, onChanged }: { id: string; canEdit: boolean; onClose: () => void; onChanged: () => void }) {
  const [q, setQ] = useState<Q | null>(null);
  const [msg, setMsg] = useState("");
  const [r, setR] = useState({ review_comment: "", source_update_ref: "" });
  const load = () => api.get<Q>(`/api/finance/questions/${id}`).then(setQ).catch((e) => setMsg(errText(e)));
  useEffect(() => { load(); }, [id]);
  async function act(url: string, body: unknown, done: string) {
    setMsg("");
    try { setQ(await api.post<Q>(url, body)); setMsg(done); onChanged(); } catch (e) { setMsg(errText(e)); }
  }
  if (!q) return msg ? <div className="alert error">{msg}</div> : null;
  return (
    <div className="card" style={{ borderLeft: "4px solid var(--info, #3a6ea5)" }}>
      <div className="section-head"><h2 style={{ margin: 0 }}>Уточнение: {q.context.title}</h2><button className="btn ghost" onClick={onClose}>Закрыть карточку</button></div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
        <span className={`badge ${Q_BADGE[q.status] ?? ""}`}>{q.status_title}</span>
        <span className="muted">спросил(а) {q.asked_by} · ответственный {q.assignee.name}{q.due_on ? ` · срок ${fmtDate(q.due_on)}` : ""}{q.deal ? ` · ${q.deal.number}` : ""}</span>
        {q.subject_url && <Link to={q.subject_url}>Открыть объект</Link>}
      </div>
      <div style={{ whiteSpace: "pre-wrap" }}>{q.question}</div>
      <div className="muted" style={{ fontSize: 12, margin: "4px 0 8px" }}>Контекст у ответственного: {Object.entries(q.context.fields).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
      {q.answer ? <div className="alert ok" style={{ whiteSpace: "pre-wrap" }}><b>Ответ{q.answered_at ? ` от ${fmtDate(q.answered_at)}` : ""}:</b> {q.answer}</div> : <div className="muted">Ответа пока нет.</div>}
      {q.files.length > 0 && <div style={{ fontSize: 13 }}>Файлы: {q.files.map((f) => <a key={f.id} href={`/api/finance/questions/${q.id}/files/${f.id}`} style={{ marginRight: 8 }}>{f.name}</a>)}</div>}
      {q.review_comment && <div className="muted" style={{ fontSize: 12 }}>Комментарий проверки: {q.review_comment}</div>}
      {q.source_update_ref && <div className="alert ok">Источник обновлён: {q.source_update_ref}</div>}
      {canEdit && q.status === "answered" && (
        <div className="row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          <input placeholder="комментарий проверки" value={r.review_comment} onChange={(e) => setR({ ...r, review_comment: e.target.value })} style={{ width: 260 }} />
          <input placeholder="что изменено в источнике (доп. соглашение, отметка, операция)" value={r.source_update_ref} onChange={(e) => setR({ ...r, source_update_ref: e.target.value })} style={{ width: 340 }} />
          <button className="btn" onClick={() => act(`/api/finance/questions/${q.id}/review`, { decision: "verified", ...r }, "Ответ проверен")}>{r.source_update_ref.trim() ? "Ответ проверен, данные исправлены" : "Ответ проверен"}</button>
          <button className="btn secondary" disabled={r.review_comment.trim().length < 3} onClick={() => act(`/api/finance/questions/${q.id}/review`, { decision: "rejected", review_comment: r.review_comment }, "Отправлено на уточнение")}>Вернуть на уточнение</button>
        </div>)}
      {q.status !== "closed" && canEdit && q.status !== "answered" && <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={() => act(`/api/finance/questions/${q.id}/close`, { reason: "закрыт в разделе" }, "Вопрос закрыт")}>Закрыть вопрос</button></div>}
      <details style={{ marginTop: 8 }}><summary className="muted">История ({q.history.length})</summary>
        <ul style={{ margin: "4px 0 0 18px", fontSize: 12 }}>{q.history.map((h, n) => <li key={n}>{fmtDate(h.at)} · {h.by} · {ACTION[h.action] || h.action}{h.comment ? ` — ${h.comment}` : ""}</li>)}</ul></details>
    </div>
  );
}
