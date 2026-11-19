import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../types";
import ClientsPage from "./ClientsPage";
import FactoriesPage from "./FactoriesPage";

// «Контрагенты» — вкладка юридического блока (D-067): клиенты и поставщики. Переключатель сверху; выбор запоминается
// и доступен по ссылке /legal?tab=counterparties&type=clients|suppliers. Старые адреса /counterparties, /clients, /factories ведут сюда.

type Kind = "clients" | "suppliers";

export function CounterpartiesBlock({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const fromUrl = new URLSearchParams(loc.search).get("type") as Kind | null;
  const [kind, setKind] = useState<Kind>(() => { if (fromUrl === "clients" || fromUrl === "suppliers") return fromUrl; try { return (localStorage.getItem("counterparties_kind") as Kind) || "clients"; } catch { return "clients"; } });
  useEffect(() => { if (fromUrl && fromUrl !== kind && (fromUrl === "clients" || fromUrl === "suppliers")) setKind(fromUrl); }, [fromUrl]);
  useEffect(() => { try { localStorage.setItem("counterparties_kind", kind); } catch { /* приватный режим */ } }, [kind]);
  const pick = (k: Kind) => { setKind(k); const q = new URLSearchParams(loc.search); q.set("tab", "counterparties"); q.set("type", k); q.delete("client"); nav(`/legal?${q.toString()}`); };
  return (
    <>
      <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)", marginBottom: 10 }}>
        {([["clients", "Клиенты"], ["suppliers", "Поставщики"]] as const).map(([k, t]) => (
          <button key={k} onClick={() => pick(k)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "6px 14px", fontSize: 14, fontFamily: "inherit",
            fontWeight: kind === k ? 700 : 500, color: kind === k ? "var(--ink)" : "var(--muted)", borderBottom: kind === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
      </div>
      {kind === "clients" ? <ClientsPage me={me} embedded /> : <FactoriesPage me={me} embedded />}
    </>
  );
}

/** Старые адреса раздела: параметры (type, id и др.) сохраняются. */
export default function CounterpartiesRedirect({ type }: { type?: Kind }) {
  const loc = useLocation();
  const q = new URLSearchParams(loc.search);
  q.set("tab", "counterparties");
  if (type) q.set("type", type);
  return <Navigate to={`/legal?${q.toString()}`} replace />;
}
