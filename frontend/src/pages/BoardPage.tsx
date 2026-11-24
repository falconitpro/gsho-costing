import { Crumbs } from "../components/Levels";
import { useEffect, useState } from "react";
import { toneOf } from "../colors";
import { lbl, PRIORITY, ROLE } from "../labels";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useNewUiFlag } from "../uiMode";

type Col = { title: string; kind?: string; deals: { id: string; number: string; client: string; priority: string; overdue: boolean; status_detail?: string; tasks: { title: string; role: string; due: string; overdue: boolean }[];
  group?: string; group_number?: string; related?: number; url?: string }[] };
const PARTS_TONE: Record<string, string> = { parts_request: "draft", parts_calc: "cd_approval", parts_kp: "kp_sent", parts_execution: "execution", parts_closed: "closed" };

export default function BoardPage() {
  const [cols, setCols] = useState<Record<string, Col>>({});
  const [err, setErr] = useState("");
  const nv = useNewUiFlag();
  const [stage, setStage] = useState("");
  // прямой адрес /board у роли без доступа: вместо необработанной ошибки — пояснение и переход к реестру (права не меняются)
  useEffect(() => { api.get<Record<string, Col>>("/api/board").then(setCols).catch((e) => setErr((e as Error).message || "Нет доступа")); }, []);
  const gsho = ["draft", "logistics_calc", "service_calc", "calc_ready", "cd_approval", "fd_visa", "gd_visa", "rework", "approved", "kp_sent", "won", "contract", "execution", "closed", "lost", "cancelled"];
  const partsCols = ["parts_request", "parts_calc", "parts_kp", "parts_execution"];
  const [type, setType] = useState<"" | "gsho" | "parts">(() => { try { return (localStorage.getItem("gsho.boardType") as "gsho") || ""; } catch { return ""; } });
  const pick = (t: "" | "gsho" | "parts") => { setType(t); try { localStorage.setItem("gsho.boardType", t); } catch { /* нет хранилища */ } };
  const order = type === "gsho" ? gsho : type === "parts" ? partsCols : [...gsho, ...partsCols];
  const nParts = partsCols.reduce((n, k) => n + (cols[k]?.deals.length || 0), 0);
  const nGsho = gsho.reduce((n, k) => n + (cols[k]?.deals.length || 0), 0);
  const shown = order.filter((k) => cols[k] && (type !== "" || !k.startsWith("parts_") || cols[k].deals.length > 0));
  // телефон в новом виде: одна колонка, этап выбирается списком; по умолчанию — первый этап со сделками
  const cur = shown.includes(stage) ? stage : (shown.find((k) => cols[k].deals.length > 0) || shown[0] || "");
  const colStyle = (k: string) => { const [bg, fg] = toneOf(PARTS_TONE[k] || k); return nv ? { borderTopColor: fg } : { background: bg }; };
  if (err) return (
    <>
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Доска сделок" }]} />
      <div className="page-head"><h1>Доска сделок</h1></div>
      <div className="card">
        <p style={{ marginTop: 0 }}>Доска сделок доступна КД, ФД, ЗГД и наблюдателю. Ваши сделки и заявки — в реестре.</p>
        <div className="row" style={{ gap: 8 }}><Link className="btn" to="/deals">Открыть реестр сделок</Link><Link className="btn ghost" to="/">На рабочее место</Link></div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Ответ сервера: {err}</p>
      </div>
    </>);
  return (
    <>
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Доска сделок" }]} />
      <div className="page-head"><h1>Доска сделок</h1>
        <div className="tabs board-type" style={{ margin: 0 }}>{([["", "Все"], ["gsho", `ГШО · ${nGsho}`], ["parts", `Запчасти · ${nParts}`]] as const).map(([k, t]) => <button key={k} className={type === k ? "active" : ""} onClick={() => pick(k)}>{t}</button>)}</div>
      </div>
      {nv && (() => {
        const total = shown.reduce((n, k) => n + cols[k].deals.length, 0);
        const late = shown.reduce((n, k) => n + cols[k].deals.filter((d) => d.overdue).length, 0);
        return (
          <div className="board-meta">
            <span>Всего сделок: <b>{total}</b>{late > 0 && <> · с просроченными задачами: <b className="bad">{late}</b></>}</span>
            <span className="muted">Красная полоса слева — есть просроченная задача. Этап меняется в карточке сделки, перетаскивания нет.</span>
            <label className="board-stage">Этап
              <select value={cur} onChange={(e) => setStage(e.target.value)}>
                {shown.map((k) => <option key={k} value={k}>{cols[k].title} · {cols[k].deals.length}</option>)}
              </select></label>
          </div>);
      })()}
      <div className={"board" + (nv ? " nv-board" : "")}>
        {shown.map((k) => (
          <div className={"col" + (k === cur ? " cur" : "")} key={k} style={colStyle(k)}>
            <h3 style={nv ? undefined : { color: toneOf(PARTS_TONE[k] || k)[1] }}>{cols[k].title} {nv ? <span className="board-n">{cols[k].deals.length}</span> : <span className="muted">({cols[k].deals.length})</span>}</h3>
            {nv && cols[k].deals.length === 0 && <div className="board-empty">Сделок нет</div>}
            {cols[k].deals.map((d) => (
              <div key={d.id} className={"deal" + (d.overdue ? " overdue" : "")}>
                <Link to={d.url || `/deals/${d.id}`}><b>{d.number}</b></Link> {(!nv || d.priority !== "normal") && <span className={"badge" + (nv && d.priority !== "normal" ? " warn" : "")}>{lbl(PRIORITY, d.priority)}</span>}
                {nv && d.overdue && <span className="badge bad">просрочено</span>}
                {d.status_detail && d.status_detail.includes("·") && <div className="muted" style={{ fontSize: 12 }}>{d.status_detail.split("·")[1].trim()}</div>}
                {(d.related || 0) > 0 && <span className="badge" title={`одна группа заявок по запросу клиента ${d.group_number}`} style={{ marginLeft: 4 }}>запрос {d.group_number}: +{d.related}</span>}
                <div className="muted">{d.client}</div>
                {(nv ? d.tasks.slice(0, 2) : d.tasks).map((t, i) => <div key={i} className={nv ? "board-task" + (t.overdue ? " bad" : "") : undefined} style={nv ? undefined : { fontSize: 12 }}>• {t.title} — {lbl(ROLE, t.role)}, до {fmtDate(t.due)}{t.overdue ? (nv ? " · просрочено" : " ⚠") : ""}</div>)}
                {nv && d.tasks.length > 2 && <Link className="board-more" to={d.url || `/deals/${d.id}`}>ещё {d.tasks.length - 2} {d.tasks.length - 2 === 1 ? "задача" : d.tasks.length - 2 < 5 ? "задачи" : "задач"} →</Link>}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
