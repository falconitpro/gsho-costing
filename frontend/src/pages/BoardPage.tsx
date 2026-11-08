import { useEffect, useState } from "react";
import { toneOf } from "../colors";
import { lbl, PRIORITY, ROLE } from "../labels";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";

type Col = { title: string; kind?: string; deals: { id: string; number: string; client: string; priority: string; overdue: boolean; status_detail?: string; tasks: { title: string; role: string; due: string; overdue: boolean }[];
  group?: string; group_number?: string; related?: number; url?: string }[] };
const PARTS_TONE: Record<string, string> = { parts_request: "draft", parts_calc: "cd_approval", parts_kp: "kp_sent", parts_execution: "execution", parts_closed: "closed" };

export default function BoardPage() {
  const [cols, setCols] = useState<Record<string, Col>>({});
  useEffect(() => { api.get<Record<string, Col>>("/api/board").then(setCols); }, []);
  const gsho = ["draft", "logistics_calc", "service_calc", "calc_ready", "cd_approval", "fd_visa", "gd_visa", "rework", "approved", "kp_sent", "won", "contract", "execution", "closed", "lost", "cancelled"];
  const partsCols = ["parts_request", "parts_calc", "parts_kp", "parts_execution"];
  const [type, setType] = useState<"" | "gsho" | "parts">(() => { try { return (localStorage.getItem("gsho.boardType") as "gsho") || ""; } catch { return ""; } });
  const pick = (t: "" | "gsho" | "parts") => { setType(t); try { localStorage.setItem("gsho.boardType", t); } catch { /* нет хранилища */ } };
  const order = type === "gsho" ? gsho : type === "parts" ? partsCols : [...gsho, ...partsCols];
  const nParts = partsCols.reduce((n, k) => n + (cols[k]?.deals.length || 0), 0);
  const nGsho = gsho.reduce((n, k) => n + (cols[k]?.deals.length || 0), 0);
  return (
    <>
      <div className="page-head"><h1>Доска сделок</h1>
        <div className="tabs" style={{ margin: 0 }}>{([["", "Все"], ["gsho", `ГШО · ${nGsho}`], ["parts", `Запчасти · ${nParts}`]] as const).map(([k, t]) => <button key={k} className={type === k ? "active" : ""} onClick={() => pick(k)}>{t}</button>)}</div>
      </div>
      <div className="board">
        {order.filter((k) => cols[k] && (type !== "" || !k.startsWith("parts_") || cols[k].deals.length > 0)).map((k) => (
          <div className="col" key={k} style={{ background: toneOf(PARTS_TONE[k] || k)[0] }}>
            <h3 style={{ color: toneOf(PARTS_TONE[k] || k)[1] }}>{cols[k].title} <span className="muted">({cols[k].deals.length})</span></h3>
            {cols[k].deals.map((d) => (
              <div key={d.id} className={"deal" + (d.overdue ? " overdue" : "")}>
                <Link to={d.url || `/deals/${d.id}`}><b>{d.number}</b></Link> <span className="badge">{lbl(PRIORITY, d.priority)}</span>{d.status_detail && d.status_detail.includes("·") && <div className="muted" style={{ fontSize: 12 }}>{d.status_detail.split("·")[1].trim()}</div>}
                {(d.related || 0) > 0 && <span className="badge" title={`одна группа заявок по запросу клиента ${d.group_number}`} style={{ marginLeft: 4 }}>запрос {d.group_number}: +{d.related}</span>}
                <div className="muted">{d.client}</div>
                {d.tasks.map((t, i) => <div key={i} style={{ fontSize: 12 }}>• {t.title} — {lbl(ROLE, t.role)}, до {fmtDate(t.due)}{t.overdue ? " ⚠" : ""}</div>)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
