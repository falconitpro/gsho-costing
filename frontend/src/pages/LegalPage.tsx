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
import { CounterpartiesBlock } from "./CounterpartiesPage";
import { Crumbs, EntryGrid, useFetched, type Entry } from "../components/Levels";

// Юридический блок: договоры, претензии, контрагенты (клиенты и поставщики) с закреплёнными реквизитами БЕРГ-ХАУС
// (компании группы с юридическими и банковскими профилями — D-067), доверенности, шаблоны и правила.

type Own = { id: string; code: string; name: string; country: string; currency: string; sells_to_clients: boolean; is_active: boolean; profiles: { id: string; summary: string; check_status_title: string; banks: { status: string; verified: boolean; currency: string }[] }[] };
type Tab = "contracts" | "claims" | "counterparties" | "poa" | "templates" | "rules";
const TABS: [Tab, string, boolean][] = [["contracts", "Договоры", true], ["claims", "Претензии", true], ["counterparties", "Контрагенты", true], ["poa", "Доверенности", true], ["templates", "Шаблоны и примеры", true], ["rules", "Правила и ИИ", true]];

export default function LegalPage({ me }: { me: Me }) {
  const full = me.roles.some((r) => ["cd", "fd", "gd", "lawyer", "admin", "manager"].includes(r.code));   // остальные сотрудники видят только договоры
  const observer = me.roles.every((r) => r.code === "observer");
  // остальные сотрудники — договоры, претензии и контрагенты; наблюдатель — только контрагенты (как раньше)
  const tabs = full ? TABS : TABS.filter(([k]) => k === "counterparties" || (!observer && (k === "contracts" || k === "claims")));
  const loc = useLocation();
  const nav = useNavigate();
  const raw = new URLSearchParams(loc.search).get("tab");
  const asked = (raw === "own" ? "counterparties" : raw) as Tab;   // старая вкладка «Карточка БЕРГ-ХАУС» — теперь вверху «Контрагентов»
  // «Новый вид»: /legal без ?tab= — обзор: основные юридические статусы и входы в подразделы; «прежний вид» — сразу первая вкладка
  const overview = !raw;
  const tab: Tab | "" = overview ? "" : tabs.some(([k]) => k === asked) ? asked : tabs[0][0];
  const can = (k: Tab) => tabs.some(([x]) => x === k);
  const ov = useFetched({ contracts: can("contracts") ? "/api/legal/contracts" : null, claims: can("claims") ? "/api/legal/claims" : null, poa: can("poa") ? "/api/legal/poa?entity_id=&status=&q=" : null }, overview);
  const sum = (k: string) => ((ov[k] as { summary?: Record<string, number> } | undefined)?.summary) || null;
  const HINTS: Record<Tab, string> = { contracts: "реестр договоров, согласование, подписание, обязательства и неустойки", claims: "входящие и исходящие претензии, сроки ответа",
    counterparties: "клиенты и поставщики, реквизиты БЕРГ-ХАУС и компаний группы", poa: "доверенности компаний группы, сроки и сканы", templates: "шаблоны договоров и примеры документов", rules: "правила проверки и настройки ИИ" };
  const entryOf = ([k, t]: [Tab, string, boolean]): Entry => {
    const c = sum("contracts"), cl = sum("claims"), p = sum("poa");
    const stats: NonNullable<Entry["stats"]> = [];
    if (k === "contracts" && c) stats.push({ value: c.active ?? 0, label: "действующих" }, { value: c.overdue ?? 0, label: "с просрочкой", tone: c.overdue ? "bad" : "" }, { value: c.no_scan ?? 0, label: "без скана", tone: c.no_scan ? "warn" : "" }, { value: c.expiring ?? 0, label: "истекают", tone: c.expiring ? "warn" : "" });
    if (k === "claims" && cl) stats.push({ value: cl.open ?? 0, label: "в работе" }, { value: cl.signing ?? 0, label: "на подписи", tone: cl.signing ? "warn" : "" }, { value: cl.answer_overdue ?? 0, label: "истёк срок ответа", tone: cl.answer_overdue ? "bad" : "" });
    if (k === "poa" && p) stats.push({ value: p.active ?? 0, label: "действующих" }, { value: p.expiring ?? 0, label: "истекают", tone: p.expiring ? "warn" : "" });
    if (k === "counterparties" && own) stats.push({ value: own.items.length, label: "компаний группы" });
    const att = k === "contracts" && c?.overdue ? `${c.overdue} с просрочкой` : k === "claims" && cl?.answer_overdue ? `${cl.answer_overdue}: истёк срок ответа` : undefined;
    return { key: k, title: t, to: `/legal?tab=${k}`, hint: HINTS[k], stats, attention: att,
      links: k === "counterparties" ? [{ label: "Клиенты", to: "/legal?tab=counterparties&type=clients" }, { label: "Поставщики", to: "/legal?tab=counterparties&type=suppliers" }, ...(full ? [{ label: "Наши реквизиты", to: "/legal?tab=own" }] : [])] : undefined };
  };
  const [ownOpen, setOwnOpen] = useState(() => { try { return localStorage.getItem("legal_own_open") === "1" || raw === "own"; } catch { return raw === "own"; } });
  const toggleOwn = () => { const v = !ownOpen; setOwnOpen(v); try { localStorage.setItem("legal_own_open", v ? "1" : "0"); } catch { /* приватный режим */ } };
  const [own, setOwn] = useState<{ items: Own[]; can_edit: boolean } | null>(null);
  const [cur, setCur] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (!full) return; api.get<{ items: Own[]; can_edit: boolean }>("/api/legal/own").then((r) => { setOwn(r); if (!cur && r.items.length) setCur(r.items.find((x) => x.sells_to_clients)?.id || r.items[0].id); }).catch((e) => setMsg((e as Error).message)); }, []);
  const ownCard = own ? <OwnCompanies own={own} cur={cur} setCur={setCur} /> : null;
  return (
    <>
      <Crumbs items={overview ? [{ label: "Рабочее место", to: "/" }, { label: "Юридический блок" }] : [{ label: "Рабочее место", to: "/" }, { label: "Юридический блок", to: "/legal" }, { label: tabs.find(([k]) => k === tab)?.[1] }]} />
      <div className="row" style={{ alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Юридический блок</h1>
        <div className="row lg-tabs" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
          {<button onClick={() => nav("/legal")} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
            fontWeight: overview ? 700 : 500, color: overview ? "var(--ink)" : "var(--muted)", borderBottom: overview ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>Обзор</button>}
          {tabs.map(([k, t, ready]) => (
            <button key={k} disabled={!ready} title={ready ? "" : "появится на следующих этапах"} onClick={() => nav(`/legal?tab=${k}`)} style={{ border: 0, background: "transparent", cursor: ready ? "pointer" : "default", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
              fontWeight: tab === k ? 700 : 500, color: !ready ? "#b9c0c9" : tab === k ? "var(--ink)" : "var(--muted)", borderBottom: tab === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
        </div>
      </div>
      {msg && <div className="alert error">{msg}</div>}
      {overview && <EntryGrid entries={tabs.map(entryOf)} />}
      {tab === "contracts" && <ContractsBlock />}
      {tab === "claims" && <ClaimsBlock />}
      {tab === "poa" && <PoaBlock />}
      {tab === "templates" && <LegalTemplates />}
      {tab === "rules" && <LegalRules />}
      {tab === "counterparties" && (
        <>
          {full && own && (() => {
            const seller = own.items.find((x) => x.sells_to_clients) || own.items[0];
            return (
              <div className="card" style={{ border: "1px solid var(--accent)", borderLeft: "6px solid var(--accent)", background: "var(--accent-soft)", marginBottom: 14 }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                  <div><span style={{ fontSize: 16, fontWeight: 700 }}>📌 БЕРГ-ХАУС — наши реквизиты</span> <span className="badge" style={{ marginLeft: 6 }}>закреплено</span>
                    {seller && <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>Продавец в РФ: <b>{seller.name}</b>{seller.profiles[0] ? ` · ${seller.profiles[0].summary}` : ""} · компаний группы: {own.items.length}</div>}</div>
                  <button className="btn secondary" onClick={toggleOwn}>{ownOpen ? "Свернуть" : "Показать реквизиты"}</button>
                </div>
                {ownOpen && <div style={{ marginTop: 10 }}>{ownCard}</div>}
              </div>);
          })()}
          <h2 style={{ margin: "4px 0 8px" }}>Контрагенты</h2>
          <CounterpartiesBlock me={me} />
        </>)}
    </>
  );
}

function OwnCompanies({ own, cur, setCur }: { own: { items: Own[]; can_edit: boolean }; cur: string | null; setCur: (id: string) => void }) {
  return (
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
        </div>
  );
}
