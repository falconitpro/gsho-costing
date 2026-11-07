import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../types";
import ClientsPage from "./ClientsPage";
import FactoriesPage from "./FactoriesPage";

// «Контрагенты»: клиенты и поставщики в одном разделе. Переключатель сверху; выбор запоминается и доступен по ссылке ?type=clients|suppliers.

type Kind = "clients" | "suppliers";

export default function CounterpartiesPage({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const fromUrl = new URLSearchParams(loc.search).get("type") as Kind | null;
  const [kind, setKind] = useState<Kind>(() => { if (fromUrl === "clients" || fromUrl === "suppliers") return fromUrl; try { return (localStorage.getItem("counterparties_kind") as Kind) || "clients"; } catch { return "clients"; } });
  useEffect(() => { if (fromUrl && fromUrl !== kind && (fromUrl === "clients" || fromUrl === "suppliers")) setKind(fromUrl); }, [fromUrl]);
  useEffect(() => { try { localStorage.setItem("counterparties_kind", kind); } catch { /* приватный режим */ } }, [kind]);
  const pick = (k: Kind) => { setKind(k); nav(`/counterparties?type=${k}`, { replace: true }); };
  return (
    <>
      <div className="row" style={{ alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Контрагенты</h1>
        <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
          {([["clients", "Клиенты"], ["suppliers", "Поставщики"]] as const).map(([k, t]) => (
            <button key={k} onClick={() => pick(k)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
              fontWeight: kind === k ? 700 : 500, color: kind === k ? "var(--ink)" : "var(--muted)", borderBottom: kind === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
        </div>
      </div>
      {kind === "clients" ? <ClientsPage me={me} embedded /> : <FactoriesPage me={me} embedded />}
    </>
  );
}
