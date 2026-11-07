import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Сканы подписанных контрактов: с клиентом и с заводом. Загрузка — после отметки подписания; файлы хранятся в документах сделки.

type F = { id: string; file_name: string; url: string; uploaded_by: string; uploaded_at: string | null; uploaded_by_id: string; comment: string };
type Kind = { title: string; signed: boolean; files: F[] };
type Resp = { client: Kind; factory: Kind; can_upload: boolean };

export default function ContractScans({ dealId, meId, canRemoveAll, only, version = 0 }: { dealId: string; meId: string; canRemoveAll: boolean; only?: "client" | "factory"; version?: number }) {
  const [d, setD] = useState<Resp | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get<Resp>(`/api/deals/${dealId}/contract-scans`).then(setD).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [dealId, version]);
  if (!d) return msg ? <div className="muted" style={{ fontSize: 12 }}>{msg}</div> : null;
  async function upload(kind: string, file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try { setD(await api.upload<Resp>(`/api/deals/${dealId}/contract-scans`, file, { kind })); setMsg(`Загружено: ${file.name}`); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  const kinds = (only ? [only] : ["client", "factory"]) as ("client" | "factory")[];
  return (
    <div style={{ marginTop: only ? 4 : 10 }}>
      {!only && <h3 style={{ margin: "4px 0" }}>Сканы подписанных контрактов</h3>}
      {kinds.map((k) => {
        const x = d[k];
        return (
          <div key={k} style={{ marginBottom: 6 }}>
            {!only && <b style={{ fontSize: 13 }}>{x.title}</b>}
            {x.files.map((f) => (
              <div key={f.id} className="row" style={{ fontSize: 13, marginLeft: only ? 0 : 10, gap: 6 }}>
                <span>📎</span><a href={f.url} target="_blank" rel="noreferrer" style={{ overflowWrap: "anywhere" }}>{f.file_name}</a>
                <span className="muted" style={{ fontSize: 11 }}>{fmtDate(f.uploaded_at)} · {f.uploaded_by}</span>
                {(canRemoveAll || f.uploaded_by_id === meId) && <a href="#" style={{ fontSize: 11 }} onClick={(e) => {
                  e.preventDefault(); if (confirm("Убрать скан из документов сделки?")) api.del(`/api/deals/${dealId}/documents/${f.id}`).then(load).catch((z) => setMsg((z as Error).message)); }}>убрать</a>}
              </div>))}
            {x.files.length === 0 && <div className="muted" style={{ fontSize: 12, marginLeft: only ? 0 : 10 }}>
              {x.signed ? <span style={{ color: "var(--warn, #b26a00)" }}>скан не приложен</span> : "скан можно приложить после ввода даты договора или отметки подписания"}</div>}
            {d.can_upload && x.signed && (
              <label className="btn ghost" style={{ fontSize: 12, padding: "2px 8px", marginLeft: only ? 0 : 10, cursor: busy ? "wait" : "pointer" }}>
                {x.files.length ? "Приложить ещё файл" : "Приложить скан"}
                <input type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.zip" style={{ display: "none" }} disabled={busy}
                  onChange={(e) => { upload(k, e.target.files?.[0]); e.target.value = ""; }} />
              </label>)}
          </div>);
      })}
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
    </div>
  );
}
