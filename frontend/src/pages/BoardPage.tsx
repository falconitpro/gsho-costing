import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";

type Col = { title: string; deals: { id: string; number: string; client: string; site: string; priority: string; overdue: boolean; tasks: { title: string; role: string; due: string; overdue: boolean }[] }[] };

export default function BoardPage() {
  const [cols, setCols] = useState<Record<string, Col>>({});
  useEffect(() => { api.get<Record<string, Col>>("/api/board").then(setCols); }, []);
  const order = ["draft", "logistics_calc", "service_calc", "calc_ready", "cd_approval", "fd_visa", "gd_visa", "rework", "approved", "kp_sent", "won", "lost", "cancelled"];
  return (
    <>
      <h1>Доска сделок</h1>
      <div className="board">
        {order.filter((k) => cols[k]).map((k) => (
          <div className="col" key={k}>
            <h3>{cols[k].title} <span className="muted">({cols[k].deals.length})</span></h3>
            {cols[k].deals.map((d) => (
              <div key={d.id} className={"deal" + (d.overdue ? " overdue" : "")}>
                <Link to={`/deals/${d.id}`}><b>{d.number}</b></Link> <span className="badge">{d.priority}</span>
                <div className="muted">{d.client} · {d.site}</div>
                {d.tasks.map((t, i) => <div key={i} style={{ fontSize: 12 }}>• {t.title} — {t.role}, до {fmtDate(t.due)}{t.overdue ? " ⚠" : ""}</div>)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
