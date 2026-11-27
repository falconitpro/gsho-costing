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
  const [mode, setMode] = useState<"appl" | "cross">("appl");
  return (
    <div>
      <div className="dc-groups aq-mode" role="tablist" aria-label="Что проверяем">
        <button type="button" role="tab" aria-selected={mode === "appl"} className={mode === "appl" ? "on" : ""} onClick={() => setMode("appl")}>Деталь — модель</button>
        <button type="button" role="tab" aria-selected={mode === "cross"} className={mode === "cross" ? "on" : ""} onClick={() => setMode("cross")}>Номер завода машины ↔ OEM</button>
      </div>
      {mode === "appl" ? <ApplQueue /> : <CrossQueue />}
    </div>);
}

function ApplQueue() {
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


/* D-202: кроссировки «номер завода машины ↔ OEM» — очередь проверки и загрузка таблицы */
type CRow = { id: string; part_id: string; part_article: string; part_name: string; kind: string; kind_title: string; number: string; manufacturer: string;
  status: string; status_title: string; basis_kind: string; basis: string; source: string; source_ref: string; verified_by_name: string; verified_at: string | null; other_numbers: string[] };
type CData = { items: CRow[]; counts: Record<string, number>; statuses: Record<string, string>; basis_kinds: Record<string, string>; need_basis: string[]; can_edit: boolean; can_import: boolean };
type ImportRep = { rows: number; exists: number; added: number; created: number; conflicts: { row: number; machine_number: string; oem_number: string; reason: string }[];
  skipped: { row: number; machine_number: string; oem_number: string; reason: string }[]; lines: { row: number; action: string }[]; dry_run: boolean };

function CrossQueue() {
  const [status, setStatus] = useState("unverified");
  const [q, setQ] = useState("");
  const [d, setD] = useState<CData | null>(null);
  const [ed, setEd] = useState<CRow | null>(null);
  const [f, setF] = useState({ status: "confirmed", basis_kind: "", basis: "" });
  const [msg, setMsg] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [create, setCreate] = useState(false);
  const [rep, setRep] = useState<ImportRep | null>(null);
  const load = () => api.get<CData>(`/api/parts/cross-refs?limit=200&status=${status}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`).then(setD).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [status]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return msg ? <ErrorState hint={msg} onRetry={load} /> : <Loading />;
  const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
  const run = async (dry: boolean) => {
    if (!file) return;
    try { const r = await api.upload<ImportRep>("/api/parts/cross-refs/import", file, { dry_run: String(dry), create: String(create) }); setRep(r); if (!dry) { setFile(null); load(); } }
    catch (e) { setMsg((e as Error).message); }
  };
  const need = d.need_basis.includes(f.status) && !f.basis.trim() && !f.basis_kind;
  return (
    <div className="aq">
      <p className="muted">Номера одной детали — на одной карточке: номер завода-изготовителя машины, OEM-номер производителя детали, наш артикул, аналоги. Здесь — проверка связей «номер завода ↔ OEM» и загрузка таблицы кроссировок. Позже такие связи будет предлагать ИИ при загрузке каталогов завода; подтверждает всегда человек.</p>
      {d.can_import && <div className="card sv-form">
        <h3>Загрузить таблицу кроссировок (Excel)</h3>
        <p className="pc-sub">Колонки: «Номер завода машины», «OEM номер»; по желанию — «Производитель машины», «Производитель детали», «Наименование». Сначала — пробный прогон.</p>
        <div className="row aq-imp">
          <input type="file" accept=".xlsx" aria-label="Файл кроссировок" onChange={(e) => { setFile(e.target.files?.[0] || null); setRep(null); }} />
          <label className="row"><input type="checkbox" checked={create} onChange={(e) => setCreate(e.target.checked)} /> заводить карточки для ненайденных номеров</label>
          <button className="btn" disabled={!file} onClick={() => run(true)}>Пробный прогон</button>
          {rep?.dry_run && <button className="btn primary" disabled={!file} onClick={() => run(false)}>Загрузить</button>}
        </div>
        {rep && <div className="aq-rep">
          <b>{rep.dry_run ? "Пробный прогон" : "Загружено"}:</b> строк {rep.rows} · уже есть {rep.exists} · {rep.dry_run ? "добавится" : "добавлено"} номеров {rep.added} · новых карточек {rep.created}
          {rep.conflicts.length > 0 && <div className="sv-req">Конфликты ({rep.conflicts.length}) — номера на разных карточках, не меняются: {rep.conflicts.slice(0, 10).map((c) => `стр. ${c.row} (${c.machine_number} ↔ ${c.oem_number})`).join("; ")}</div>}
          {rep.skipped.length > 0 && <div className="muted">Пропущено ({rep.skipped.length}): {rep.skipped.slice(0, 10).map((c) => `стр. ${c.row} — ${c.reason}`).join("; ")}</div>}
          {rep.lines.length > 0 && <ul className="pc-sub">{rep.lines.slice(0, 15).map((l) => <li key={l.row}>стр. {l.row}: {l.action}</li>)}</ul>}
        </div>}
      </div>}
      <div className="pc-toolbar sv-toolbar">
        <div className="pc-groups" role="group" aria-label="Статус номера">
          {Object.entries(d.statuses).map(([k, t]) => <button key={k} type="button" className={status === k ? "on" : ""} aria-pressed={status === k} onClick={() => setStatus(k)}>{t} · {d.counts[k] || 0}</button>)}
          <button type="button" className={status === "" ? "on" : ""} aria-pressed={status === ""} onClick={() => setStatus("")}>Все · {total}</button>
        </div>
        <form className="row sv-search" onSubmit={(e) => { e.preventDefault(); load(); }}>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Номер или артикул" aria-label="Поиск номера" /><button className="btn ghost">Найти</button></form>
      </div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {ed && <div className="card sv-form">
        <h3>{ed.kind_title}: {ed.number} · карточка {ed.part_article}</h3>
        <div className="grid cols-3">
          <label className="f">Статус<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{Object.entries(d.statuses).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
          <label className="f">Основание{d.need_basis.includes(f.status) ? " *" : ""}<select value={f.basis_kind} onChange={(e) => setF({ ...f, basis_kind: e.target.value })}>
            <option value="">— выберите —</option>{Object.entries(d.basis_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
          <label className="f">Документ, комментарий<input value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })} /></label>
        </div>
        <div className="row"><button className="btn primary" disabled={need} onClick={async () => {
          try { await api.post(`/api/parts/cross-refs/${ed.id}/verify`, f); setEd(null); setMsg(`${ed.number}: ${d.statuses[f.status]}`); load(); } catch (e) { setMsg((e as Error).message); }
        }}>Сохранить проверку</button><button className="btn ghost" onClick={() => setEd(null)}>отмена</button></div>
      </div>}
      {d.items.length === 0 ? <Empty title="Номеров с таким статусом нет" /> : (
        <table className="pc-table nv-cards sv-table">
          <thead><tr><th>Номер</th><th>Карточка и другие номера</th><th>Источник</th><th>Статус</th><th /></tr></thead>
          <tbody>{d.items.map((r) => (
            <tr key={r.id}>
              <td><b className="pc-mono">{r.number}</b><div className="pc-sub">{r.kind_title}{r.manufacturer ? ` · ${r.manufacturer}` : ""}</div></td>
              <td data-label="Карточка"><Link to={`/catalog?s=parts&part=${r.part_id}`}>{r.part_article}</Link> {r.part_name}<div className="pc-sub">{r.other_numbers.join(" · ")}</div></td>
              <td data-label="Источник">{{ manual: "вручную", import: "импорт", ai: "ИИ" }[r.source] || r.source}{r.source_ref && <div className="pc-sub">{r.source_ref}</div>}</td>
              <td data-label="Статус"><span className={`badge ${APPL_TONE[r.status] || ""}`}>{r.status_title}</span>
                {(r.basis || r.basis_kind) && <div className="pc-sub">{[d.basis_kinds[r.basis_kind], r.basis].filter(Boolean).join(": ")}</div>}
                {r.verified_by_name && <div className="pc-sub">{r.verified_by_name} · {fmtDate(r.verified_at)}</div>}</td>
              <td>{d.can_edit && <button className="btn ghost" onClick={() => { setEd(r); setF({ status: r.status === "unverified" ? "confirmed" : r.status, basis_kind: r.basis_kind, basis: r.basis }); }}>Проверить</button>}</td>
            </tr>))}</tbody>
        </table>)}
    </div>);
}
