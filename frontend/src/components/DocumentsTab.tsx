import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import type { Deal, Me } from "../types";

type Doc = { id: string; category: string; category_title: string; legal: boolean; file_name: string; comment: string; size?: number; url: string;
  uploaded_by: string; uploaded_at: string | null; uploaded_by_id: string };
type Review = { status?: string; comment?: string; by_name?: string; at?: string; title: string };
type Resp = { documents: Doc[]; client_categories: Record<string, string>; legal_categories: Record<string, string>; review: Review;
  can_upload_client: boolean; can_upload_legal: boolean; can_review: boolean };

export const REVIEW_BADGE: Record<string, string> = { in_review: "warn", ok: "ok", remarks: "bad" };

/** Документы сделки: файлы клиента (тендерная документация, типовая форма договора, ТЗ) и юридические документы
 *  (протокол разногласий, заключение юриста). Юрист проверяет документы и отмечает результат. */
export default function DocumentsTab({ deal, me, onChanged }: { deal: Deal; me: Me; onChanged: () => void }) {
  const [d, setD] = useState<Resp | null>(null);
  const [files, setFiles] = useState<FileList | null>(null);
  const [category, setCategory] = useState("");
  const [comment, setComment] = useState("");
  const [rStatus, setRStatus] = useState("ok");
  const [rComment, setRComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const base = `/api/deals/${deal.id}`;
  const load = () => api.get<Resp>(`${base}/documents`).then((r) => {
    setD(r);
    setCategory((c) => c || Object.keys(r.can_upload_client ? r.client_categories : r.legal_categories)[0] || "");
  }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [deal.id, me.id]);
  if (!d) return <div className="card">{err ? <div className="alert error">{err}</div> : "Загрузка…"}</div>;

  const cats: Record<string, string> = { ...(d.can_upload_client ? d.client_categories : {}), ...(d.can_upload_legal ? d.legal_categories : {}) };
  const isAdminLike = me.roles.some((r) => ["cd", "gd"].includes(r.code));

  async function upload() {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) await api.upload(`${base}/documents`, f, { category, comment });
      setMsg(`Загружено файлов: ${files.length}` + (["tender", "contract_form"].includes(category) ? " · юристу поставлена задача на проверку" : ""));
      setFiles(null); setComment(""); setErr(""); await load(); onChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function remove(doc: Doc) {
    if (!window.confirm(`Убрать файл «${doc.file_name}» из документов сделки?`)) return;
    try { await api.del(`${base}/documents/${doc.id}`); await load(); onChanged(); } catch (e) { setErr((e as Error).message); }
  }
  async function review() {
    setBusy(true);
    try {
      await api.post(`${base}/legal-review`, { status: rStatus, comment: rComment });
      setMsg("Результат юридической проверки сохранён"); setRComment(""); setErr(""); await load(); onChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const group = (legal: boolean) => d.documents.filter((x) => x.legal === legal);
  const table = (docs: Doc[]) => docs.length === 0 ? <div className="muted">файлов нет</div> : (
    <table><thead><tr><th>Вид</th><th>Файл</th><th>Комментарий</th><th>Загрузил</th><th>Дата</th><th /></tr></thead>
      <tbody>{docs.map((x) => (
        <tr key={x.id}><td>{x.category_title}</td><td><a href={x.url}>{x.file_name}</a></td><td>{x.comment}</td><td>{x.uploaded_by}</td>
          <td>{x.uploaded_at ? fmtDate(x.uploaded_at) : ""}</td>
          <td>{(x.uploaded_by_id === me.id || isAdminLike) && <button className="btn ghost" style={{ padding: "2px 6px" }} title="убрать из документов сделки" onClick={() => remove(x)}>×</button>}</td></tr>))}</tbody></table>);

  return (
    <div>
      {err && <div className="alert error">{err}</div>}
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>Юридическая проверка</h2>
          <span className={"badge " + (REVIEW_BADGE[d.review.status || ""] || "")}>{d.review.title}</span>
        </div>
        {d.review.status && <div className="muted" style={{ marginTop: 4 }}>{d.review.by_name}{d.review.at ? `, ${fmtDate(d.review.at)}` : ""}{d.review.comment ? ` — ${d.review.comment}` : ""}</div>}
        {d.can_review && (
          <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <select value={rStatus} onChange={(e) => setRStatus(e.target.value)}>
              <option value="ok">проверено, замечаний нет</option>
              <option value="remarks">есть замечания (протокол разногласий)</option>
              <option value="in_review">вернуть на проверку</option>
            </select>
            <input placeholder={rStatus === "remarks" ? "замечания (обязательно)" : "комментарий"} value={rComment} onChange={(e) => setRComment(e.target.value)} style={{ width: 380 }} />
            <button className="btn" disabled={busy || (rStatus === "remarks" && !rComment.trim())} onClick={review}>Сохранить результат</button>
          </div>)}
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Юрист проверяет тендерную документацию и типовую форму договора клиента, при необходимости готовит протокол разногласий и загружает его сюда.</div>
      </div>

      {Object.keys(cats).length > 0 && (
        <div className="card">
          <b>Загрузить документы</b>
          <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {d.can_upload_client && <optgroup label="Файлы клиента">{Object.entries(d.client_categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</optgroup>}
              {d.can_upload_legal && <optgroup label="Юридические документы">{Object.entries(d.legal_categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</optgroup>}
            </select>
            <input type="file" multiple onChange={(e) => setFiles(e.target.files)} />
            <input placeholder="комментарий" value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: 260 }} />
            <button className="btn" disabled={busy || !files?.length || !category} onClick={upload}>Загрузить</button>
          </div>
        </div>)}

      <div className="card"><b>Файлы клиента</b><div style={{ marginTop: 6 }}>{table(group(false))}</div></div>
      <div className="card"><b>Юридические документы</b><div style={{ marginTop: 6 }}>{table(group(true))}</div></div>
    </div>
  );
}
