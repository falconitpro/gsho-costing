import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import { STATUS_TITLES } from "../types";

// Сделки коллег для менеджера — минимальный объём, только чтение: чтобы не дублировать работу по клиенту и знать, к кому обратиться.

type Row = { id: string; number: string; client_id: string; client_name: string; site: string; equipment: string; status: string; status_detail: string; manager_name: string; kp_due: string | null };

export default function ColleaguesDeals() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  useEffect(() => { api.get<Row[]>("/api/deals/colleagues").then(setRows).catch(() => setRows([])); }, []);
  if (!rows || rows.length === 0) return null;
  const shown = rows.filter((r) => !q || [r.number, r.client_name, r.site, r.equipment, r.manager_name].some((x) => (x || "").toLowerCase().includes(q.toLowerCase())));
  return (
    <div className="card" style={{ padding: "10px 12px", marginTop: 10 }}>
      <div className="row" style={{ justifyContent: "space-between", cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <span><b>Сделки коллег</b> <span className="muted">· {rows.length} в работе · только для сведения, без цен и файлов</span></span>
        <button className="btn ghost" style={{ padding: "1px 8px", fontSize: 12 }}>{open ? "Свернуть ▴" : "Показать ▾"}</button>
      </div>
      {open && (
        <>
          <input placeholder="🔍 клиент, объект, оборудование, менеджер" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginTop: 8, width: 360, maxWidth: "100%" }} />
          <div style={{ overflowX: "auto", marginTop: 6 }}>
            <table style={{ fontSize: 13 }}>
              <thead><tr><th>Сделка</th><th>Клиент / объект</th><th>Оборудование</th><th>Статус</th><th>Менеджер</th><th>Срок КП</th></tr></thead>
              <tbody>{shown.map((r) => (
                <tr key={r.id}><td style={{ whiteSpace: "nowrap" }}>{r.number}</td><td><b>{r.client_name}</b>{r.site && <div className="muted" style={{ fontSize: 12 }}>{r.site}</div>}</td>
                  <td>{r.equipment || <span className="muted">—</span>}</td><td>{r.status_detail || STATUS_TITLES[r.status] || r.status}</td><td style={{ whiteSpace: "nowrap" }}>{r.manager_name}</td><td>{r.kp_due ? fmtDate(r.kp_due) : "—"}</td></tr>))}</tbody>
            </table>
          </div>
        </>)}
    </div>
  );
}
