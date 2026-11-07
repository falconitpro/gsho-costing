import { useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../types";
import SettingsPage from "./SettingsPage";
import TeamPage from "./TeamPage";
import AccessPage from "./AccessPage";
import AdminPage from "./AdminPage";

// «Настройки» одним разделом: ставки и нормативы, сотрудники, уровни доступа, администрирование.
// Доступ к каждому блоку — как и раньше (уровни доступа и администрирование — ЗГД / админ / ФД); блок виден только тем, кому он доступен.

type Sec = "rates" | "team" | "access" | "admin";

export default function SettingsHub({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const has = (...codes: string[]) => me.roles.some((r) => codes.includes(r.code));
  const perm = (g: string, lvl: number) => (me.perms?.[g] ?? 0) >= lvl;
  const sections: [Sec, string, boolean][] = [
    ["rates", "Ставки и нормативы", has("admin", "fd", "gd", "cd")],
    ["team", "Сотрудники", true],
    ["access", "Уровни доступа", perm("access_admin", 2)],
    ["admin", "Администрирование", has("admin", "gd", "fd")],
  ];
  const visible = sections.filter(([, , ok]) => ok);
  const want = new URLSearchParams(loc.search).get("s") as Sec | null;
  const cur: Sec = visible.some(([k]) => k === want) ? (want as Sec) : visible[0]?.[0] || "team";
  return (
    <>
      <div className="row" style={{ alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Настройки</h1>
        <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
          {visible.map(([k, t]) => (
            <button key={k} onClick={() => nav(`/settings?s=${k}`, { replace: true })} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
              fontWeight: cur === k ? 700 : 500, color: cur === k ? "var(--ink)" : "var(--muted)", borderBottom: cur === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
        </div>
      </div>
      {cur === "rates" && <SettingsPage me={me} embedded />}
      {cur === "team" && <TeamPage me={me} embedded />}
      {cur === "access" && <AccessPage me={me} embedded />}
      {cur === "admin" && <AdminPage me={me} embedded />}
    </>
  );
}
