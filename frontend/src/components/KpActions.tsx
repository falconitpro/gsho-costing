import { useState } from "react";
import { api, ApiError } from "../api";

type Rcpt = { name: string; position: string; email: string };

/** КП в карточке сделки: «Сформировать КП», «Выгрузить КП» (Word / PDF), «Отправить клиенту» (письмо из системы, отметка «КП отправлено»). */
export default function KpActions({ dealId, status, canAct, onDone }: { dealId: string; status: string; canAct: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [rcpts, setRcpts] = useState<Rcpt[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [extra, setExtra] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [format, setFormat] = useState("pdf");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const ready = ["approved", "kp_sent", "contract", "execution"].includes(status);
  if (!ready) return null;

  async function build() {
    setBusy(true);
    try { const r = await api.post<{ version_no: number }>(`/api/deals/${dealId}/kp/build`); setMsg(`КП сформировано заново по версии ${r.version_no}`); onDone(); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function openSend() {
    const r = await api.get<{ recipients: Rcpt[]; subject: string; body: string }>(`/api/deals/${dealId}/kp/recipients`);
    setRcpts(r.recipients); setSel(new Set(r.recipients.slice(0, 1).map((x) => x.email))); setSubject(r.subject); setBody(r.body); setOpen(true); setMsg("");
  }
  async function send() {
    const to = [...sel, ...extra.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean)];
    setBusy(true);
    try {
      const r = await api.post<{ sent_to: string[]; provider: string }>(`/api/deals/${dealId}/kp/send`, { to, subject, body, format });
      setMsg(`КП отправлено: ${r.sent_to.join(", ")}${r.provider === "console" ? " (почта на сервере не подключена — письмо записано в журнал, не доставлено)" : ""}`);
      setOpen(false); onDone();
    } catch (e) { const d = (e as ApiError).detail as { message?: string } | string; setMsg(typeof d === "string" ? d : d?.message || (e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {canAct && <button className="btn secondary" disabled={busy} onClick={build} title="пересобрать файл КП по утверждённой версии расчёта">Сформировать КП</button>}
        <a className="btn ghost" href={`/api/deals/${dealId}/kp/file?format=docx`}>Выгрузить КП: Word</a>
        <a className="btn ghost" href={`/api/deals/${dealId}/kp/file?format=pdf`}>PDF</a>
        {canAct && status === "approved" && <button className="btn" disabled={busy} onClick={openSend}>Отправить клиенту</button>}
      </div>
      {msg && <div className="alert info" style={{ marginTop: 6 }}>{msg}</div>}
      {open && (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--accent)" }}>
          <h2 style={{ marginTop: 0 }}>Отправить КП клиенту</h2>
          <div className="muted" style={{ marginBottom: 4 }}>Получатели — контакты клиента; можно добавить адреса вручную через запятую.</div>
          {rcpts.length === 0 && <div className="alert warn">В карточке клиента нет контактов с e-mail — добавьте адрес вручную.</div>}
          {rcpts.map((r) => (
            <label key={r.email} className="row"><input type="checkbox" checked={sel.has(r.email)} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(r.email); else n.delete(r.email); setSel(n); }} />
              {r.name || r.email}{r.position && <span className="muted">, {r.position}</span>} <span className="muted">&lt;{r.email}&gt;</span></label>))}
          <label className="f" style={{ marginTop: 6 }}>Ещё адреса<input value={extra} placeholder="name@company.ru, …" onChange={(e) => setExtra(e.target.value)} /></label>
          <label className="f">Тема<input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
          <label className="f">Текст письма<textarea rows={6} style={{ width: "100%" }} value={body} onChange={(e) => setBody(e.target.value)} /></label>
          <label className="row">Вложение
            <select value={format} onChange={(e) => setFormat(e.target.value)}><option value="pdf">КП в PDF</option><option value="docx">КП в Word</option><option value="both">Word и PDF</option></select></label>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={busy || (sel.size === 0 && !extra.trim())} onClick={send}>Отправить и отметить «КП отправлено»</button>
            <button className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
          </div>
        </div>)}
    </>
  );
}
