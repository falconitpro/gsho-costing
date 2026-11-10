import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";

// Уведомления «в системе»: значок с числом непрочитанных в шапке, список за 30 дней, отметка «прочитано».

type Item = { id: string; at: string | null; event_title: string; text: string; deal_id: string | null; deal_number: string; url?: string | null; read: boolean };

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const nav = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  const poll = () => api.get<{ unread: number }>("/api/notifications/unread-count").then((r) => setUnread(r.unread)).catch(() => undefined);
  useEffect(() => { poll(); const t = setInterval(poll, 60000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (!open) return;
    api.get<{ unread: number; items: Item[] }>("/api/notifications").then((r) => { setItems(r.items); setUnread(r.unread); }).catch(() => undefined);
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const read = (ids?: string[]) => api.post<{ unread: number }>("/api/notifications/read", { ids: ids || null }).then((r) => {
    setUnread(r.unread); setItems(items.map((x) => (!ids || ids.includes(x.id) ? { ...x, read: true } : x))); }).catch(() => undefined);
  const go = (x: Item) => { if (!x.read) read([x.id]); setOpen(false); if (x.deal_id) nav(`/deals/${x.deal_id}`); else if (x.url) nav(x.url); };
  const when = (iso: string | null) => (iso ? `${fmtDate(iso)} ${iso.slice(11, 16)}` : "");
  return (
    <div ref={box} style={{ position: "relative" }}>
      <button className="btn ghost" onClick={() => setOpen(!open)} title="Уведомления" aria-label={`Уведомления, непрочитанных: ${unread}`}
        style={{ position: "relative", padding: "4px 8px", fontSize: 18, lineHeight: 1 }}>
        🔔{unread > 0 && <span style={{ position: "absolute", top: -4, right: -2, minWidth: 18, height: 18, padding: "0 5px", borderRadius: 9, background: "var(--accent)", color: "#fff",
          fontSize: 11, fontWeight: 700, lineHeight: "18px", textAlign: "center" }}>{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div style={{ position: "absolute", right: 0, top: "110%", width: 380, maxWidth: "92vw", maxHeight: "70vh", overflowY: "auto", background: "#fff", border: "1px solid var(--line)",
          borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,.12)", zIndex: 20 }}>
          <div className="row" style={{ justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--line)" }}>
            <b>Уведомления</b>{unread > 0 && <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); read(); }}>прочитать все</a>}
          </div>
          {items.length === 0 && <div className="muted" style={{ padding: 12 }}>За 30 дней уведомлений нет.</div>}
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
