import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";
import LegalProfileBlock from "../components/LegalProfileBlock";
import LegalTemplates from "../components/LegalTemplates";
import LegalRules from "../components/LegalRules";
import PoaBlock from "../components/PoaBlock";
import ContractsBlock from "../components/ContractsBlock";
import ClaimsBlock from "../components/ClaimsBlock";

// Юридический блок: карточка БЕРГ-ХАУС (компании группы с юридическими и банковскими профилями).
// Следующие этапы добавят сюда реестр пакетов, библиотеку шаблонов и правил.

type Own = { id: string; code: string; name: string; country: string; currency: string; sells_to_clients: boolean; is_active: boolean; profiles: { id: string; summary: string; check_status_title: string; banks: { status: string; verified: boolean; currency: string }[] }[] };
type Tab = "contracts" | "claims" | "own" | "poa" | "templates" | "rules";
const TABS: [Tab, string, boolean][] = [["contracts", "Договоры", true], ["claims", "Претензии", true], ["own", "Карточка БЕРГ-ХАУС", true], ["poa", "Доверенности", true], ["templates", "Шаблоны и примеры", true], ["rules", "Правила и ИИ", true]];

export default function LegalPage({ me }: { me: Me }) {
  const full = me.roles.some((r) => ["cd", "fd", "gd", "lawyer", "admin", "manager"].includes(r.code));   // остальные сотрудники видят только договоры
  const tabs = full ? TABS : TABS.filter(([k]) => k === "contracts" || k === "claims");
  const loc = useLocation();
  const nav = useNavigate();
  const asked = new URLSearchParams(loc.search).get("tab") as Tab;
  const tab = full ? (TABS.some(([k]) => k === asked) ? asked : "contracts") : (new URLSearchParams(loc.search).get("tab") === "claims" ? "claims" : "contracts");
  const [own, setOwn] = useState<{ items: Own[]; can_edit: boolean } | null>(null);
  const [cur, setCur] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (!full) return; api.get<{ items: Own[]; can_edit: boolean }>("/api/legal/own").then((r) => { setOwn(r); if (!cur && r.items.length) setCur(r.items.find((x) => x.sells_to_clients)?.id || r.items[0].id); }).catch((e) => setMsg((e as Error).message)); }, []);
  return (
    <>
      <div className="row" style={{ alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Юридический блок</h1>
        <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
          {tabs.map(([k, t, ready]) => (
            <button key={k} disabled={!ready} title={ready ? "" : "появится на следующих этапах"} onClick={() => nav(`/legal?tab=${k}`, { replace: true })} style={{ border: 0, background: "transparent", cursor: ready ? "pointer" : "default", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
              fontWeight: tab === k ? 700 : 500, color: !ready ? "#b9c0c9" : tab === k ? "var(--ink)" : "var(--muted)", borderBottom: tab === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
        </div>
      </div>
      {msg && <div className="alert error">{msg}</div>}
      {tab === "contracts" && <ContractsBlock />}
      {tab === "claims" && <ClaimsBlock />}
      {tab === "poa" && <PoaBlock />}
      {tab === "templates" && <LegalTemplates />}
      {tab === "rules" && <LegalRules />}
      {tab === "own" && own && (
        <div className="grid" style={{ gridTemplateColumns: "320px 1fr" }}>
          <div className="card">
            <b>Компании группы</b>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 8px" }}>Продавец для клиентов в РФ — компания РФ. Покупатель у завода по умолчанию — она же; ФД, ЗГД или КД могут выбрать другую компанию группы при формировании пакета.</p>
            {own.items.map((e) => {
              const p = e.profiles[0];
              const banks = p?.banks.filter((b) => b.status === "active") || [];
              return (
                <div key={e.id} onClick={() => setCur(e.id)} style={{ cursor: "pointer", border: "1px solid var(--line)", borderLeft: `4px solid ${cur === e.id ? "var(--accent)" : "var(--line)"}`, borderRadius: 8, padding: "8px 12px", marginBottom: 6, background: cur === e.id ? "var(--accent-soft)" : "#fff", opacity: e.is_active ? 1 : .6 }}>
                  <b>{e.name}</b> <span className="badge" style={{ marginLeft: 4 }}>{e.code.toUpperCase()}</span>{e.sells_to_clients && <span className="badge ok" style={{ marginLeft: 4 }}>продавец в РФ</span>}
                  <div className="muted" style={{ fontSize: 12 }}>{e.country} · {e.currency} · {p ? p.check_status_title : "профиля нет"} · счетов: {banks.length}{banks.length ? ` (${banks.map((b) => b.currency + (b.verified ? " ✓" : "")).join(", ")})` : ""}</div>
                </div>);
            })}
            <p className="muted" style={{ fontSize: 11 }}>Сами компании (код, валюта, склад) заводятся в «Запасные части → Настройка → Компании группы».</p>
          </div>
          <div>{cur && <LegalProfileBlock ownerKind="own" ownerId={cur} title={`Юридический профиль — ${own.items.find((x) => x.id === cur)?.name || ""}`} />}
            {cur && <div style={{ marginTop: 12 }}><PoaBlock entityId={cur} compact /></div>}</div>
        </div>)}
    </>
  );
}
