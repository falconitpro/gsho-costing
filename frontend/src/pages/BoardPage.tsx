import { useEffect, useState } from "react";
import { lbl, PRIORITY, ROLE } from "../labels";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";

type Col = { title: string; deals: { id: string; number: string; client: string; site: string; priority: string; overdue: boolean; status_detail?: string; tasks: { title: string; role: string; due: string; overdue: boolean }[];
  group?: string; group_number?: string; related?: number }[] };

export default function BoardPage() {
  const [cols, setCols] = useState<Record<string, Col>>({});
  useEffect(() => { api.get<Record<string, Col>>("/api/board").then(setCols); }, []);
  const order = ["draft", "logistics_calc", "service_calc", "calc_ready", "cd_approval", "fd_visa", "gd_visa", "rework", "approved", "kp_sent", "won", "contract", "execution", "closed", "lost", "cancelled"];
  return (
    <>
      <h1>Доска сделок</h1>
      <div className="board">
        {order.filter((k) => cols[k]).map((k) => (
          <div className="col" key={k}>
            <h3>{cols[k].title} <span className="muted">({cols[k].deals.length})</span></h3>
            {cols[k].deals.map((d) => (
              <div key={d.id} className={"deal" + (d.overdue ? " overdue" : "")}>
                <Link to={`/deals/${d.id}`}><b>{d.number}</b></Link> <span className="badge">{lbl(PRIORITY, d.priority)}</span>{d.status_detail && d.status_detail.includes("·") && <div className="muted" style={{ fontSize: 12 }}>{d.status_detail.split("·")[1].trim()}</div>}
                {(d.related || 0) > 0 && <span className="badge" title={`одна группа заявок по запросу клиента ${d.group_number}`} style={{ marginLeft: 4 }}>запрос {d.group_number}: +{d.related}</span>}
                <div className="muted">{d.client} · {d.site}</div>
                {d.tasks.map((t, i) => <div key={i} style={{ fontSize: 12 }}>• {t.title} — {lbl(ROLE, t.role)}, до {fmtDate(t.due)}{t.overdue ? " ⚠" : ""}</div>)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
