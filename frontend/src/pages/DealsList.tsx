import { useEffect, useMemo, useState } from "react";
import { PRIORITY_COLOR, toneOf } from "../colors";
import { lbl, PRIORITY } from "../labels";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtRub } from "../api";
import { can, STATUS_TITLES, type Deal, type Me } from "../types";
import ColleaguesDeals from "../components/ColleaguesDeals";
import PartsCreateForm from "../components/PartsRequestCreate";

type Exec = { done: number; total: number; overdue: number; next: { title: string; on: string } | null; delay_days: number | null };
type Row = Deal & { group?: string; parent_number?: string; related_count?: number; manager_name?: string; machines?: number; equipment?: string; equipment_lines?: { qty: number; name: string }[]; equipment_more?: number;
  price_rub?: number | null; price_amount?: number | null; price_currency?: string | null; exec?: Exec; factory_contract_number?: string | null;
  kind?: "gsho" | "parts"; stage?: Stage; stage_title?: string; block?: Exclude<Block, "archive">; status_text?: string; lines?: number; urgent?: boolean; closed?: string };
// Заявка на запчасти в списке сделок (единый раздел «Сделки»: ГШО и запчасти)
type PartsRow = { id: string; number: string; client_id: string; client_name: string; machine: string; urgent: boolean; created_at: string; manager_id: string | null; manager_name: string;
  warranty: boolean; lines: number; status_text: string; closed: string; stage: Stage; stage_title: string; block: Exclude<Block, "archive">; price_rub: number };
// Общие этапы для ГШО и запчастей: ими считают воронку, доску и рабочее место
type Stage = "request" | "calc" | "kp" | "execution" | "closed";
const STAGE_TITLES: Record<Stage, string> = { request: "Запрос", calc: "Расчёт и согласование", kp: "КП у клиента", execution: "Исполнение", closed: "Закрыта" };
const STAGE_OF_STATUS: Record<string, Stage> = { draft: "request", logistics_calc: "request", service_calc: "request", calc_ready: "calc", cd_approval: "calc", fd_visa: "calc", gd_visa: "calc", rework: "calc",
  approved: "kp", kp_sent: "kp", won: "execution", contract: "execution", execution: "execution", closed: "closed", lost: "closed", cancelled: "closed" };
const stageOf = (d: Row): Stage => d.stage || STAGE_OF_STATUS[d.status] || "request";
const toRow = (r: PartsRow): Row => ({ ...(r as unknown as Row), kind: "parts", status: `parts:${r.stage}`, status_detail: r.status_text, priority: r.urgent ? "urgent" : "normal", equipment: r.machine,
  machines: r.lines, equipment_lines: [], requested_at: r.created_at, kp_due: null, current_version_no: 0 });
type DealType = "" | "gsho" | "parts";

// Блоки экрана: в обработке (до результата), выигранные (контракт, исполнение, закрытые), проигранные и отменённые
type Block = "work" | "won" | "lost" | "archive";
const BLOCK_STATUSES: Record<Exclude<Block, "archive">, string[]> = {
  work: ["draft", "logistics_calc", "service_calc", "calc_ready", "cd_approval", "fd_visa", "gd_visa", "rework", "approved", "kp_sent"],
  won: ["won", "contract", "execution", "closed"],
  lost: ["lost", "cancelled"],
};
const blockOfStatus = (st: string): Exclude<Block, "archive"> => (BLOCK_STATUSES.won.includes(st) ? "won" : BLOCK_STATUSES.lost.includes(st) ? "lost" : "work");
const blockOf = (d: Row): Exclude<Block, "archive"> => d.block || blockOfStatus(d.status);
// цвет статуса по этапу воронки
// фирменная палитра: красный акцент, чёрный, серые — без зелёного и синего. Чем дальше по воронке, тем темнее; после выигрыша — красный.
const PRIO_ORDER: Record<string, number> = { low: 0, normal: 1, medium: 1, high: 2, urgent: 3, critical: 3 };
const PRIO_COLOR = PRIORITY_COLOR;
const dealDate = (d: Row) => (d.requested_at || d.created_at || "") as string;
const today = () => new Date().toISOString().slice(0, 10);
const daysTo = (iso?: string | null) => (iso ? Math.round((new Date(iso.slice(0, 10)).getTime() - new Date(today()).getTime()) / 86400000) : null);
const kpOverdue = (d: Row) => d.kind !== "parts" && BLOCK_STATUSES.work.slice(0, 9).includes(d.status) && (daysTo(d.kp_due) ?? 1) < 0;

