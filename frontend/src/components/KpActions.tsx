import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import KpRecipients, { newDispatchKey, type Candidate, type Picked } from "./KpRecipients";

/** КП в карточке сделки: «Сформировать КП», «Выгрузить КП» (Word / PDF), «Отправить клиенту» (письмо из системы, отметка «КП отправлено»). */
export default function KpActions({ dealId, status, canAct, canMark = canAct, onDone }: { dealId: string; status: string; canAct: boolean; canMark?: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [cand, setCand] = useState<Candidate[]>([]);   // D-188: контакты клиента из справочника, ничего не отмечено
  const [clientId, setClientId] = useState<string | null>(null);
  const [picked, setPicked] = useState<{ p: Picked; ok: boolean }>({ p: { to: [], cc: [] }, ok: false });
  const [key, setKey] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [format, setFormat] = useState("pdf");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [mailOn, setMailOn] = useState(false);
  const ready = ["approved", "kp_sent", "contract", "execution"].includes(status);
  useEffect(() => { if (ready) api.get<{ mail_configured: boolean }>(`/api/deals/${dealId}/kp/recipients`).then((r) => setMailOn(r.mail_configured)).catch(() => undefined); }, [dealId, ready]);
  if (!ready) return null;

  async function build() {
    setBusy(true);
    try { const r = await api.post<{ version_no: number }>(`/api/deals/${dealId}/kp/build`); setMsg(`КП сформировано заново по версии ${r.version_no}`); onDone(); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function openSend() {
    const r = await api.get<{ candidates: Candidate[]; subject: string; body: string; client_id: string | null }>(`/api/deals/${dealId}/kp/recipients`);
    setCand(r.candidates); setClientId(r.client_id); setPicked({ p: { to: [], cc: [] }, ok: false }); setKey(newDispatchKey());
    setSubject(r.subject); setBody(r.body); setOpen(true); setMsg("");
  }
  async function send() {
    setBusy(true);
    try {
      const r = await api.post<{ sent_to: string[]; provider: string }>(`/api/deals/${dealId}/kp/send`, { to: picked.p.to, cc: picked.p.cc, subject, body, format, dispatch_key: key });
      setMsg(`КП отправлено: ${r.sent_to.join(", ")}`);
      setOpen(false); onDone();
    } catch (e) { const d = (e as ApiError).detail as { message?: string } | string; setMsg(typeof d === "string" ? d : d?.message || (e as Error).message); }
    finally { setBusy(false); }
  }

  async function markSent() {
    setBusy(true);
    try {
      await api.post(`/api/deals/${dealId}/kp-sent`, { recipients: picked.p.to, cc: picked.p.cc, sent_via: "outlook", dispatch_key: key });
      setMsg(`Отмечено: КП отправлено (${[...picked.p.to, ...picked.p.cc].join(", ")})`); setOpen(false); onDone();
    } catch (e) { const d = (e as ApiError).detail as { message?: string } | string; setMsg(typeof d === "string" ? d : d?.message || (e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {canAct && <button className="btn secondary" disabled={busy} onClick={build} title="пересобрать файл КП по утверждённой версии расчёта">Сформировать КП</button>}
        <a className="btn ghost" href={`/api/deals/${dealId}/kp/file?format=docx`}>Выгрузить КП: Word</a>
        <a className="btn ghost" href={`/api/deals/${dealId}/kp/file?format=pdf`}>PDF</a>
        {canAct && status === "approved" && mailOn && <button className="btn" disabled={busy} onClick={openSend}>Отправить клиенту</button>}
        {canMark && status === "approved" && !mailOn && <button className="btn" disabled={busy} onClick={() => openSend()} title="КП отправлено своей почтой — отметить в сделке">Отправил клиенту — отметить</button>}
      </div>
      {msg && <div className="alert info" style={{ marginTop: 6 }}>{msg}</div>}
      {open && (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--accent)" }}>
          <h2 style={{ marginTop: 0 }}>{mailOn ? "Отправить КП клиенту" : "КП отправлено клиенту"}</h2>
          {!mailOn && <div className="muted" style={{ marginBottom: 4 }}>Почта на сервере не подключена: отметьте, кому вы отправили КП своей почтой, — статус, лента, задачи и история цен обновятся так же, как при отправке из системы.</div>}
          <KpRecipients dealId={dealId} candidates={cand} clientId={clientId} onChange={(p, ok) => setPicked({ p, ok })} />
          {mailOn && <>
            <label className="f">Тема<input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
            <label className="f">Текст письма<textarea rows={6} style={{ width: "100%" }} value={body} onChange={(e) => setBody(e.target.value)} /></label>
            <label className="row">Вложение
              <select value={format} onChange={(e) => setFormat(e.target.value)}><option value="pdf">КП в PDF</option><option value="docx">КП в Word</option><option value="both">Word и PDF</option></select></label></>}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={busy || !picked.ok} title={picked.ok ? "" : "сначала проверьте получателей"} onClick={mailOn ? send : markSent}>{mailOn ? "Отправить и отметить «КП отправлено»" : "Отметить «КП отправлено»"}</button>
            <button className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
          </div>
        </div>)}
    </>
  );
}
