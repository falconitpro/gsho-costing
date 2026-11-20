import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import { askPrompt } from "./Dialogs";

type R = { id: string; user: string; deal_id: string; deal_number: string; file_name: string; path: string; reason: string; requested_at: string };

/** Запросы на скачивание файлов (D-077): ЗГД разрешает на 24 часа или отклоняет; файл можно открыть для просмотра. */
export default function DownloadRequests() {
  const [rows, setRows] = useState<R[]>([]);
  const [err, setErr] = useState("");
  const load = () => api.get<R[]>("/api/files-download-requests?status=pending").then(setRows).catch(() => setRows([]));
  useEffect(() => { load(); }, []);
  if (!rows.length) return null;
  const decide = async (id: string, approve: boolean) => {
    const comment = approve ? "" : (await askPrompt("Причина отказа (необязательно)")) ?? "";
    api.post(`/api/files-download-requests/${id}`, { approve, comment }).then(load).catch((e) => setErr((e as Error).message));
  };
  return (
    <div className="wp-card" style={{ marginBottom: 14 }}>
      <div className="wp-head"><h2>Запросы на скачивание файлов <span style={{ color: "var(--accent)" }}>· {rows.length}</span></h2></div>
      {err && <div className="alert error">{err}</div>}
      <table><tbody>{rows.map((r) => (
        <tr key={r.id}><td><b>{r.user}</b><div className="muted" style={{ fontSize: 12 }}>{fmtDate(r.requested_at)}</div></td>
          <td><a href={`/api/files/${r.path}`} title={r.file_name}>{r.file_name}</a><div className="muted" style={{ fontSize: 12 }}>сделка {r.deal_number}{r.reason ? ` · ${r.reason}` : ""}</div></td>
          <td style={{ whiteSpace: "nowrap" }}><button className="btn" onClick={() => decide(r.id, true)}>Разрешить на 24 ч</button> <button className="btn ghost" onClick={() => decide(r.id, false)}>Отклонить</button></td></tr>))}</tbody></table>
    </div>);
}
