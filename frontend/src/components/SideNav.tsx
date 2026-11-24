import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";
import { setNewUi } from "../uiMode";
import { isActive, navItems } from "./RoleNav";

/** D-159 «Новый вид»: боковая панель — ВСЕ разделы роли (те же права, что у верхнего меню, источник один — navItems),
 *  иконки, счётчики «Мои задачи» и «Поручения» (мне, открытые), сворачивание до иконок, возврат к прежнему виду. */

const P: Record<string, string> = {   // контурные иконки 24×24, цвет — currentColor
  work: "M3 11.5 12 4l9 7.5M5.5 9.5V20h13V9.5M10 20v-5.5h4V20",
  deals: "M3 8h18v11H3zM8.5 8V5.5h7V8M3 13h18",
  tasks: "M4 4h16v16H4zM8 12l3 3 5-6",
  assignments: "M7 4h10v3H7zM5 6h2m10 0h2v15H5V6M8.5 12h7M8.5 16h5",
  board: "M4 4h4.5v16H4zM10 4h4.5v11H10zM16 4h4v7h-4z",
  summary: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  finance: "M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  legal: "M12 3v18M6 21h12M4 7h16M7 7l-3 7c0 1.2 1.3 2 3 2s3-.8 3-2L7 7zm10 0-3 7c0 1.2 1.3 2 3 2s3-.8 3-2l-3-7z",
  office: "M3 5h18v14H3zM3 6l9 7 9-7",
  catalog: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11",
  supply: "M3 7.5 12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 13a7.5 7.5 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-1.7-1L15 3.5h-4l-.3 2.5a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.4L6.6 11a7.5 7.5 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7.6 7.6 0 0 0 1.7-1l2.4 1 2-3.4z",
  collapse: "M15 6l-6 6 6 6",
  expand: "M9 6l6 6-6 6",
  back: "M10 6 4 12l6 6M4 12h16",
};

export function Icon({ k }: { k: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={P[k] || P.work} /></svg>;
}

export function useSection(me: Me): string {
  const loc = useLocation();
  const cur = navItems(me).items.find((i) => isActive(i, loc.pathname));
  if (loc.pathname.startsWith("/directions/")) return "Направление";
  if (loc.pathname.startsWith("/support")) return "Поддержка";
  if (loc.pathname.startsWith("/whats-new")) return "Что нового";
  return cur ? cur.label : "";
}

export default function SideNav({ me, collapsed, onCollapse, onNavigate }: { me: Me; collapsed: boolean; onCollapse: (v: boolean) => void; onNavigate: () => void }) {
  const { items } = navItems(me);
  const loc = useLocation();
  const [count, setCount] = useState<Record<string, number>>({});
  useEffect(() => { onNavigate(); }, [loc.pathname, loc.search]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {      // счётчики: открытые «Мои задачи» и поручения «мне» — те же списки, что на страницах
    let alive = true;
    Promise.all([
      api.bg<unknown[]>("/api/tasks/my").then((r) => (Array.isArray(r) ? r.length : 0)).catch(() => 0),
      api.bg<{ items: unknown[] }>("/api/management/assignments?scope=mine&status=open").then((r) => r.items.length).catch(() => 0),
    ]).then(([t, a]) => { if (alive) setCount({ tasks: t, assignments: a }); });
    return () => { alive = false; };
  }, [loc.pathname]);
  return (
    <>
      <nav className="app-nav" aria-label="Разделы">
        {items.map((i) => (
          <NavLink key={i.key} to={i.to} end={i.end} title={collapsed ? i.label : undefined}>
            <Icon k={i.key} />
            <span className="app-nav-label">{i.label}</span>
            {count[i.key] ? <span className="app-count" aria-label={`${count[i.key]} открыто`}>{count[i.key] > 99 ? "99+" : count[i.key]}</span> : null}
          </NavLink>))}
      </nav>
      <div className="app-side-foot">
        <button type="button" className="app-side-btn app-collapse" onClick={() => onCollapse(!collapsed)} aria-pressed={collapsed} title={collapsed ? "Развернуть панель" : "Свернуть панель"}>
          <Icon k={collapsed ? "expand" : "collapse"} /><span className="app-nav-label">Свернуть панель</span></button>
        <button type="button" className="app-side-btn" onClick={() => setNewUi(false)} title="Вернуть прежний вид">
          <Icon k="back" /><span className="app-nav-label">Прежний вид</span></button>
      </div>
    </>);
}
