import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";
import CatalogPage from "./CatalogPage";
import PartsCatalog from "../components/PartsCatalog";
import { KitsBlock, OtherModelsBlock, WarehousesBlock } from "../components/CatalogExtra";
import { HsCodes } from "./AdminPage";
import { GroupSetupBlock } from "./PartsSetupPage";

// Единый «Каталог» (решение владельца 08.11.2026): все справочники в одном разделе — оборудование ГШО, запчасти, комплекты ТО,
// техника других производителей, склады, ТН ВЭД, компании группы и маршруты поставки. Выбранная вкладка — в адресе (?s=…).

type Sec = "equipment" | "parts" | "kits" | "other" | "warehouses" | "hs" | "entities" | "routes";
type Found = { models: { id: string; title: string; machine_type: string }[]; parts: { id: string; article: string; name_ru: string; manufacturer: string }[]; other: { id: string; title: string; machine_type: string }[] };

export default function CatalogHub({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const qs = new URLSearchParams(loc.search);
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const partsReader = has("cd", "fd", "gd", "admin", "logistician", "manager", "purchasing", "service_head");
  const fleetReader = has("manager", "service_head", "cd", "gd", "admin", "fd", "logistician", "purchasing");
  const setupReader = has("cd", "fd", "gd", "admin", "logistician");
  const SECS: [Sec, string, boolean][] = [
    ["equipment", "Оборудование ГШО", true],
    ["parts", "Запасные части", partsReader],
    ["kits", "Комплекты ТО", fleetReader],
    ["other", "Техника других производителей", true],
    ["warehouses", "Склады", partsReader],
    ["hs", "ТН ВЭД", has("admin", "gd")],
    ["entities", "Компании группы", setupReader],
    ["routes", "Маршруты поставки", setupReader],
  ];
  const visible = SECS.filter((x) => x[2]);
  const asked = qs.get("s") as Sec;
  const sec: Sec = visible.some(([k]) => k === asked) ? asked : "equipment";
  const go = (s: Sec, extra = "") => nav(`/catalog?s=${s}${extra}`, { replace: !extra });
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setFound(null); return; }
    const t = setTimeout(() => api.get<Found>(`/api/catalog/search?q=${encodeURIComponent(q.trim())}`).then(setFound).catch(() => setFound(null)), 300);
    return () => clearTimeout(t);
  }, [q]);
  const pick = (s: Sec, extra: string) => { setQ(""); setFound(null); go(s, extra); };
  const nothing = found && !found.models.length && !found.parts.length && !found.other.length;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 12, marginBottom: 6 }}>
        <h1 style={{ margin: 0 }}>Каталог</h1>
        <div style={{ position: "relative" }}>
          <input placeholder="Поиск: модель, исполнение, артикул, OEM, кросс-номер…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 340 }} />
          {found && (
            <div className="card" style={{ position: "absolute", right: 0, top: "100%", zIndex: 20, width: 420, maxHeight: 420, overflow: "auto", marginTop: 4, padding: 10, fontSize: 13 }}>
              {nothing && <div className="muted">Ничего не найдено</div>}
              {found.models.length > 0 && <><div className="muted" style={{ fontSize: 11, margin: "2px 0" }}>Оборудование ГШО</div>
                {found.models.map((m) => <div key={m.id}><a href="#" onClick={(e) => { e.preventDefault(); pick("equipment", `&model=${m.id}`); }}><b>{m.title}</b></a> <span className="muted">{m.machine_type}</span></div>)}</>}
              {found.parts.length > 0 && partsReader && <><div className="muted" style={{ fontSize: 11, margin: "8px 0 2px" }}>Запасные части</div>
                {found.parts.map((p) => <div key={p.id}><a href="#" onClick={(e) => { e.preventDefault(); pick("parts", `&part=${p.id}`); }}><b>{p.article}</b></a> {p.name_ru}{p.manufacturer && <span className="muted"> · {p.manufacturer}</span>}</div>)}</>}
              {found.other.length > 0 && <><div className="muted" style={{ fontSize: 11, margin: "8px 0 2px" }}>Техника других производителей</div>
                {found.other.map((o) => <div key={o.id}><a href="#" onClick={(e) => { e.preventDefault(); pick("other", ""); }}>{o.title}</a> <span className="muted">{o.machine_type}</span></div>)}</>}
            </div>)}
        </div>
      </div>
      <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)", marginBottom: 12, flexWrap: "wrap" }}>
        {visible.map(([k, t]) => (
          <button key={k} onClick={() => go(k)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "8px 12px", fontSize: 14, fontFamily: "inherit",
            fontWeight: sec === k ? 700 : 500, color: sec === k ? "var(--ink)" : "var(--muted)", borderBottom: sec === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
      </div>
      {sec === "equipment" && <CatalogPage me={me} embedded openModelId={qs.get("model") || undefined} />}
      {sec === "parts" && <PartsCatalog me={me} openId={qs.get("part") || undefined} />}
      {sec === "kits" && <KitsBlock me={me} />}
      {sec === "other" && <OtherModelsBlock me={me} />}
      {sec === "warehouses" && <WarehousesBlock me={me} />}
      {sec === "hs" && <div className="card"><HsCodes /></div>}
      {sec === "entities" && <GroupSetupBlock me={me} part="entities" />}
      {sec === "routes" && <GroupSetupBlock me={me} part="routes" />}
          </>);
}