type SortKey = "number" | "date" | "client" | "status" | "kp_due" | "priority" | "manager" | "price" | "next" | "progress" | "machines";
const sortVal = (d: Row, k: SortKey): string | number => {
  switch (k) {
    case "number": return d.number; case "date": return dealDate(d); case "client": return d.client_name || "";
    case "status": return d.kind === "parts" ? ["request", "calc", "kp", "execution", "closed"].indexOf(stageOf(d)) * 10 + 5 : BLOCK_STATUSES.work.concat(BLOCK_STATUSES.won, BLOCK_STATUSES.lost).indexOf(d.status);
    case "kp_due": return d.kp_due || "9999"; case "priority": return PRIO_ORDER[d.priority] ?? 1; case "manager": return d.manager_name || "";
    case "price": return d.price_rub ?? -1; case "machines": return d.machines ?? 0; case "next": return d.exec?.next?.on || "9999"; case "progress": return d.exec ? d.exec.done / Math.max(d.exec.total, 1) : -1;
  }
};

/** Задача 9: связанные заявки (исходная и её дубли) идут подряд — дубли сразу под исходной, с отступом. */
function grouped(deals: Row[]): Row[] {
  const byGroup = new Map<string, Row[]>();
  for (const d of deals) { const g = d.group || d.id; if (!byGroup.has(g)) byGroup.set(g, []); byGroup.get(g)!.push(d); }
  const out: Row[] = [], seen = new Set<string>();
  for (const d of deals) {
    const g = d.group || d.id;
    if (seen.has(g)) continue;
    seen.add(g);
    const members = byGroup.get(g)!;
    out.push(...members.filter((m) => !m.parent_number), ...members.filter((m) => m.parent_number).sort((a, b) => a.number.localeCompare(b.number, "ru", { numeric: true })));
  }
  return out;
}

const PARTS_TONE_STATUS: Record<Stage, string> = { request: "draft", calc: "cd_approval", kp: "kp_sent", execution: "execution", closed: "closed" };
function StatusBadge({ d }: { d: Row }) {
  if (d.kind === "parts") {
    const st = stageOf(d);
    const [bg, fg] = toneOf(d.closed === "lost" || d.closed === "cancelled" ? "lost" : PARTS_TONE_STATUS[st]);
    return <div style={{ minWidth: 120 }}><span className="badge" style={{ background: bg, color: fg, fontWeight: 600, whiteSpace: "nowrap" }}>{d.closed ? d.status_text : STAGE_TITLES[st]}</span>
      {!d.closed && d.status_text && <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{d.status_text}</div>}</div>;
  }
  const [bg, fg] = toneOf(d.status);
  const detail = (d.status_detail || "").split(" · ").slice(1).join(" · ");
  return <div style={{ minWidth: 120 }}><span className="badge" style={{ background: bg, color: fg, fontWeight: 600, whiteSpace: "nowrap" }}>{STATUS_TITLES[d.status] || d.status}</span>
    {detail && <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{detail}</div>}</div>;
}
function TypeMark({ d }: { d: Row }) {
  return <span className="badge" title={d.kind === "parts" ? "сделка по запасным частям" : "сделка по оборудованию ГШО"} style={{ fontSize: 10, padding: "0 5px", marginLeft: 6, background: d.kind === "parts" ? "#eef2f7" : "#f4f4f4" }}>{d.kind === "parts" ? "ЗЧ" : "ГШО"}</span>;
}
function KpDue({ d }: { d: Row }) {
  if (!d.kp_due) return <span className="muted">—</span>;
  const n = daysTo(d.kp_due)!;
  const active = BLOCK_STATUSES.work.slice(0, 9).includes(d.status);
  return <span style={{ whiteSpace: "nowrap" }}>{fmtDate(d.kp_due)}{active && (n < 0 ? <span className="badge bad" style={{ marginLeft: 4 }}>просрочен {-n} дн.</span>
    : n <= 2 ? <span className="badge warn" style={{ marginLeft: 4 }}>{n === 0 ? "сегодня" : `через ${n} дн.`}</span> : null)}</span>;
}
function Prio({ p }: { p: string }) {
  const o = PRIO_ORDER[p] ?? 1;
  return <span style={{ whiteSpace: "nowrap" }}><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: PRIO_COLOR[o], marginRight: 5 }} />{lbl(PRIORITY, p)}</span>;
}
function Progress({ e }: { e?: Exec }) {
  if (!e || !e.total) return <span className="muted">—</span>;
  const pct = Math.round((e.done / e.total) * 100);
  return (<div style={{ minWidth: 110 }}>
    <div style={{ height: 6, background: "#eee", borderRadius: 3, overflow: "hidden" }}><div style={{ width: `${pct}%`, height: "100%", background: e.overdue ? "var(--accent)" : "var(--ink)" }} /></div>
    <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{e.done} из {e.total} этапов{e.overdue ? <span style={{ color: "var(--accent)" }}> · просрочено {e.overdue}</span> : ""}</div>
  </div>);
}
const money = (d: Row) => (d.price_rub == null ? <span className="muted">—</span> : d.price_currency && d.price_currency !== "RUB" && d.price_amount != null
  ? <span style={{ whiteSpace: "nowrap" }}>{fmtRub(d.price_amount)} {d.price_currency}<div className="muted" style={{ fontSize: 11 }}>≈ {fmtRub(d.price_rub)} RUB</div></span>
  : <span style={{ whiteSpace: "nowrap" }}>{fmtRub(d.price_rub)} RUB</span>);

