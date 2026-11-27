import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Empty, ErrorState, Loading } from "./States";

/** J-b (D-196): проверка применимости — очередь связей «деталь — модель» по статусам. Только новый вид.
 *  Все связи, заведённые до J-b, — «не подтверждено» (решение владельца 7); здесь их подтверждают или отклоняют с основанием. */

export type ApplRow = { id: string; part_id: string; part_article: string; part_name: string; model_title: string; assembly: string; status: string;
  status_title: string; serial_from: string; serial_to: string; basis_kind: string; basis: string; verified_by_name: string; verified_at: string | null;
  execution_name: string };
type Data = { items: ApplRow[]; counts: Record<string, number>; statuses: Record<string, string>; basis_kinds: Record<string, string>; need_basis: string[]; can_edit: boolean };

export const APPL_TONE: Record<string, string> = { confirmed: "ok", probable: "info", unverified: "warn", not_applicable: "bad", replaced: "" };

export default function ApplicabilityQueue() {
  const [status, setStatus] = useState("unverified");
  const [q, setQ] = useState("");
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [ed, setEd] = useState<ApplRow | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Data>(`/api/parts/applicability?limit=200&status=${status}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`)
    .then((r) => { setD(r); setErr(""); }).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, [status]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (err && !d) return <ErrorState hint={err} onRetry={load} />;
  if (!d) return <Loading />;
  const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
  return (
    <div className="aq">
      <p className="muted">Насколько можно доверять связи «деталь подходит к модели». Подбор запчастей к машине и в наряд показывает статус;
        «Не подходит» и номера вне диапазона серийных номеров в подбор не попадают.</p>
      <div className="pc-toolbar sv-toolbar">
        <div className="pc-groups" role="group" aria-label="Статус применимости">
          {Object.entries(d.statuses).map(([k, t]) => (
            <button key={k} type="button" className={status === k ? "on" : ""} aria-pressed={status === k} onClick={() => setStatus(k)}>{t} · {d.counts[k] || 0}</button>))}
          <button type="button" className={status === "" ? "on" : ""} aria-pressed={status === ""} onClick={() => setStatus("")}>Все · {total}</button>
        </div>
        <form className="row sv-search" onSubmit={(e) => { e.preventDefault(); load(); }}>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Артикул, деталь, модель, узел" aria-label="Поиск" />
          <button className="btn ghost">Найти</button></form>
      </div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {ed && <VerifyForm row={ed} d={d} onDone={(r) => { setEd(null); setMsg(`${r.part_article} · ${r.model_title}: ${r.status_title}`); load(); }} onCancel={() => setEd(null)} />}
      {d.items.length === 0 ? <Empty title={status === "unverified" ? "Непроверенных связей нет" : "Связей с таким статусом нет"} /> : (
        <table className="pc-table nv-cards sv-table">
          <thead><tr><th>Деталь</th><th>Модель · узел</th><th>Серийные номера</th><th>Статус и основание</th><th /></tr></thead>
          <tbody>{d.items.map((r) => (
            <tr key={r.id}>
              <td><Link className="pc-art" to={`/catalog?s=parts&part=${r.part_id}`}>{r.part_article}</Link><div className="pc-sub">{r.part_name}</div></td>
              <td data-label="Модель · узел"><div className="pc-name">{r.model_title || "—"}</div><div className="pc-sub">{[r.execution_name, r.assembly].filter(Boolean).join(" · ") || "узел не указан"}</div></td>
              <td data-label="Серийные номера">{r.serial_from || r.serial_to ? `${r.serial_from || "…"} — ${r.serial_to || "…"}` : <span className="muted">все</span>}</td>
              <td data-label="Статус"><span className={`badge ${APPL_TONE[r.status] || ""}`}>{r.status_title}</span>
                {(r.basis || r.basis_kind) && <div className="pc-sub">{[d.basis_kinds[r.basis_kind], r.basis].filter(Boolean).join(": ")}</div>}
                {r.verified_by_name && <div className="pc-sub">{r.verified_by_name} · {fmtDate(r.verified_at)}</div>}</td>
              <td>{d.can_edit && <button className="btn ghost" onClick={() => setEd(r)}>Проверить</button>}</td>
            </tr>))}</tbody>
        </table>)}
    </div>);
}

function VerifyForm({ row, d, onDone, onCancel }: { row: ApplRow; d: Data; onDone: (r: ApplRow) => void; onCancel: () => void }) {
  const [f, setF] = useState({ status: row.status === "unverified" ? "confirmed" : row.status, basis_kind: row.basis_kind, basis: row.basis,
    serial_from: row.serial_from, serial_to: row.serial_to });
  const [err, setErr] = useState("");
  const need = d.need_basis.includes(f.status) && !f.basis.trim() && !f.basis_kind;
  const save = async () => {
    try { onDone(await api.post<ApplRow>(`/api/parts/applicability/${row.id}/verify`, f)); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card sv-form">
      <h3>{row.part_article} · {row.model_title}{row.assembly ? ` · ${row.assembly}` : ""}</h3>
      {err && <div className="alert error">{err}</div>}
      <div className="grid cols-3">
        <label className="f">Статус<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          {Object.entries(d.statuses).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Основание{d.need_basis.includes(f.status) ? " *" : ""}<select value={f.basis_kind} onChange={(e) => setF({ ...f, basis_kind: e.target.value })}>
          <option value="">— выберите —</option>{Object.entries(d.basis_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Документ, страница, комментарий<input value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })} placeholder="Например: каталог завода, стр. 112" /></label>
        <label className="f">Серийные номера с<input value={f.serial_from} onChange={(e) => setF({ ...f, serial_from: e.target.value })} placeholder="пусто — без ограничения" /></label>
        <label className="f">по<input value={f.serial_to} onChange={(e) => setF({ ...f, serial_to: e.target.value })} placeholder="пусто — без ограничения" /></label>
      </div>
      {need && <p className="muted">Для статуса «{d.statuses[f.status]}» нужно основание.</p>}
      <div className="row"><button className="btn primary" disabled={need} onClick={save}>Сохранить проверку</button><button className="btn ghost" onClick={onCancel}>отмена</button></div>
    </div>);
}
