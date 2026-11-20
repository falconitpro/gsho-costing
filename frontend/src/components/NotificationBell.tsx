import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";

// Уведомления «в системе»: значок с числом непрочитанных в шапке, список за 30 дней, отметка «прочитано».
// D-101: значок считает только то, что требует действия; «Для сведения» — отдельная вкладка без счётчика на значке.
// Уведомление о задаче становится прочитанным само, когда задача выполнена; уведомления этапа — когда сделка перешла дальше.

type Item = { id: string; at: string | null; event_title: string; text: string; deal_id: string | null; deal_number: string; url?: string | null; read: boolean; action?: boolean };
type Kind = "action" | "info";

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [kind, setKind] = useState<Kind>("action");
  const [info, setInfo] = useState(0);
  const nav = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  const poll = () => api.bg<{ unread: number; info?: number }>("/api/notifications/unread-count").then((r) => { setUnread(r.unread); setInfo(r.info || 0); }).catch(() => undefined);
  useEffect(() => { poll(); const t = setInterval(poll, 60000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!open) return;
    api.get<{ unread: number; info_unread?: number; items: Item[] }>(`/api/notifications?kind=${kind}&limit=100`).then((r) => { setItems(r.items); setUnread(r.unread); setInfo(r.info_unread || 0); }).catch(() => undefined);
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, kind]);
  // «прочитать все» — только в открытой вкладке (ids видимых непрочитанных), чтобы не погасить заодно другую
  const read = (ids?: string[]) => {
    const list = ids || items.filter((x) => !x.read).map((x) => x.id);
    if (!list.length) return;
    api.post<{ unread: number; info?: number }>("/api/notifications/read", { ids: list }).then((r) => {
      setUnread(r.unread); setInfo(r.info || 0); setItems(items.map((x) => (list.includes(x.id) ? { ...x, read: true } : x))); }).catch(() => undefined);
  };
  const go = (x: Item) => { if (!x.read) read([x.id]); setOpen(false); if (x.deal_id) nav(`/deals/${x.deal_id}`); else if (x.url) nav(x.url); };
  const when = (iso: string | null) => (iso ? `${fmtDate(iso)} ${iso.slice(11, 16)}` : "");
  return (
    <div ref={box} style={{ position: "relative" }}>
      <button className="hm-trigger" onClick={() => setOpen(!open)} title="Уведомления" aria-label={`Уведомления, непрочитанных: ${unread}`}
        style={{ fontSize: 16, lineHeight: 1 }}>
        🔔{unread > 0 && <span style={{ position: "absolute", top: -4, right: -2, minWidth: 18, height: 18, padding: "0 5px", borderRadius: 9, background: "var(--accent)", color: "#fff",
          fontSize: 11, fontWeight: 700, lineHeight: "18px", textAlign: "center" }}>{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div style={{ position: "absolute", right: 0, top: "110%", width: 380, maxWidth: "92vw", maxHeight: "70vh", overflowY: "auto", background: "#fff", border: "1px solid var(--line)",
          borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,.12)", zIndex: 20 }}>
          <div className="row" style={{ justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
            <b>Уведомления</b>{items.some((x) => !x.read) && <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); read(); }}>прочитать все</a>}
          </div>
          <div className="seg" role="tablist" aria-label="Вид уведомлений" style={{ margin: "8px 12px" }}>
            <button type="button" role="tab" aria-selected={kind === "action"} className={kind === "action" ? "on" : ""} onClick={() => setKind("action")}>Требуют действия{unread ? ` · ${unread}` : ""}</button>
            <button type="button" role="tab" aria-selected={kind === "info"} className={kind === "info" ? "on" : ""} onClick={() => setKind("info")}>Для сведения{info ? ` · ${info}` : ""}</button>
          </div>
          {items.length === 0 && <div className="muted" style={{ padding: 12 }}>{kind === "action" ? "Нет уведомлений, которые ждут действия. Задачи — в «Мои задачи»." : "За 30 дней сведений нет."}</div>}
          {items.map((x) => (
            <div key={x.id} onClick={() => go(x)} style={{ padding: "8px 12px", borderBottom: "1px solid #f0f0f0", cursor: x.deal_id || x.url ? "pointer" : "default",
              background: x.read ? "#fff" : "var(--accent-soft)" }}>
              <div style={{ fontSize: 11 }} className="muted">{when(x.at)} · {x.event_title}{x.deal_number && ` · ${x.deal_number}`}</div>
              <div style={{ fontSize: 13, fontWeight: x.read ? 400 : 600 }}>{x.text}</div>
            </div>))}
        </div>)}
    </div>
  );
}
