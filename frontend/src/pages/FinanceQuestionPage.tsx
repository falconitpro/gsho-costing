import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, fmtDate } from "../api";

// Уточнение «Финансов» для ответственного (D-141): только вопрос и показанный ему контекст, ответ и файл.
// Права на раздел «Финансы» не нужно; чужой вопрос сервер не отдаёт.

type Scoped = { id: string; subject_title: string; question: string; context: { title: string; fields: Record<string, string> }; asked_by: string; due_on: string | null;
  status: string; status_title: string; answer: string; answered_at: string | null; review_comment: string; files: { id: string; name: string; size: number }[]; can_answer: boolean };
const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);

export default function FinanceQuestionPage() {
  const { id = "" } = useParams();
  const [q, setQ] = useState<Scoped | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Scoped>(`/api/finance/questions/${id}`).then((x) => { setQ(x); setText(x.status === "rejected" ? x.answer : ""); }).catch((e) => setErr(errText(e))); }, [id]);
  async function upload(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    try { setQ(await api.upload<Scoped>(`/api/finance/questions/${id}/files`, f)); setMsg(`Файл «${f.name}» приложен`); } catch (e) { setMsg(errText(e)); } finally { setBusy(false); }
  }
  async function send() {
    setBusy(true);
    try { setQ(await api.post<Scoped>(`/api/finance/questions/${id}/answer`, { answer: text })); setMsg("Ответ отправлен — спасибо"); } catch (e) { setMsg(errText(e)); } finally { setBusy(false); }
  }
  if (err) return <div className="card"><div className="alert error">{err}</div><Link to="/tasks">← Мои задачи</Link></div>;
  if (!q) return <div className="muted">Загружаю…</div>;
  return (
    <>
      <div className="page-head"><h1>Уточнение по «Финансам»</h1><span className="muted">{q.status_title}</span><div className="push"><Link to="/tasks">← Мои задачи</Link></div></div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <div className="muted" style={{ fontSize: 12 }}>Спрашивает {q.asked_by}{q.due_on ? ` · ответить до ${fmtDate(q.due_on)}` : ""} · {q.subject_title}</div>
        <h2 style={{ margin: "4px 0" }}>{q.context.title}</h2>
        <table style={{ maxWidth: 640, fontSize: 13 }}><tbody>{Object.entries(q.context.fields).map(([k, v]) => <tr key={k}><td className="muted">{k}</td><td>{v}</td></tr>)}</tbody></table>
        <p style={{ whiteSpace: "pre-wrap", fontSize: 15 }}><b>Вопрос:</b> {q.question}</p>
        {q.status === "rejected" && q.review_comment && <div className="alert warn">Нужно дополнить: {q.review_comment}</div>}
        {!q.can_answer && q.answer && <div className="alert ok" style={{ whiteSpace: "pre-wrap" }}><b>Ваш ответ{q.answered_at ? ` от ${fmtDate(q.answered_at)}` : ""}:</b> {q.answer}</div>}
        {q.files.length > 0 && <div style={{ fontSize: 13, marginBottom: 6 }}>Файлы: {q.files.map((f) => <a key={f.id} href={`/api/finance/questions/${q.id}/files/${f.id}`} style={{ marginRight: 8 }}>{f.name}</a>)}</div>}
        {q.can_answer && (
          <>
            <textarea rows={4} style={{ width: "100%", maxWidth: 720 }} placeholder="Ваш ответ" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
              <button className="btn" disabled={busy || (text.trim().length < 2 && !q.files.length)} onClick={send}>Отправить ответ</button>
              <label className="btn secondary" style={{ cursor: "pointer" }}>Приложить файл<input type="file" hidden onChange={(e) => upload(e.target.files?.[0])} /></label>
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Ответ проверит финансовый директор. Если нужно изменить договор, этап или документ — сделайте это там, где вы обычно работаете с ними.</div>
          </>)}
      </div>
    </>
  );
}
