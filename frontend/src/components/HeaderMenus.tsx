import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";

/** Выпадающее меню шапки: закрывается по щелчку вне меню и по Esc. */
function Dropdown({ button, label, children, className = "" }: { button: ReactNode; label: string; children: (close: () => void) => ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  return (
    <div className={"hm " + className} ref={ref}>
      <button type="button" className="hm-trigger" aria-label={label} aria-haspopup="menu" aria-expanded={open} title={label} onClick={() => setOpen(!open)}>{button}</button>
      {open && <div className="hm-menu" role="menu">{children(() => setOpen(false))}</div>}
    </div>
  );
}

/** «?» в шапке: «Помощник» и «Поддержка» в одном месте; точка — есть новые ответы поддержки. */
export function HelpMenu({ canAssistant, onAssistant }: { canAssistant: boolean; onAssistant: () => void }) {
  const [n, setN] = useState(0);
  const nav = useNavigate();
  useEffect(() => {
    const poll = () => api.bg<{ unread: number }>("/api/support/meta").then((r) => setN(r.unread)).catch(() => undefined);
    poll(); const t = setInterval(poll, 60000);
    window.addEventListener("gsho:support-read", poll);
    return () => { clearInterval(t); window.removeEventListener("gsho:support-read", poll); };
  }, []);
  return (
    <Dropdown label={`Помощь${n ? `, новых ответов поддержки: ${n}` : ""}`} className="hm-help"
      button={<><span className="hm-q" aria-hidden>?</span>{n > 0 && <span className="hm-dot">{n > 99 ? "99+" : n}</span>}</>}>
      {(close) => <>
        {canAssistant && <button type="button" role="menuitem" onClick={() => { close(); onAssistant(); }}>Помощник<small>вопрос по CRM, поиск по сделкам</small></button>}
        <button type="button" role="menuitem" onClick={() => { close(); nav(`/support?from=${encodeURIComponent(window.location.pathname + window.location.search)}`); }}>
          Поддержка{n > 0 && <span className="badge accent" style={{ marginLeft: 6 }}>{n}</span>}<small>сообщить о проблеме, мои обращения</small></button>
      </>}
    </Dropdown>
  );
}

/** Аватар в шапке: имя и роли, «Выйти». */
export function UserMenu({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const name = me.display_name.replace(/\s*\(.*\)\s*$/, "");
  const initials = me.display_name.replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  const roles = me.roles.map((r) => r.title).join(", ");
  return (
    <Dropdown label={`${name} — ${roles}`} className="hm-user" button={<span className="avatar">{initials}</span>}>
      {(close) => <>
        <div className="hm-who"><b>{name}</b><small>{roles}</small><small>{me.email}</small></div>
        <button type="button" role="menuitem" onClick={() => { close(); onLogout(); }}>Выйти</button>
      </>}
    </Dropdown>
  );
}