type Filters = { q: string; manager: string; client: string; priority: string; statuses: string[]; flag: "" | "overdue" | "mine"; type: DealType };
const EMPTY_F: Filters = { q: "", manager: "", client: "", priority: "", statuses: [], flag: "", type: "" };
const load = <T,>(k: string, d: T): T => { try { const v = JSON.parse(localStorage.getItem(k) || ""); return v ?? d; } catch { return d; } };
const save = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* хранилище недоступно */ } };

export default function DealsList({ me }: { me: Me }) {
  const [deals, setDeals] = useState<Row[]>([]);
  const [clients, setClients] = useState<{ id: string; name: string; sites: { name: string }[] }[]>([]);
  const [showNew, setShowNew] = useState(new URLSearchParams(window.location.search).get("new") === "1");   // с рабочего места: сразу открыть форму
  const [newKind, setNewKind] = useState<"gsho" | "parts">((new URLSearchParams(window.location.search).get("new_type") as "parts") === "parts" ? "parts" : "gsho");
  const partsReader = me.roles.some((r) => ["manager", "cd", "fd", "gd", "purchasing", "service_head", "logistician", "admin", "observer"].includes(r.code));
  const partsCreator = me.roles.some((r) => ["manager", "cd", "gd", "purchasing"].includes(r.code));
  const [partsMeta, setPartsMeta] = useState<{ channels: Record<string, string>; line_status: Record<string, string>; close_kinds: Record<string, string>; can_create: boolean } | null>(null);
  useEffect(() => { if (partsCreator) api.get<typeof partsMeta>("/api/parts/requests/meta").then(setPartsMeta).catch(() => setPartsMeta(null)); }, [partsCreator]);
  const [form, setForm] = useState({ client_id: "", contact: "", kp_due: "", priority: "normal", service_flag: false, target_hours: "" });
  const [err, setErr] = useState("");
  const nav = useNavigate();
  const isManager = me.roles.some((r) => ["manager", "cd", "gd"].includes(r.code));
  const canAssign = me.roles.some((r) => ["cd", "gd"].includes(r.code));
  const [managers, setManagers] = useState<{ id: string; display_name: string }[]>([]);
  const [managerId, setManagerId] = useState("");
  useEffect(() => {
    if (canAssign) api.get<{ users: { id: string; display_name: string; roles: string[] }[] }>("/api/team").then((r) => setManagers(r.users.filter((u) => u.roles.includes("manager") && u.id !== me.id))).catch(() => setManagers([]));
  }, [canAssign, me.id]);

  const urlBlock = new URLSearchParams(window.location.search).get("block") as Block | null;   // с рабочего места: ?block=won|work|archive
  const [block, setBlockState] = useState<Block>(() => (urlBlock && ["work", "won", "lost", "archive"].includes(urlBlock) ? urlBlock : load<Block>("gsho.dealsBlock", "work")));
  const setBlock = (b: Block) => { setBlockState(b); save("gsho.dealsBlock", b); setF({ ...f, statuses: [] }); };
  const urlType = new URLSearchParams(window.location.search).get("type") as DealType | null;
  const [f, setFState] = useState<Filters>(() => ({ ...EMPTY_F, type: "gsho", ...load<Partial<Filters>>("gsho.dealsFilters", {}), ...(urlType === "gsho" || urlType === "parts" ? { type: urlType } : {}) }));
  const setF = (x: Filters) => { setFState(x); save("gsho.dealsFilters", x); };
  const urlSort = new URLSearchParams(window.location.search).get("sort") as SortKey | null;
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>(() => (urlSort ? { key: urlSort, dir: 1 as 1 | -1 } : load("gsho.dealsSort2", { key: "date" as SortKey, dir: -1 as 1 | -1 })));
  const toggleSort = (key: SortKey) => {
    const next = { key, dir: (sort.key === key ? -sort.dir : ["date", "priority", "price", "progress"].includes(key) ? -1 : 1) as 1 | -1 };
    setSort(next); save("gsho.dealsSort2", next);
  };
  const [archive, setArchive] = useState<Row[]>([]);
  const [parts, setParts] = useState<Row[]>([]);
  useEffect(() => { api.get<Row[]>("/api/deals").then((rows) => setDeals(rows.map((d) => ({ ...d, kind: "gsho" as const })))); }, []);
  useEffect(() => { if (partsReader) api.get<PartsRow[]>("/api/parts/requests?open_only=false").then((rows) => setParts(rows.map(toRow))).catch(() => setParts([])); }, [partsReader]);
  const all = useMemo(() => [...deals, ...parts], [deals, parts]);
  useEffect(() => { if (block === "archive") api.get<Row[]>("/api/deals?archived=true").then((rows) => setArchive(rows.map((d) => ({ ...d, kind: "gsho" as const })))); }, [block]);
  const byType = (d: Row) => !f.type || (d.kind || "gsho") === f.type;
  const counts = useMemo(() => ({ work: all.filter((d) => byType(d) && blockOf(d) === "work").length, won: all.filter((d) => byType(d) && blockOf(d) === "won").length,
    lost: all.filter((d) => byType(d) && blockOf(d) === "lost").length }), [all, f.type]);
  const typeCounts = useMemo(() => ({ gsho: deals.length, parts: parts.length }), [deals, parts]);
  const inBlock = (block === "archive" ? archive : all.filter((d) => blockOf(d) === block)).filter(byType);
  const q = f.q.trim().toLowerCase();
  const shown = inBlock.filter((d) => (!q || [d.number, d.client_name, d.site, d.equipment, d.contract_number, d.factory_contract_number, d.manager_name].some((x) => (x || "").toLowerCase().includes(q)))
    && (!f.manager || d.manager_id === f.manager) && (!f.client || d.client_id === f.client) && (!f.priority || String(PRIO_ORDER[d.priority] ?? 1) === f.priority)
    && (!f.statuses.length || f.statuses.includes(d.kind === "parts" ? `stage:${stageOf(d)}` : d.status))
    && (f.flag !== "overdue" || kpOverdue(d) || (d.exec?.overdue || 0) > 0) && (f.flag !== "mine" || d.manager_id === me.id));
  const sorted = [...shown].sort((a, b) => {
    const x = sortVal(a, sort.key), y = sortVal(b, sort.key);
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ru", { numeric: true });
    return c * sort.dir || a.number.localeCompare(b.number, "ru", { numeric: true }) * -1;
  });
  const statusChips: (readonly [string, number])[] = block === "archive" ? [] : f.type === "parts"
    ? (Object.keys(STAGE_TITLES) as Stage[]).map((st) => [`stage:${st}`, inBlock.filter((d) => stageOf(d) === st).length] as const).filter(([, n]) => n > 0)
    : [...BLOCK_STATUSES[block].map((st) => [st, inBlock.filter((d) => d.kind !== "parts" && d.status === st).length] as const),
       ...(Object.keys(STAGE_TITLES) as Stage[]).map((st) => [`stage:${st}`, inBlock.filter((d) => d.kind === "parts" && stageOf(d) === st).length] as const)].filter(([, n]) => n > 0);
  const chipTitle = (st: string) => (st.startsWith("stage:") ? `ЗЧ · ${STAGE_TITLES[st.slice(6) as Stage]}` : STATUS_TITLES[st]);
  const chipTone = (st: string) => toneOf(st.startsWith("stage:") ? PARTS_TONE_STATUS[st.slice(6) as Stage] : st);
  const mgrs = [...new Map(inBlock.filter((d) => d.manager_id).map((d) => [d.manager_id, d.manager_name || "—"])).entries()].sort((a, b) => a[1].localeCompare(b[1], "ru"));
  const cls = [...new Map(inBlock.filter((d) => d.client_id).map((d) => [d.client_id, d.client_name || "—"])).entries()].sort((a, b) => a[1].localeCompare(b[1], "ru"));
  const anyFilter = JSON.stringify({ ...f, q: f.q.trim(), type: "" }) !== JSON.stringify(EMPTY_F);
  const fin = can(me, "finance_analytics");   // суммы и портфель — только ФД, КД, ЗГД
  const kpis = block === "work" ? [
    ["В обработке", shown.length, ""], ["Срок КП просрочен", shown.filter(kpOverdue).length, "bad"],
    ["На утверждении и визах", shown.filter((d) => ["calc_ready", "cd_approval", "fd_visa", "gd_visa"].includes(d.status)).length, ""],
    ["На доработке", shown.filter((d) => d.status === "rework").length, "warn"], ["КП отправлено", shown.filter((d) => d.status === "kp_sent" || (d.kind === "parts" && stageOf(d) === "kp")).length, "ok"],
    ...(shown.some((d) => d.kind === "parts") ? [["Машина стоит", shown.filter((d) => d.kind === "parts" && d.urgent).length, "bad"]] : [])]
    : block === "won" ? [
    ["Сделок", shown.length, ""], ["Заключение контракта", shown.filter((d) => ["won", "contract"].includes(d.status)).length, ""],
    ["Исполнение", shown.filter((d) => d.status === "execution").length, ""], ["С просроченными этапами", shown.filter((d) => (d.exec?.overdue || 0) > 0).length, "bad"],
    ["Машин / позиций", shown.reduce((s_, d) => s_ + (d.machines || 0), 0), ""],
    ...(fin && shown.some((d) => d.price_rub != null) ? [["Портфель, RUB без НДС", fmtRub(shown.reduce((s_, d) => s_ + (d.price_rub || 0), 0)), ""]] : [])]
    : [["Сделок", shown.length, ""]];
  const th = (k: SortKey, t: string, w?: number) => (
    <th key={k} onClick={() => toggleSort(k)} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap", width: w, minWidth: w }} title="сортировать по столбцу">
      {t} <span style={{ opacity: sort.key === k ? 1 : 0.35 }}>{sort.key === k ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span></th>);
  const numCell = (d: Row) => (
    <td style={{ whiteSpace: "nowrap", ...(d.parent_number ? { paddingLeft: 22 } : {}) }}>{d.parent_number && <span className="muted">↳ </span>}<Link to={d.kind === "parts" ? `/deals/parts/${d.id}` : `/deals/${d.id}`} style={{ fontWeight: 600 }}>{d.number}</Link>{!f.type && <TypeMark d={d} />}
      {!d.parent_number && (d.related_count || 0) > 0 && <span className="badge" title="связанные заявки по одному запросу клиента" style={{ marginLeft: 6 }}>+{d.related_count} связ.</span>}
      {d.parent_number && <div className="muted" style={{ fontSize: 11 }}>дубль {d.parent_number}</div>}
      <div className="muted" style={{ fontSize: 11 }}>{fmtDate(dealDate(d))}</div></td>);
  const clientCell = (d: Row) => (<td style={{ minWidth: 160 }}><b>{d.client_name}</b>{d.site && <div className="muted" style={{ fontSize: 12 }}>{d.site}</div>}</td>);
  const eqCell = (d: Row) => d.kind === "parts" ? (<td style={{ minWidth: 180, fontSize: 13 }}>{d.equipment || <span className="muted">машина не указана</span>}{d.urgent && <div><span className="badge bad" style={{ fontSize: 10 }}>машина стоит</span></div>}</td>) : (<td style={{ minWidth: 180, fontSize: 13 }}>{d.equipment_lines?.length ? <>{d.equipment_lines.map((l, i) => <div key={i} style={{ whiteSpace: "nowrap" }}>{l.name || <span className="muted">без наименования</span>}</div>)}{d.equipment_more ? <div className="muted" style={{ fontSize: 11 }}>… ещё {d.equipment_more}</div> : null}</> : <span className="muted">позиций нет</span>}</td>);
  const qtyCell = (d: Row) => d.kind === "parts" ? (<td className="num" style={{ fontSize: 13, whiteSpace: "nowrap" }}>{d.lines}<div className="muted" style={{ fontSize: 11 }}>поз.</div></td>) : (<td className="num" style={{ fontSize: 13, whiteSpace: "nowrap" }}>{d.equipment_lines?.length ? <>{d.equipment_lines.map((l, i) => <div key={i}>{l.qty}</div>)}{(d.equipment_lines.length > 1 || d.equipment_more) ? <div style={{ borderTop: "1px solid var(--line)", fontWeight: 600 }}>{d.machines ?? 0}</div> : null}</> : <span className="muted">—</span>}</td>);
  useEffect(() => { api.get<typeof clients>("/api/refs/clients").then(setClients); }, []);

  const [docs, setDocs] = useState<{ file: File; category: string }[]>([]);   // файлы от клиента — хранятся в сделке
  async function create() {
    try {
      const d = await api.post<Deal & { warning?: string }>("/api/deals", { ...form, manager_id: managerId || undefined, kp_due: form.kp_due || null, target_hours: form.target_hours ? Number(form.target_hours) : null });
      if (d.warning) alert(d.warning);
      for (const x of docs) await api.upload(`/api/deals/${d.id}/documents`, x.file, { category: x.category, comment: "загружено при заведении сделки" });
      nav(`/deals/${d.id}`);
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <>
      <div className="page-head">
        <h1>Сделки</h1>
        <div className="tabs" style={{ margin: 0 }}>
          {([["work", "В обработке", counts.work], ["won", "Выигранные и исполнение", counts.won], ["lost", "Проигранные и отменённые", counts.lost], ["archive", "Архив", null]] as const).map(([k, t, n]) => (
            <button key={k} className={block === k ? "active" : ""} onClick={() => setBlock(k)}>{t}{n != null && <span className="badge" style={{ marginLeft: 6 }}>{n}</span>}</button>))}
        </div>
        <div className="push row" style={{ gap: 8 }}>
          {f.type === "parts" ? (partsCreator && <button className="btn add" onClick={() => { setNewKind("parts"); setShowNew(!(showNew && newKind === "parts")); }}>Новая заявка на запчасти</button>)
            : f.type === "gsho" ? (isManager && <button className="btn add" onClick={() => { setNewKind("gsho"); setShowNew(!(showNew && newKind === "gsho")); }}>Новая сделка ГШО</button>)
            : (isManager || partsCreator) && <button className="btn add" onClick={() => setShowNew(!showNew)}>Новая сделка</button>}
        </div>
      </div>
      <div className="deals-layout">
      {partsReader && <nav className="deals-kinds" aria-label="Вид сделок">
        {([["gsho", "ГШО", "оборудование", typeCounts.gsho], ["parts", "Запасные части", "заявки клиентов", typeCounts.parts]] as const).map(([k, t, sub, n]) => (
          <button key={k} className={"deals-kind" + (f.type === k ? " active" : "")} onClick={() => { setF({ ...f, type: k, statuses: [] }); setNewKind(k); }}>
            <span className="deals-kind-title">{t}</span><span className="deals-kind-sub">{sub}</span><span className="deals-kind-count">{n}</span></button>))}
        <button className={"deals-kind small" + (f.type === "" ? " active" : "")} onClick={() => setF({ ...f, type: "", statuses: [] })}>все сделки вместе</button>
      </nav>}
      <div className="deals-main">
      {showNew && (isManager || partsCreator) && !f.type && (
        <div className="row" style={{ gap: 8, marginBottom: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>Тип сделки:</span>
          <div className="tabs" style={{ margin: 0 }}>
            {isManager && <button className={newKind === "gsho" ? "active" : ""} onClick={() => setNewKind("gsho")}>Оборудование ГШО</button>}
            {partsCreator && <button className={newKind === "parts" ? "active" : ""} onClick={() => setNewKind("parts")}>Запасные части</button>}
          </div>
        </div>)}
      {showNew && newKind === "parts" && partsMeta && (
        <PartsCreateForm meta={partsMeta} clients={clients} onCreated={(r) => nav(`/deals/parts/${r.id}`)} onCancel={() => setShowNew(false)} />)}
      {showNew && newKind === "gsho" && isManager && (
        <div className="card">
          <div className="grid cols-3">
            <label className="f">Клиент
              <select value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })}>
                <option value="">—</option>
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            {canAssign && (
              <label className="f">Ответственный менеджер
                <select value={managerId} onChange={(e) => setManagerId(e.target.value)}>
                  <option value="">я ({me.display_name})</option>
                  {managers.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
                </select></label>)}
            <label className="f">Контактное лицо<input value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} /></label>
            <label className="f">Срок КП<input type="date" value={form.kp_due} onChange={(e) => setForm({ ...form, kp_due: e.target.value })} /></label>
            <label className="f">Приоритет
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                <option value="low">низкий</option><option value="normal">обычный</option><option value="high">высокий</option>
              </select>
            </label>
            <label className="f">Сервис до наработки, м·ч
              <div className="row"><input type="checkbox" checked={form.service_flag} onChange={(e) => setForm({ ...form, service_flag: e.target.checked })} />
                <input type="number" value={form.target_hours} placeholder="10000" disabled={!form.service_flag} onChange={(e) => setForm({ ...form, target_hours: e.target.value })} /></div>
            </label>
          </div>
          <div style={{ marginTop: 10 }}>
            <b>Файлы от клиента</b> <span className="muted">(если есть: тендерная документация, типовая форма договора, ТЗ — сохраняются в сделке; тендер и форму договора проверит юрист)</span>
            <div className="row" style={{ marginTop: 4 }}><input type="file" multiple onChange={(e) => { setDocs([...docs, ...(Array.from(e.target.files || []) as File[]).map((file) => ({ file, category: /догов|contract/i.test(file.name) ? "contract_form" : /тендер|tender|документац/i.test(file.name) ? "tender" : /тз|техническ|spec/i.test(file.name) ? "client_spec" : "client_other" }))]); e.target.value = ""; }} /></div>
            {docs.map((x, i) => (
              <div key={i} className="row" style={{ marginTop: 4 }}>
                <span>{x.file.name}</span>
                <select value={x.category} onChange={(e) => setDocs(docs.map((y, j) => (j === i ? { ...y, category: e.target.value } : y)))}>
                  <option value="tender">Тендерная документация</option><option value="contract_form">Типовая форма договора клиента</option>
                  <option value="client_spec">Техническое задание клиента</option><option value="client_other">Прочие файлы клиента</option>
                </select>
                <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setDocs(docs.filter((_, j) => j !== i))}>×</button>
              </div>))}
          </div>
          {err && <div className="alert error">{err}</div>}
          <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={create} disabled={!form.client_id}>Создать</button></div>
        </div>
      )}
      <div className="card" style={{ padding: "10px 12px" }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <input placeholder="🔍 номер, клиент, объект, оборудование, контракт…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} style={{ flex: "1 1 260px", minWidth: 0 }} />
          <select value={f.manager} onChange={(e) => setF({ ...f, manager: e.target.value })}><option value="">все менеджеры</option>{mgrs.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
          <select value={f.client} onChange={(e) => setF({ ...f, client: e.target.value })} style={{ maxWidth: 220 }}><option value="">все клиенты</option>{cls.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
          <select value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="">любой приоритет</option>
            <option value="3">срочный</option><option value="2">высокий</option><option value="1">обычный</option><option value="0">низкий</option></select>
          <div className="tabs" style={{ margin: 0 }}>
            <button className={f.flag === "" ? "active" : ""} onClick={() => setF({ ...f, flag: "" })}>все</button>
            <button className={f.flag === "mine" ? "active" : ""} onClick={() => setF({ ...f, flag: "mine" })}>мои</button>
            <button className={f.flag === "overdue" ? "active" : ""} onClick={() => setF({ ...f, flag: "overdue" })}>с просрочкой</button></div>
          {anyFilter && <button className="btn ghost" onClick={() => setF({ ...EMPTY_F, type: f.type })}>сбросить фильтры</button>}
        </div>
        {statusChips.length > 1 && <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          <span className="muted" style={{ fontSize: 12 }}>Этап:</span>
          {statusChips.map(([st, n]) => { const on = f.statuses.includes(st); const [bg, fg] = chipTone(st);
            return <button key={st} onClick={() => setF({ ...f, statuses: on ? f.statuses.filter((x) => x !== st) : [...f.statuses, st] })}
              style={{ border: `1px solid ${on ? fg : "transparent"}`, background: bg, color: fg, borderRadius: 14, padding: "3px 10px", cursor: "pointer", fontSize: 12, fontWeight: on ? 700 : 500 }}>
              {chipTitle(st)} · {n}</button>; })}
        </div>}
        <div className="row" style={{ flexWrap: "wrap", gap: 10, marginTop: 10 }}>
          {kpis.map(([t, v, c]) => (
            <div key={String(t)} style={{ flex: "1 1 130px", border: "1px solid var(--line)", borderRadius: 8, padding: "6px 10px", background: "#fff" }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: Number(v) > 0 && c === "bad" ? "var(--bad)" : Number(v) > 0 && c === "warn" ? "var(--warn)" : undefined }}>{v}</div>
              <div className="muted" style={{ fontSize: 12 }}>{t}</div></div>))}
        </div>
      </div>
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <table style={{ minWidth: 900 }}>
          {block === "won" ? (
            <thead><tr>{th("number", "Сделка", 120)}{th("client", "Клиент / объект")}<th>Оборудование</th>{th("machines", "Кол-во", 70)}<th>Контракт</th>{fin && th("price", "Сумма")}
              {th("progress", "Исполнение", 130)}{th("next", "Ближайший этап")}{th("status", "Статус")}{th("manager", "Менеджер")}</tr></thead>
          ) : (
            <thead><tr>{th("number", "Сделка", 120)}{th("client", "Клиент / объект")}<th>Оборудование</th>{th("machines", "Кол-во", 70)}{th("kp_due", "Срок КП", 120)}{th("priority", "Приоритет", 100)}
              {th("status", "Статус")}{th("manager", "Менеджер")}{fin && block !== "work" && th("price", "Сумма")}<th>Версия</th></tr></thead>)}
          <tbody>
            {grouped(sorted).map((d) => {
              const late = kpOverdue(d) || (d.exec?.overdue || 0) > 0;
              const rowStyle = { ...(d.parent_number ? { background: "#fafafa" } : {}), ...(late ? { boxShadow: "inset 3px 0 0 var(--accent)" } : {}) };
              return block === "won" ? (
                <tr key={d.id} style={rowStyle}>
                  {numCell(d)}{clientCell(d)}{eqCell(d)}{qtyCell(d)}
                  <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>{d.kind === "parts" ? <span className="muted">по заказу клиента</span> : d.contract_number ? <>клиент № {d.contract_number}{d.contract_signed_at && <span className="muted"> от {fmtDate(d.contract_signed_at)}</span>}</> : <span className="muted">не подписан</span>}
                    {d.kind !== "parts" && <div>{d.factory_contract_number ? <>завод № {d.factory_contract_number}{d.factory_contract_signed_at && <span className="muted"> от {fmtDate(d.factory_contract_signed_at)}</span>}</> : <span className="muted">завод — нет</span>}</div>}</td>
                  {fin && <td>{money(d)}</td>}
                  <td><Progress e={d.exec} />{d.exec?.delay_days ? <div style={{ fontSize: 11, color: d.exec.delay_days > 0 ? "var(--accent)" : "var(--muted)" }}>{d.exec.delay_days > 0 ? `+${d.exec.delay_days}` : d.exec.delay_days} дн. к плану</div> : null}</td>
                  <td style={{ fontSize: 13, minWidth: 160 }}>{d.exec?.next ? <>{d.exec.next.title}<div style={{ fontSize: 12, color: (daysTo(d.exec.next.on) ?? 1) < 0 ? "var(--accent)" : "var(--muted)" }}>
                    {fmtDate(d.exec.next.on)}{(daysTo(d.exec.next.on) ?? 1) < 0 ? ` · просрочен ${-(daysTo(d.exec.next.on) || 0)} дн.` : ""}</div></> : <span className="muted">—</span>}</td>
                  <td><StatusBadge d={d} /></td><td style={{ whiteSpace: "nowrap" }}>{d.manager_name}</td>
                </tr>) : (
                <tr key={d.id} style={rowStyle}>
                  {numCell(d)}{clientCell(d)}{eqCell(d)}{qtyCell(d)}<td><KpDue d={d} /></td><td><Prio p={d.priority} /></td>
                  <td><StatusBadge d={d} /></td><td style={{ whiteSpace: "nowrap" }}>{d.manager_name}</td>{fin && block !== "work" && <td>{money(d)}</td>}
                  <td className="muted">{d.kind === "parts" ? "" : d.current_version_no || ""}</td>
                </tr>);
            })}
            {sorted.length === 0 && <tr><td colSpan={10} className="muted" style={{ padding: 16 }}>{inBlock.length ? "Нет сделок по выбранным фильтрам" : block === "archive" ? "В архиве сделок нет" : f.type === "parts" ? "Заявок на запчасти нет — нажмите «Новая заявка на запчасти»" : "Сделок нет"}</td></tr>}
          </tbody>
        </table>
      </div>
      {me.roles.some((r) => r.code === "manager") && !me.roles.some((r) => ["cd", "fd", "gd"].includes(r.code)) && <ColleaguesDeals />}
      </div>
      </div>
    </>
  );
}
