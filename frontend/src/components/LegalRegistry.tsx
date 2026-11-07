import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtRub } from "../api";
import { toneOf } from "../colors";
import { STATUS_TITLES } from "../types";

// Юридический блок, этап 5: реестр договоров и пакетов по всем доступным сделкам — фильтры, статусы, файлы, переход в сделку.

type Pkg = { id: string; deal_id: string; deal_number: string; deal_status: string; client_name: string; kind: string; counterparty_name: string; version_no: number; deal_version_no: number | null;
  status: string; status_title: string; language: string; files: { kind: string; title: string; file_name: string }[]; signed_scans: { file_name: string }[]; created_by_name: string; created_at: string;
  totals: { amount?: number | null; currency?: string }; contract: { number?: string }; superseded_by: string | null; approvals: { at: string; by_name: string; to: string; comment: string }[] };
type Resp = { items: Pkg[]; statuses: Record<string, string>; summary: Record<string, number>; kinds: Record<string, string> };
const KIND_SHORT: Record<string, string> = { purchase_contract: "Договор", spec_equipment: "Прил. 1", spec_zip: "Прил. 2", client_contract_copy: "Раб. копия", disagreements_protocol: "Протокол", tech_spec_client: "Техспец.", signed_scan: "Скан" };
const ST_TONE: Record<string, string> = { draft: "warn", needs_data: "warn", lawyer_review: "info", commercial_review: "info", approved: "ok", signed: "ok", superseded: "", archived: "" };

export default function LegalRegistry() {
  const [data, setData] = useState<Resp | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [showOld, setShowOld] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<Resp>("/api/legal/packages").then(setData).catch((e) => setMsg((e as Error).message)); }, []);
  if (!data) return <div className="card muted">{msg || "Загрузка…"}</div>;
  const rows = data.items.filter((p) => (showOld || !["superseded", "archived"].includes(p.status)) && (!status || p.status === status) && (!kind || p.kind === kind)
    && (!q || `${p.deal_number} ${p.client_name} ${p.counterparty_name} ${p.contract.number || ""}`.toLowerCase().includes(q.toLowerCase())));
  const kpis: [string, number][] = [["всего пакетов", data.items.length], ["черновики", (data.summary.draft || 0) + (data.summary.needs_data || 0)], ["на проверке юриста", data.summary.lawyer_review || 0],
    ["на коммерческом согласовании", data.summary.commercial_review || 0], ["утверждены", data.summary.approved || 0], ["подписаны", data.summary.signed || 0]];
  return (
    <div className="card">
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input placeholder="🔍 сделка, клиент, контрагент, номер договора" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: "1 1 260px" }} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">все типы</option>{Object.entries(data.kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">все статусы</option>{Object.entries(data.statuses).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        <label className="row muted" style={{ fontSize: 12, gap: 4 }}><input type="checkbox" checked={showOld} onChange={(e) => setShowOld(e.target.checked)} /> заменённые и архив</label>
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
        {kpis.map(([t, v]) => <div key={t} style={{ flex: "1 1 120px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}><div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{t}</div></div>)}
      </div>
      <div style={{ overflowX: "auto", marginTop: 10 }}>
        <table style={{ fontSize: 13 }}>
          <thead><tr><th>Сделка</th><th>Клиент</th><th>Тип · контрагент</th><th>Договор</th><th className="num">Сумма</th><th>Статус пакета</th><th>Документы</th><th>Автор · дата</th></tr></thead>
          <tbody>{rows.map((p) => (
            <tr key={p.id} style={{ cursor: "pointer", opacity: ["superseded", "archived"].includes(p.status) ? .55 : 1 }} onClick={() => setOpen(open === p.id ? null : p.id)}>
              <td style={{ whiteSpace: "nowrap" }}><Link to={`/deals/${p.deal_id}?tab=legal`} onClick={(e) => e.stopPropagation()}><b>{p.deal_number}</b></Link><div><span className="badge" style={{ background: toneOf(p.deal_status)[0], color: toneOf(p.deal_status)[1], fontSize: 11 }}>{STATUS_TITLES[p.deal_status] || p.deal_status}</span></div></td>
              <td>{p.client_name}</td>
              <td><span className="badge" style={{ fontSize: 11 }}>{data.kinds[p.kind] || p.kind}</span><div style={{ fontSize: 12 }}>{p.counterparty_name}</div></td>
              <td style={{ whiteSpace: "nowrap" }}>{p.contract.number ? `№ ${p.contract.number}` : <span className="muted">—</span>}<div className="muted" style={{ fontSize: 11 }}>пакет v{p.version_no} · сделка v{p.deal_version_no ?? "—"} · {p.language}</div></td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>{p.totals.amount != null ? `${fmtRub(p.totals.amount)} ${p.totals.currency || ""}` : <span className="muted">—</span>}</td>
              <td><span className={`badge ${ST_TONE[p.status] || ""}`}>{p.status_title}</span>{p.signed_scans.length > 0 && <div className="badge ok" style={{ fontSize: 10, marginTop: 2 }}>скан подписанного ✓</div>}</td>
              <td onClick={(e) => e.stopPropagation()}><div className="row" style={{ gap: 4, flexWrap: "wrap" }}>{p.files.map((f) => <a key={f.kind + f.file_name} className="btn ghost" style={{ padding: "1px 7px", fontSize: 11 }} href={`/api/legal/packages/${p.id}/files/${f.kind}`} title={f.file_name}>{KIND_SHORT[f.kind] || f.title}</a>)}
                {p.files.length > 0 && <a className="btn ghost" style={{ padding: "1px 7px", fontSize: 11 }} href={`/api/legal/packages/${p.id}/zip`}>ZIP</a>}</div></td>
              <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{p.created_by_name}<br />{fmtDate(p.created_at)}</td>
            </tr>))}
            {rows.length === 0 && <tr><td colSpan={8} className="muted">Пакетов нет. Они формируются в сделке на стадии контракта → вкладка «Юридические документы».</td></tr>}</tbody>
        </table>
      </div>
      {open && (() => { const p = data.items.find((x) => x.id === open); return p ? (
        <div style={{ marginTop: 8, fontSize: 12, border: "1px solid var(--line)", borderRadius: 8, padding: 10, background: "#fff" }}>
          <b>{p.deal_number} · {p.counterparty_name} · v{p.version_no}</b> — история согласования
          <table style={{ fontSize: 12, marginTop: 4 }}><tbody>{p.approvals.map((a, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{fmtDate(a.at)}</td><td>{a.by_name}</td><td>{data.statuses[a.to] || a.to}</td><td className="muted">{a.comment}</td></tr>)}</tbody></table>
          <Link to={`/deals/${p.deal_id}?tab=legal`}>Открыть в сделке →</Link>
        </div>) : null; })()}
    </div>
  );
}
