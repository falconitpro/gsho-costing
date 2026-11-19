import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import type { Me } from "../types";

/** Меню по роли (D-097): 4–5 главных пунктов роли в строке, остальные доступные — в «Ещё».
 *  Права не меняются: в «Ещё» те же разделы, что были в меню; у нескольких ролей главные пункты объединяются. */

type Item = { key: string; to: string; label: string; end?: boolean };
const ITEMS: Item[] = [
  { key: "work", to: "/", label: "Рабочее место", end: true },
  { key: "deals", to: "/deals", label: "Сделки" },
  { key: "tasks", to: "/tasks", label: "Мои задачи" },
  { key: "board", to: "/board", label: "Доска" },
  { key: "summary", to: "/summary", label: "Сводка" },
  { key: "finance", to: "/finance", label: "Финансы" },
  { key: "legal", to: "/legal", label: "Юридический блок" },
  { key: "catalog", to: "/catalog", label: "Каталог" },
  { key: "supply", to: "/supply", label: "Снабжение и склад" },
  { key: "settings", to: "/settings", label: "Настройки" },
];
const PRIMARY: Record<string, string[]> = {
  manager: ["work", "deals", "tasks", "catalog"],
  logistician: ["work", "deals", "tasks", "supply", "summary"],
  service_head: ["work", "deals", "tasks", "supply"],
  purchasing: ["work", "supply", "catalog", "tasks"],
  cd: ["work", "deals", "tasks", "board", "summary"],
  fd: ["work", "finance", "deals", "board", "tasks"],
  gd: ["work", "deals", "board", "summary", "finance"],
  lawyer: ["work", "legal", "deals", "tasks"],
  admin: ["work", "settings", "deals"],
  observer: ["work", "deals", "board"],
};

export default function RoleNav({ me }: { me: Me }) {
  const codes = me.roles.map((r) => r.code);
  const has = (...r: string[]) => codes.some((c) => r.includes(c));
  const perm = (g: string) => (me.perms?.[g] ?? 0) >= 1;
  const allowed: Record<string, boolean> = {
    work: true, deals: true, tasks: true, legal: true, catalog: true, settings: true,
    board: has("cd", "fd", "gd", "observer"),
    summary: has("cd", "fd", "gd", "logistician", "service_head", "purchasing", "manager"),
    finance: perm("finance_section"),
    supply: has("cd", "fd", "gd", "admin", "logistician", "manager", "purchasing", "service_head"),
  };
  const primary = new Set(codes.flatMap((c) => PRIMARY[c] || []));
  if (primary.size === 0) ["work", "deals", "tasks"].forEach((k) => primary.add(k));
  const items = ITEMS.filter((i) => allowed[i.key]);
  const main = items.filter((i) => primary.has(i.key));
  const more = items.filter((i) => !primary.has(i.key));

  const loc = useLocation();
  const active = more.find((i) => (i.end ? loc.pathname === i.to : loc.pathname === i.to || loc.pathname.startsWith(i.to + "/")));
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { setOpen(false); }, [loc.pathname, loc.search]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown); document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <nav>
      {main.map((i) => <NavLink key={i.key} to={i.to} end={i.end}>{i.label}</NavLink>)}
      {more.length > 0 && (
        <div className="nav-more" ref={ref}>
          <button type="button" className={active ? "active" : ""} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
            {active ? active.label : "Ещё"} <span aria-hidden>▾</span></button>
          {open && <div className="hm-menu nav-more-menu" role="menu">{more.map((i) => (
            <NavLink key={i.key} to={i.to} end={i.end} role="menuitem">{i.label}</NavLink>))}</div>}
        </div>)}
    </nav>
  );
}
