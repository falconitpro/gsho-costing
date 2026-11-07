import { useState } from "react";
import { api, fmtDate } from "../api";
import type { Deal, Me } from "../types";

// Ожидание результата после отправки КП: когда уточняли статус, что сказал клиент, когда ждём решение; напоминание — задача менеджеру.

export default function WaitingPanel({ deal, me, onDone }: { deal: Deal; me: Me; onDone: (msg: string) => void }) {
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const can = has("cd", "gd") || deal.manager_id === me.id;
  const [note, setNote] = useState("");
  const [on, setOn] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (deal.status !== "kp_sent") return null;
  const exp = deal.decision_expected_on;
  const days = exp ? Math.round((new Date(exp).getTime() - new Date(new Date().toISOString().slice(0, 10)).getTime()) / 86400000) : null;
  async function check() {
    setBusy(true);
    try { await api.post(`/api/deals/${deal.id}/status-check`, { note, expected_on: on || null }); setNote(""); setOn(""); setErr(""); onDone("Статус уточнён — следующее напоминание поставлено"); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="card" style={{ borderLeft: "4px solid var(--ink)" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>Ожидание результата.</b>{" "}
          {exp ? <>Решение клиента ждём до <b>{fmtDate(exp)}</b>{days != null && <span style={{ color: days < 0 ? "var(--accent)" : "var(--muted)" }}> ({days < 0 ? `срок прошёл ${-days} дн. назад` : days === 0 ? "сегодня" : `через ${days} дн.`})</span>}.</> : "Дата ожидаемого решения не задана."}
          {deal.status_checked_on && <span className="muted"> Последний раз уточняли {fmtDate(deal.status_checked_on)}{deal.status_check_note ? `: «${deal.status_check_note}»` : ""}.</span>}
        </div>
        <span className="muted" style={{ fontSize: 12 }}>напоминание уточнить статус приходит менеджеру задачей и уведомлением</span>
      </div>
      {can && (
        <div className="row" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }}>
          <input placeholder="что сказал клиент (необязательно)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: "1 1 260px" }} />
          <label className="row" style={{ gap: 6, fontSize: 12 }}>ждём решение до <input type="date" value={on} onChange={(e) => setOn(e.target.value)} /></label>
          <button className="btn" disabled={busy} onClick={check}>Уточнил статус</button>
          <span className="muted" style={{ fontSize: 12 }}>без даты — напомнить через 5 рабочих дней</span>
        </div>)}
      {err && <div className="alert error" style={{ marginTop: 6 }}>{err}</div>}
    </div>
  );
}
