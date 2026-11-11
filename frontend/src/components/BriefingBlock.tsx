import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import SecurityModal from "./SecurityModal";

/** «Ваши приоритеты» (docs/operations/07): 3–5 дел из фактических данных CRM — что сделать, почему сейчас, следующий шаг.
 *  Закрепить / отложить / скрыть действуют только для текущей версии источника; срок самого дела не меняется. */
export type BriefingItem = { item_key: string; source_type: string; source_signature: string; severity: string; severity_title: string; title: string;
  reason: string; advice_text: string; due_date: string | null; due_label: string; source_label: string; url: string; amount: string | null;
  currency: string | null; deputy: boolean; pinned: boolean };
export type Briefing = { generated_at: string; items: BriefingItem[]; more?: number; more_url?: string | null; snoozed?: number; hidden?: number;
  warnings: string[]; empty_text?: string; error?: boolean };

const SEV: Record<string, string> = { critical: "bad", high: "warn", medium: "info", info: "" };
const at = (iso: string) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function BriefingBlock({ initial }: { initial: Briefing }) {
  const nav = useNavigate();
  const [b, setB] = useState<Briefing>(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [states, setStates] = useState<{ item_key: string; title: string; hidden_at: string | null; snoozed_until: string | null }[] | null>(null);
  useEffect(() => { setB(initial); }, [initial]);
  const refresh = () => { setBusy(true); setErr(""); return api.get<Briefing>("/api/briefing").then(setB).catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось обновить")).finally(() => setBusy(false)); };
  const act = async (it: BriefingItem, kind: "pin" | "unpin" | "hide" | "snooze", days = 1) => {
    setMenu(null); setErr("");
    const key = encodeURIComponent(it.item_key).replace(/%3A/g, ":");
    try {
      if (kind === "unpin") await api.del(`/api/briefing/items/${key}/pin`);
      else if (kind === "snooze") await api.post(`/api/briefing/items/${key}/snooze`, { source_signature: it.source_signature, until: isoDay(new Date(Date.now() + days * 864e5)) });
      else await api.post(`/api/briefing/items/${key}/${kind}`, { source_signature: it.source_signature });
      await refresh();
    } catch (e) { setErr(e instanceof ApiError ? e.message : "Не удалось сохранить"); }
  };
  const openStates = () => api.get<{ items: typeof states }>("/api/briefing/states").then((r) => setStates(r.items || [])).catch(() => setStates([]));
  const reset = (keys?: string[]) => api.post("/api/briefing/items/reset", { item_keys: keys ?? null }).then(() => { setStates(null); refresh(); });
  const parked = (b.snoozed || 0) + (b.hidden || 0);
  return (
    <div className="wp-card brf">
      <div className="wp-head"><h2>Ваши приоритеты</h2>
        <button className="btn ghost" onClick={refresh} disabled={busy} title="Пересчитать по текущим данным">{busy ? "…" : "Обновить"}</button></div>
      <div className="muted" style={{ fontSize: 12, marginTop: -4 }}>Сформировано {at(b.generated_at)} · из задач, согласований, сроков и обращений CRM</div>
      {b.warnings?.map((w) => <div key={w} className="alert warn" style={{ margin: "6px 0", fontSize: 13 }}>{w}</div>)}
      {err && <div className="alert error" role="alert">{err}</div>}
      {!b.error && b.items.length === 0 && <p className="muted" style={{ margin: "8px 0" }}>{b.empty_text || "Срочных дел по вашим задачам и срокам нет"}</p>}
      <ol className="brf-list">{b.items.map((it) => (
        <li key={it.item_key} className={`brf-item ${SEV[it.severity]}`}>
          <div className="brf-top">
            <span className={`badge ${SEV[it.severity]}`}>{it.severity_title}</span>
            <span className="muted" style={{ fontSize: 12 }}>{it.source_label}{it.deputy ? " · по замещению" : ""}{it.pinned ? " · закреплено" : ""}</span>
            <button className="btn ghost brf-more" aria-label={`Действия: ${it.title}`} aria-expanded={menu === it.item_key} onClick={() => setMenu(menu === it.item_key ? null : it.item_key)}>⋯</button>
          </div>
          <button className="brf-title" onClick={() => nav(it.url)}>{it.title}</button>
          <div className="brf-reason">{it.reason}</div>
          <div className="brf-advice">→ {it.advice_text}</div>
          {menu === it.item_key && <div className="brf-menu" role="menu">
            <button role="menuitem" className="btn ghost" onClick={() => act(it, it.pinned ? "unpin" : "pin")}>{it.pinned ? "Открепить" : "Закрепить"}</button>
            <button role="menuitem" className="btn ghost" onClick={() => act(it, "snooze", 1)}>Отложить до завтра</button>
            <button role="menuitem" className="btn ghost" onClick={() => act(it, "snooze", 7)}>Отложить на неделю</button>
            <button role="menuitem" className="btn ghost" onClick={() => act(it, "hide")}>Скрыть, пока не изменится</button>
          </div>}
        </li>))}</ol>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6, fontSize: 13 }}>
        {b.more ? <a href={b.more_url || "/tasks"} onClick={(e) => { e.preventDefault(); nav(b.more_url || "/tasks"); }}>Ещё {b.more} требуют внимания →</a> : <span />}
        {parked > 0 && <button className="btn ghost" onClick={openStates}>Отложено и скрыто: {parked}</button>}
      </div>
      {states && <SecurityModal title="Отложенные и скрытые" onClose={() => setStates(null)} width={520}>
        {states.length === 0 ? <p className="muted">Ничего не отложено и не скрыто.</p> : <ul className="brf-states">{states.map((s) => (
          <li key={s.item_key}><span>{s.title}<small className="muted"> · {s.hidden_at ? "скрыто" : `отложено до ${new Date(s.snoozed_until!).toLocaleDateString("ru-RU")}`}</small></span>
            <button className="btn ghost" onClick={() => reset([s.item_key])}>Вернуть</button></li>))}</ul>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setStates(null)}>Закрыть</button>
          {states.length > 0 && <button className="btn" onClick={() => reset()}>Вернуть все</button>}</div>
      </SecurityModal>}
    </div>
  );
}
