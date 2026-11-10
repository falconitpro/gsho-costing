import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ClaimCard, ClaimsTable, NewClaimDialog, type ClaimRow } from "./ClaimsBlock";

// Реестр договоров (юридический блок, этап 1): договоры с клиентами, поставщиками, подрядчиками и прочие.
// По каждому договору обязательны условия оплаты «сколько — через сколько дней — после какого события»; неустойки — для потока и претензий.

type PayLine = { no?: number; share_pct?: number | null; amount?: number | null; event: string; offset_days: number; offset_kind: string; via?: string; event_on?: string | null; day_of_month?: number | null; note?: string };
type PerfTerm = { code: string; title?: string; event: string; offset_days: number; offset_kind: string; fixed_on?: string | null; note?: string };
type Penalty = { payer: string; reason: string; rate_kind: string; rate: number | null; base: string; grace_days: number; cap_kind: string; cap_value: number | null; term?: string; note?: string };
type Sched = { no: number; amount: number | null; currency: string; event_title: string; event_on: string | null; event_fact: boolean; event_source: string; due_on: string | null; due_legal: boolean; days_overdue: number; text: string };
type CFile = { id: string; kind: string; kind_title: string; name: string; size: number; version: number; uploaded_by_name: string; uploaded_at: string; note: string };
type Row = {
  id: string; doc_type: string; doc_type_title: string; kind: string; kind_title: string; direction: string; direction_title: string; number: string; cp_number: string; title: string; subject: string;
  is_framework?: boolean; framework?: { specs: number; signed: number; by_currency: Record<string, number>; limit: number | null; currency: string; remaining: number | null; exceeded: boolean } | null;
  status: string; status_title: string; locked: boolean; own_entity_id: string | null; own_entity_name: string; cp_kind: string; cp_id: string | null; cp_name: string; cp_inn: string;
  deal_id: string | null; deal_number: string; amount: number | null; currency: string; signed_on: string | null; valid_to: string | null; days_to_expiry: number | null; sign_method: string;
  has_scan: boolean; curator_name: string; terms_text: string; pending: string[]; exec?: { overdue: number; pen_in: number; pen_out: number };
  package?: { id: string; version_no: number; status: string; status_title: string } | null;
};
type Ob = { key: string; title: string; debtor: string; amount: number | null; currency: string; due_on: string | null; due_legal: boolean; done_on: string | null; done_source?: string; days_late: number; status: string; no?: number; idx?: number };
type Pen = { key: string; payer: string; payer_title: string; reason_title: string; obligation: string; days: number; base: number | null; rate_text: string; calc_amount: number | null; amount: number | null;
  currency: string; capped: boolean; status: string; status_title: string; mark: { comment?: string; on?: string; amount?: number } };
type Obligations = { payments: Ob[]; performance: Ob[]; penalties: Pen[]; totals: { receivable: number; payable: number; receivable_accruing: number; payable_accruing: number; net: number; currency: string } };
type Step = { code: string; title: string; decision: string | null; by_name: string; at: string | null; comment: string; pending: boolean };
type Full = Row & {
  vat_mode: string; vat_rate: number | null; price_type: string; fx_clause: string; effective_from: string | null; auto_renew: boolean; signatory: { name?: string; position?: string; basis?: string };
  payment_terms: PayLine[]; performance_terms: PerfTerm[]; penalties: Penalty[]; liability_cap_pct: number | null; security: Record<string, string>; warranty: Record<string, string>;
  law: { law?: string; court?: string; claim_days?: number; pre_trial?: boolean }; edo: { operator?: string; doc_id?: string }; original: { received_on?: string; location?: string };
  files: CFile[]; history: { at: string; by_name: string; action: string; comment: string }[]; notes: string; schedule: Sched[]; check: { ok: boolean; errors: string[]; warnings: string[] };
  children: Row[]; can_edit: boolean; can_edit_number: boolean; created_by_name: string; finance_url?: string | null;
  client_order: boolean; approval: { round?: number; submitted_at?: string; submitted_by_name?: string; steps?: Step[] }; route_preview: string[];
  sign_checklist: { code: string; title: string; ok: boolean; optional?: boolean }[]; amends: { field: string; title: string }[];
  my_steps: string[]; can_register: boolean; can_sign: boolean; can_correct?: boolean; can_delete?: boolean; obligations: Obligations | null;
};
type Catalog = { kinds: Record<string, { title: string; direction: string; cp_kind: string }>; directions: Record<string, string>; statuses: Record<string, string>; file_kinds: Record<string, string>;
  events: Record<string, string>; performance_codes: Record<string, string>; penalty_payers: Record<string, string>; penalty_reasons: Record<string, string>; rate_kinds: Record<string, string>;
  penalty_bases: Record<string, string>; cap_kinds: Record<string, string> };
type Registry = { items: Row[]; summary: Record<string, number>; entities: { id: string; name: string }[]; can_manage: boolean };

const STATUS_CLASS: Record<string, string> = { draft: "", legal_review: "info", approval: "info", approved: "info", signing: "warn", active: "ok", closed: "", terminated: "bad", cancelled: "" };
const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");
const money = (v: number | null | undefined, cur = "") => (v == null ? "—" : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}${cur ? " " + cur : ""}`);
const ACTION: Record<string, string> = { created: "создан", edited: "изменён", package: "обновлён из пакета", pull_terms: "условия из расчёта", status: "статус", file: "файл", file_removed: "файл удалён", imported: "импорт из сделки", correction: "исправление ЗГД", submitted: "на согласование", visa: "виза", recalled: "возвращён в проект", route_reset: "согласование заново", amendment: "доп. соглашение", renewed: "продлён", fact: "исполнение", claim: "претензия" };

export default function ContractsBlock({ dealId, compact }: { dealId?: string; compact?: boolean }) {
  const [reg, setReg] = useState<Registry | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [f, setF] = useState({ q: "", status: "", direction: "", kind: "" });
  const qs = new URLSearchParams(window.location.search);
  const [open, setOpen] = useState<string | null>(dealId ? null : qs.get("open"));
  const [creating, setCreating] = useState(!dealId && qs.get("new") === "1");
  const [msg, setMsg] = useState("");
  const load = () => {
    const p = new URLSearchParams({ ...f, ...(dealId ? { deal_id: dealId } : {}) });
    api.get<Registry>(`/api/legal/contracts?${p}`).then(setReg).catch((e) => setMsg((e as Error).message));
  };
  useEffect(() => { load(); }, [f.q, f.status, f.direction, f.kind, dealId]);
  useEffect(() => { api.get<Catalog>("/api/legal/contracts/catalog").then(setCat).catch(() => undefined); }, []);
  if (!reg || !cat) return <div className="card">{msg || "Загрузка…"}</div>;
  const s = reg.summary;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>{dealId ? "Договоры сделки" : "Реестр договоров"}</b>
          <div className="muted" style={{ fontSize: 12 }}>действуют: <b>{s.active || 0}</b> · проекты: {s.draft || 0}{s.expiring ? <> · <span style={{ color: "var(--warn)" }}>истекают в 30 дней: <b>{s.expiring}</b></span></> : null}
            {s.no_scan ? <> · <span style={{ color: "var(--bad)" }}>без скана: {s.no_scan}</span></> : null}{s.no_terms ? <> · <span style={{ color: "var(--bad)" }}>без условий оплаты: {s.no_terms}</span></> : null}
            {s.overdue ? <> · <span style={{ color: "var(--bad)" }}>с просрочкой: <b>{s.overdue}</b></span></> : null}
            {s.penalties_in_rub ? <> · неустойки к получению: <b>{money(s.penalties_in_rub, "₽")}</b></> : null}{s.penalties_out_rub ? <> · к уплате: <b style={{ color: "var(--bad)" }}>{money(s.penalties_out_rub, "₽")}</b></> : null}</div>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {!compact && <>
            <input placeholder="номер, контрагент, предмет…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} style={{ width: 200 }} />
            <select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}><option value="">все направления</option>{Object.entries(cat.directions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="">все виды</option>{Object.entries(cat.kinds).map(([k, v]) => <option key={k} value={k}>{v.title}</option>)}</select>
            <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">все статусы</option>{Object.entries(cat.statuses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          </>}
          <button className="btn add" onClick={() => setCreating(true)}>Договор</button>
        </div>
      </div>
      {msg && <div className="alert error" style={{ marginTop: 8 }}>{msg}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="table" style={{ marginTop: 8, fontSize: 13 }}>
          <thead><tr><th>Номер</th><th>Вид</th><th>Контрагент</th>{!compact && <th>Компания</th>}<th className="num">Сумма</th><th>Условия оплаты</th><th>Подписан</th><th>Статус</th></tr></thead>
          <tbody>
            {reg.items.length === 0 && <tr><td colSpan={8} className="muted">Договоров нет — нажмите «+ Договор». Проекты договоров сделки появляются и при формировании юридического пакета.</td></tr>}
            {reg.items.map((r) => (
              <tr key={r.id} onClick={() => setOpen(r.id)} style={{ cursor: "pointer" }}>
                <td style={{ whiteSpace: "nowrap" }}><b>{r.number || "—"}</b>{r.cp_number && <div className="muted" style={{ fontSize: 11 }}>у контрагента: {r.cp_number}</div>}{r.deal_number && <div className="muted" style={{ fontSize: 11 }}>сделка {r.deal_number}</div>}</td>
                <td>{r.kind_title}<div className="muted" style={{ fontSize: 11 }}>{r.direction === "income" ? "доходный" : r.direction === "expense" ? "расходный" : "без оплаты"}</div></td>
                <td>{r.cp_name || "—"}<div className="muted" style={{ fontSize: 11 }}>{r.subject}</div></td>
                {!compact && <td className="muted">{r.own_entity_name}</td>}
                <td className="num" style={{ whiteSpace: "nowrap" }}>{money(r.amount, r.currency)}</td>
                <td style={{ fontSize: 12, maxWidth: 280 }}>{r.terms_text || (r.direction === "none" ? <span className="muted">—</span> : <span style={{ color: "var(--bad)" }}>не заданы</span>)}</td>
                <td style={{ whiteSpace: "nowrap" }}>{d(r.signed_on)}{r.locked && !r.has_scan && <div style={{ fontSize: 11, color: "var(--bad)" }}>нет скана</div>}{r.exec?.overdue ? <div style={{ fontSize: 11, color: "var(--bad)" }}>просрочка: {r.exec.overdue}</div> : null}{r.sign_method === "edo" && <div className="muted" style={{ fontSize: 11 }}>ЭДО</div>}</td>
                <td><span className={`badge ${STATUS_CLASS[r.status] || ""}`}>{r.status_title}</span>{r.package && <div className="muted" style={{ fontSize: 11 }}>пакет v{r.package.version_no}: {r.package.status_title}</div>}{r.pending?.length > 0 && <div className="muted" style={{ fontSize: 11 }}>ждёт: {r.pending.join(", ")}</div>}{r.days_to_expiry != null && r.status === "active" && r.days_to_expiry <= 30 && <div style={{ fontSize: 11, color: "var(--warn)" }}>до {d(r.valid_to)}</div>}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {creating && <CreateDialog cat={cat} reg={reg} dealId={dealId} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); load(); setOpen(id); }} />}
      {open && <ContractCard id={open} cat={cat} onClose={() => { setOpen(null); load(); }} onOpen={setOpen} />}
    </div>
  );
}

// ------------------------------------------------------------------ новый договор
function CreateDialog({ cat, reg, dealId, onClose, onCreated }: { cat: Catalog; reg: Registry; dealId?: string; onClose: () => void; onCreated: (id: string) => void }) {
  const [kind, setKind] = useState(dealId ? "client_supply" : "services");
  const [ent, setEnt] = useState(reg.entities[0]?.id || "");
  const [cpId, setCpId] = useState("");
  const [cpName, setCpName] = useState("");
  const [cpInn, setCpInn] = useState("");
  const [subject, setSubject] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("RUB");
  const [refs, setRefs] = useState<{ id: string; name: string }[]>([]);
  const [err, setErr] = useState("");
  const cpKind = cat.kinds[kind]?.cp_kind || "other";
  useEffect(() => {
    setCpId("");
    if (cpKind === "client") api.get<{ id: string; name: string }[]>("/api/refs/clients").then(setRefs).catch(() => setRefs([]));
    else if (cpKind === "factory") api.get<{ id: string; name: string; short_name?: string }[]>("/api/refs/factories").then((r) => setRefs(r.map((x) => ({ id: x.id, name: x.short_name || x.name })))).catch(() => setRefs([]));
    else setRefs([]);
  }, [cpKind]);
  const save = () => api.post<Full>("/api/legal/contracts", { kind, own_entity_id: ent || null, cp_kind: cpKind, cp_id: cpId || null, cp_name: cpKind === "other" ? cpName : null, cp_inn: cpInn || null,
    subject, amount: amount ? Number(amount) : null, currency, deal_id: dealId || null }).then((c) => onCreated(c.id)).catch((e) => setErr((e as Error).message));
  return (
    <Modal title="Новый договор" onClose={onClose}>
      <div className="poa-body">
        {err && <div className="alert error" style={{ marginBottom: 10 }}>{err}</div>}
        <div className="poa-form">
          <F label="Вид договора" span={2}><select value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(cat.kinds).map(([k, v]) => <option key={k} value={k}>{v.title}</option>)}</select></F>
          <F label="Компания группы" span={2}><select value={ent} onChange={(e) => setEnt(e.target.value)}>{reg.entities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></F>
          {cpKind !== "other" ? <F label={cpKind === "client" ? "Клиент" : "Поставщик / завод"} span={2}><select value={cpId} onChange={(e) => setCpId(e.target.value)}><option value="">— выберите —</option>{refs.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></F>
            : <><F label="Контрагент"><input value={cpName} onChange={(e) => setCpName(e.target.value)} placeholder="ООО «…»" /></F><F label="ИНН"><input value={cpInn} onChange={(e) => setCpInn(e.target.value)} /></F></>}
          <F label="Предмет" span={2}><input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="поставка / перевозка / услуги …" /></F>
          <F label="Цена договора"><input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} /></F>
          <F label="Валюта"><select value={currency} onChange={(e) => setCurrency(e.target.value)}>{["RUB", "CNY", "USD", "EUR", "KGS"].map((c) => <option key={c}>{c}</option>)}</select></F>
        </div>
        <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>Номер присвоится сразу ({cat.kinds[kind]?.direction === "income" ? "К" : cat.kinds[kind]?.direction === "expense" ? "П" : "С"}-код компании-год-№); вы станете куратором. Условия оплаты, сроки и неустойки — в карточке; типовые неустойки подставятся автоматически.</p>
      </div>
      <div className="poa-footer"><span /><div className="row" style={{ gap: 8 }}><button className="btn ghost" onClick={onClose}>Отмена</button><button className="btn" onClick={save}>Создать</button></div></div>
    </Modal>);
}

// ------------------------------------------------------------------ карточка
type Tab = "main" | "pay" | "perf" | "exec" | "files" | "history";
function ContractCard({ id, cat, onClose, onOpen }: { id: string; cat: Catalog; onClose: () => void; onOpen: (id: string) => void }) {
  const [c, setC] = useState<Full | null>(null);
  const [draft, setDraft] = useState<Partial<Full>>({});
  const [tab, setTab] = useState<Tab>("main");
  const [err, setErr] = useState<string[]>([]);
  const [info, setInfo] = useState("");
  const [fileKind, setFileKind] = useState("draft");
  const [reason, setReason] = useState("");
  const [fix, setFix] = useState(false);            // ЗГД: исправление ошибки в подписанном договоре
  const [fixReason, setFixReason] = useState("");
  const load = () => api.get<Full>(`/api/legal/contracts/${id}`).then((x) => { setC(x); setDraft({}); }).catch((e) => setErr([(e as Error).message]));
  useEffect(() => { load(); setTab("main"); }, [id]);
  const v = useMemo(() => (c ? { ...c, ...draft } as Full : null), [c, draft]);
  if (!c || !v) return <Modal title="Договор" onClose={onClose}><div className="poa-body">{err.join("; ") || "Загрузка…"}</div></Modal>;
  const ro = (!c.can_edit || c.locked) && !fix;
  const set = (p: Partial<Full>) => setDraft({ ...draft, ...p });
  const dirty = Object.keys(draft).length > 0;
  const fail = (e: unknown) => { const det = (e as { detail?: { errors?: string[] } }).detail; setErr(det?.errors?.length ? det.errors : [(e as Error).message]); };
  const save = () => api.put<Full>(`/api/legal/contracts/${id}`, fix ? { ...draft, correction_reason: fixReason } : draft)
    .then((x) => { setC(x); setDraft({}); setErr([]); setInfo(fix ? "Исправление сохранено — записано в историю" : "Сохранено"); if (fix) { setFix(false); setFixReason(""); } }).catch(fail);
  const status = (to: string) => api.post<Full>(`/api/legal/contracts/${id}/status`, { status: to, comment: reason }).then((x) => { setC({ ...c, ...x }); setReason(""); setErr([]); setInfo(`Статус: ${x.status_title}`); }).catch(fail);
  const act = (path: string, body: object = {}) => api.post<Full>(`/api/legal/contracts/${id}/${path}`, body).then((x) => { setC(x); setDraft({}); setReason(""); setErr([]); setInfo(`Статус: ${x.status_title}`); }).catch(fail);
  const inRoute = ["legal_review", "approval", "approved", "signing"].includes(c.status);
  const pull = () => api.post<Full>(`/api/legal/contracts/${id}/pull-terms`).then((x) => { setC(x); setDraft({}); setInfo("Цена и условия оплаты перенесены из расчёта сделки"); }).catch(fail);
  const tabs: [Tab, string][] = [["main", "Основное"], ["pay", "Оплата"], ["perf", "Сроки и неустойки"], ...(c.obligations ? [["exec", "Исполнение"] as [Tab, string]] : []), ["files", c.package ? `Документы (${c.files.length} + пакет)` : `Документы (${c.files.length})`], ["history", "История"]];
  return (
    <Modal title={`${c.doc_type_title} ${c.number}${c.cp_name ? " — " + c.cp_name : ""}`} onClose={() => { if (!dirty || window.confirm("Закрыть без сохранения?")) onClose(); }} wide>
      <div className="poa-steps" style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}>
        {tabs.map(([k, t], i) => <button key={k} className={`poa-step ${tab === k ? "cur" : ""}`} onClick={() => setTab(k)}><span className="n">{i + 1}</span><span>{t}</span></button>)}
      </div>
      <div className="poa-body view">
        <div className="poa-head-line">
          <span className={`badge ${STATUS_CLASS[c.status] || ""}`}>{c.status_title}</span><span className="badge">{c.kind_title}</span><span className="badge">{c.direction_title}</span>
          <span className="muted">{c.own_entity_name}{c.deal_number ? ` · сделка ${c.deal_number}` : ""} · куратор {c.curator_name || "—"}</span>
        </div>
        {c.locked && !fix && <div className="alert info" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span>Договор подписан: условия меняются только дополнительным соглашением. Номер и дату у подписанного меняет ЗГД или КД.</span>
          {c.can_correct && <button className="btn ghost" onClick={() => setFix(true)}>Исправить ошибку в данных</button>}</div>}
        {fix && <div className="alert warn" style={{ display: "grid", gap: 6 }}>
          <span><b>Исправление подписанного договора (ЗГД).</b> Меняйте только то, что внесено с ошибкой — подписанный текст договора это не меняет. «Было / стало» и причина попадут в историю, юристы и куратор получат уведомление.</span>
          <span className="row" style={{ gap: 8 }}><input value={fixReason} onChange={(e) => setFixReason(e.target.value)} placeholder="какая ошибка исправляется (обязательно)" style={{ flex: 1 }} />
            <button className="btn ghost" onClick={() => { setFix(false); setDraft({}); setFixReason(""); }}>Отменить</button></span></div>}
        {err.length > 0 && <div className="alert error"><ul style={{ margin: 0, paddingLeft: 18 }}>{err.map((e) => <li key={e}>{e}</li>)}</ul></div>}
        {info && <div className="alert ok" onClick={() => setInfo("")}>{info}</div>}
        {!c.check.ok && !c.locked && <div className="alert warn"><b>Не готов к согласованию:</b> {c.check.errors.join("; ")}</div>}
        {inRoute && !c.locked && <div className="alert info">Договор на маршруте: условия меняют юрист или ЗГД, после виз — согласование заново. Чтобы поправить самому — «Вернуть в проект».</div>}
        {c.doc_type === "amendment" && <div className="alert info">{c.amends.length ? <>После подписания в основном договоре изменится: <b>{c.amends.map((x) => x.title).join(", ")}</b>.</> : "Заполните только то, что меняется: цену, условия оплаты, сроки, неустойки или срок действия — после подписания это перейдёт в основной договор."}</div>}
        {c.doc_type === "contract" && <div className="alert info" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={!!c.is_framework} disabled={!(c.can_edit || c.can_correct)}
            onChange={(e) => api.put<Full>(`/api/legal/contracts/${id}`, { is_framework: e.target.checked }).then((y) => setC({ ...c, ...y })).catch(fail)} />
            <b>Рамочный договор</b></label>
          <span className="muted" style={{ fontSize: 12 }}>{c.is_framework ? "сделки оформляются спецификациями к нему; сумма договора — лимит" : "разовый договор — можно сделать рамочным и во время исполнения"}</span>
          {c.is_framework && c.framework && <span style={{ fontSize: 13 }}>спецификаций: <b>{c.framework.specs}</b> (подписано {c.framework.signed}) ·{" "}
            {Object.entries(c.framework.by_currency).map(([k, x]) => money(x, k)).join(" + ") || "сумм нет"}
            {c.framework.limit != null && <> · лимит {money(c.framework.limit, c.framework.currency)}, остаток <b style={{ color: c.framework.exceeded ? "var(--bad)" : undefined }}>{money(c.framework.remaining, c.framework.currency)}</b></>}
            {c.framework.exceeded && <span className="badge bad" style={{ marginLeft: 4 }}>лимит превышен</span>}</span>}
          {c.is_framework && c.can_edit && <button className="btn ghost add" onClick={() => api.post<Full>("/api/legal/contracts", { parent_id: c.id, doc_type: "specification" }).then((x) => onOpen(x.id)).catch(fail)}>Спецификация</button>}
        </div>}
        <RouteStrip c={c} />

        {tab === "main" && (
          <div className="poa-cols">
            <Section title="Стороны">
              <div className="poa-form">
                <F label="Контрагент" span={2}><input value={v.cp_name} disabled={ro || c.cp_kind !== "other"} onChange={(e) => set({ cp_name: e.target.value })} /></F>
                <F label="ИНН контрагента"><input value={v.cp_inn || ""} disabled={ro} onChange={(e) => set({ cp_inn: e.target.value })} /></F>
                <F label="Номер у контрагента"><input value={v.cp_number || ""} disabled={c.locked ? !c.can_edit_number : ro} onChange={(e) => set({ cp_number: e.target.value })} /></F>
                <F label="Подписант с нашей стороны"><input value={v.signatory?.name || ""} disabled={ro} onChange={(e) => set({ signatory: { ...v.signatory, name: e.target.value } })} /></F>
                <F label="Основание"><input value={v.signatory?.basis || ""} disabled={ro} placeholder="Устав / доверенность № …" onChange={(e) => set({ signatory: { ...v.signatory, basis: e.target.value } })} /></F>
              </div>
            </Section>
            <Section title="Предмет и цена">
              <div className="poa-form">
                <F label="Предмет" span={2}><input value={v.subject} disabled={ro} onChange={(e) => set({ subject: e.target.value })} /></F>
                <F label="Цена договора"><input type="number" value={v.amount ?? ""} disabled={ro} onChange={(e) => set({ amount: e.target.value === "" ? null : Number(e.target.value) })} /></F>
                <F label="Валюта"><select value={v.currency} disabled={ro} onChange={(e) => set({ currency: e.target.value })}>{["RUB", "CNY", "USD", "EUR", "KGS"].map((x) => <option key={x}>{x}</option>)}</select></F>
                <F label="НДС"><select value={v.vat_mode} disabled={ro} onChange={(e) => set({ vat_mode: e.target.value })}><option value="included">включён в цену</option><option value="on_top">сверху</option><option value="none">без НДС</option></select></F>
                <F label="Тип цены"><select value={v.price_type} disabled={ro} onChange={(e) => set({ price_type: e.target.value })}><option value="fixed">фиксированная</option><option value="by_spec">по спецификациям</option><option value="framework">рамочный лимит</option></select></F>
                {v.direction === "expense" && <label className="row" style={{ gridColumn: "span 2", gap: 8, fontSize: 13 }}><input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={!!v.client_order} disabled={ro} onChange={(e) => set({ client_order: e.target.checked })} />закупка под заказ клиента (ГШО или запчасти) — согласует КД</label>}
                <F label="Валютная оговорка" span={2}><input value={v.fx_clause || ""} disabled={ro} placeholder="по курсу ЦБ на дату оплаты ± …" onChange={(e) => set({ fx_clause: e.target.value })} /></F>
              </div>
            </Section>
            <Section title="Срок действия и подписание">
              <div className="poa-form">
                <F label="Дата подписания"><input type="date" value={v.signed_on || ""} disabled={c.locked ? !c.can_edit_number : ro} onChange={(e) => set({ signed_on: e.target.value || null })} /></F>
                <F label="Действует до"><input type="date" value={v.valid_to || ""} disabled={ro} onChange={(e) => set({ valid_to: e.target.value || null })} /></F>
                <F label="Способ подписания"><select value={v.sign_method} disabled={ro} onChange={(e) => set({ sign_method: e.target.value })}><option value="paper">на бумаге</option><option value="edo">в ЭДО</option></select></F>
                <F label="Оператор ЭДО"><input value={v.edo?.operator || ""} disabled={v.sign_method !== "edo" || (ro && !c.can_edit)} placeholder="Диадок, Контур, СБИС" onChange={(e) => set({ edo: { ...v.edo, operator: e.target.value } })} /></F>
                <F label="Оригинал получен"><input type="date" value={v.original?.received_on || ""} disabled={!c.can_edit} onChange={(e) => set({ original: { ...v.original, received_on: e.target.value } })} /></F>
                <F label="Где хранится оригинал"><input value={v.original?.location || ""} disabled={!c.can_edit} onChange={(e) => set({ original: { ...v.original, location: e.target.value } })} /></F>
              </div>
              <div style={{ marginTop: 10, fontSize: 13 }}>{c.sign_checklist.map((x) => <div key={x.code} style={{ color: x.ok ? "var(--ok)" : x.optional ? "var(--muted)" : "var(--ink)" }}>{x.ok ? "✓" : "○"} {x.title}{x.optional && !x.ok ? " (желательно)" : ""}</div>)}</div>
            </Section>
            <Section title="Гарантия, обеспечение, споры">
              <div className="poa-form">
                <F label="Гарантия на товар, мес."><input type="number" value={v.warranty?.months || ""} disabled={ro} onChange={(e) => set({ warranty: { ...v.warranty, months: e.target.value } })} /></F>
                <F label="Обеспечение"><select value={v.security?.kind || ""} disabled={ro} onChange={(e) => set({ security: { ...v.security, kind: e.target.value } })}><option value="">нет</option><option value="bank_guarantee">банковская гарантия</option><option value="lc">аккредитив</option><option value="retention">гарантийное удержание</option></select></F>
                <F label="Срок ответа на претензию, дн."><input type="number" value={v.law?.claim_days ?? ""} disabled={ro} onChange={(e) => set({ law: { ...v.law, claim_days: e.target.value === "" ? undefined : Number(e.target.value) } })} /></F>
                <F label="Суд / арбитраж"><input value={v.law?.court || ""} disabled={ro} onChange={(e) => set({ law: { ...v.law, court: e.target.value } })} /></F>
                <F label="Примечание" span={2}><textarea rows={2} value={v.notes || ""} disabled={!c.can_edit} onChange={(e) => set({ notes: e.target.value })} /></F>
              </div>
            </Section>
            {c.children.length > 0 && (
              <div className="poa-section wide"><div className="poa-section-head"><b>Доп. соглашения и спецификации</b></div>
                {c.children.map((x) => <div key={x.id} className="poa-kv" style={{ cursor: "pointer" }} onClick={() => onOpen(x.id)}><span className="muted">{x.doc_type_title}</span><span><b>{x.number}</b> · {x.subject || "—"} · {money(x.amount, x.currency)} · <span className={`badge ${STATUS_CLASS[x.status] || ""}`}>{x.status_title}</span></span></div>)}
              </div>)}
          </div>)}

        {tab === "pay" && <PayTab v={v} c={c} cat={cat} ro={ro} set={set} pull={pull} />}
        {tab === "perf" && <PerfTab v={v} cat={cat} ro={ro} set={set} />}

        {tab === "exec" && c.obligations && <ExecTab c={c} ob={c.obligations} onSaved={(x) => { setC(x); setErr([]); setInfo("Записано"); }} onErr={fail} />}
        {tab === "files" && <PackagesSection contractId={c.id} />}
        {tab === "files" && (
          <div className="poa-section" style={{ marginTop: 14 }}>
            <div className="poa-section-head" style={{ justifyContent: "space-between" }}>
              <span><b>Файлы договора</b> {c.locked && !c.has_scan && <span style={{ color: "var(--bad)", fontSize: 12 }}>· нет подписанного скана</span>}</span>
              <span className="row" style={{ gap: 8 }}>
                <select value={fileKind} onChange={(e) => setFileKind(e.target.value)}>{Object.entries(cat.file_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                <label className="btn ghost">Загрузить<input type="file" style={{ display: "none" }} onChange={(e) => { const fl = e.target.files?.[0]; if (fl) api.upload<Full>(`/api/legal/contracts/${id}/files`, fl, { kind: fileKind }).then((x) => setC({ ...c, ...x })).catch(fail); e.target.value = ""; }} /></label>
              </span>
            </div>
            {c.files.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Файлов нет. Для статуса «действует» нужен подписанный скан (и при подписании в ЭДО — тоже).</div> : (
              <table className="table poa-files"><thead><tr><th>Вид</th><th>Файл</th><th>Версия</th><th>Загрузил</th><th>Дата</th><th /></tr></thead>
                <tbody>{c.files.map((x) => (
                  <tr key={x.id}><td>{x.kind_title}</td><td><a href={`/api/legal/contracts/${id}/files/${x.id}`} target="_blank" rel="noreferrer">{x.name}</a></td><td>v{x.version}</td><td>{x.uploaded_by_name}</td><td>{d(x.uploaded_at)}</td>
                    <td style={{ textAlign: "right" }}>{c.can_edit && !(c.locked && ["signed_scan", "edo"].includes(x.kind)) && <button className="btn ghost" style={{ padding: "0 8px" }} title="удалить" onClick={() => { if (window.confirm(`Удалить ${x.name}?`)) api.del<Full>(`/api/legal/contracts/${id}/files/${x.id}`).then((y) => setC({ ...c, ...y })).catch(fail); }}>×</button>}</td></tr>))}
                </tbody></table>)}
          </div>)}

        {tab === "history" && (
          <div className="poa-section"><ul className="poa-history" style={{ marginTop: 0 }}>{c.history.map((h, i) => <li key={i}><span className="muted">{new Date(h.at).toLocaleString("ru-RU")}</span> · {h.by_name || "система"} · {ACTION[h.action] || h.action}{h.comment ? ` — ${h.comment}` : ""}</li>)}</ul>
            <div className="muted" style={{ fontSize: 12 }}>Создал: {c.created_by_name || "система"}</div></div>)}
      </div>

      <div className="poa-footer">
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {c.can_delete && <button className="btn ghost" style={{ color: "var(--bad)" }} title="Проект заведён по ошибке — удалить из реестра (снимок останется в журнале)"
            onClick={() => { if (window.confirm(`Удалить проект ${c.doc_type_title.toLowerCase()}а ${c.number}${c.cp_name ? " — " + c.cp_name : ""}? Восстановить из системы нельзя, снимок останется в журнале.`))
              api.del<{ deleted: string }>(`/api/legal/contracts/${id}`).then(() => onClose()).catch(fail); }}>Удалить проект</button>}
          {c.status === "draft" && c.can_edit && <button className="btn" disabled={dirty} title={dirty ? "сначала сохраните" : ""} onClick={() => act("submit")}>На согласование</button>}
          {c.my_steps.length > 0 && <>
            <button className="btn" onClick={() => act("visa", { decision: "approve", comment: reason })}>Согласовать ({c.my_steps.join(", ")})</button>
            <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => act("visa", { decision: "reject", comment: reason })}>На доработку</button>
          </>}
          {inRoute && c.can_edit && <button className="btn ghost" onClick={() => act("recall", { comment: reason })}>Вернуть в проект</button>}
          {c.status === "approved" && c.can_sign && <button className="btn" onClick={() => status("signing")}>Передать на подпись</button>}
          {["approved", "signing"].includes(c.status) && c.can_sign && <button className="btn ghost" onClick={() => status("active")}>Отметить подписанным</button>}
          {["draft", "legal_review", "approval"].includes(c.status) && c.can_register && <button className="btn ghost" title="договор уже подписан вне системы — регистрация без маршрута" onClick={() => status("active")}>Зарегистрировать подписанный</button>}
          {c.status === "active" && <button className="btn ghost" onClick={() => status("closed")}>Исполнен</button>}
          {(c.status === "active" || (!c.locked && c.status !== "cancelled")) && <>
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="комментарий / причина" style={{ width: 240 }} />
            {c.status === "active" ? <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => status("terminated")}>Расторгнут</button>
              : <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => status("cancelled")}>Аннулировать</button>}
          </>}
          {c.doc_type === "contract" && c.status === "active" && <button className="btn ghost add" onClick={() => api.post<Full>("/api/legal/contracts", { parent_id: c.id, doc_type: "amendment" }).then((x) => onOpen(x.id)).catch(fail)}>Доп. соглашение</button>}
        </div>
        <div className="row" style={{ gap: 8 }}>
          {dirty && <button className="btn ghost" onClick={() => setDraft({})}>Отменить изменения</button>}
          <button className="btn" disabled={!dirty || (fix && fixReason.trim().length < 5)} title={fix && fixReason.trim().length < 5 ? "укажите, какая ошибка исправляется" : ""} onClick={save}>{fix ? "Сохранить исправление" : "Сохранить"}</button>
        </div>
      </div>
    </Modal>
  );
}

function PayTab({ v, c, cat, ro, set, pull }: { v: Full; c: Full; cat: Catalog; ro: boolean; set: (p: Partial<Full>) => void; pull: () => void }) {
  const lines = v.payment_terms;
  const upd = (i: number, p: Partial<PayLine>) => set({ payment_terms: lines.map((l, j) => (j === i ? { ...l, ...p } : l)) });
  const sum = lines.reduce((s, l) => s + (Number(l.share_pct) || 0), 0);
  return (
    <>
      <div className="poa-section">
        <div className="poa-section-head" style={{ justifyContent: "space-between" }}>
          <span><b>Условия оплаты</b> <span className="muted">— сколько, через сколько дней, после какого события</span></span>
          {c.deal_id && !ro && <button className="btn ghost" onClick={pull}>Взять из расчёта сделки</button>}
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="table" style={{ fontSize: 13, minWidth: 760 }}>
            <thead><tr><th style={{ width: 28 }}>№</th><th style={{ width: 80 }}>Доля, %</th><th style={{ width: 120 }}>или сумма</th><th>После события</th><th style={{ width: 80 }}>Через, дн.</th><th style={{ width: 130 }}>Дни</th><th style={{ width: 130 }}>Дата события</th><th style={{ width: 28 }} /></tr></thead>
            <tbody>{lines.map((l, i) => (
              <tr key={i}>
                <td className="muted">{i + 1}</td>
                <td><input type="number" disabled={ro} value={l.share_pct ?? ""} onChange={(e) => upd(i, { share_pct: e.target.value === "" ? null : Number(e.target.value), amount: null })} /></td>
                <td><input type="number" disabled={ro} value={l.amount ?? ""} onChange={(e) => upd(i, { amount: e.target.value === "" ? null : Number(e.target.value), share_pct: null })} /></td>
                <td><select disabled={ro} value={l.event} onChange={(e) => upd(i, { event: e.target.value })}>{Object.entries(cat.events).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></td>
                <td>{l.event === "monthly" ? <input type="number" disabled={ro} value={l.day_of_month ?? 1} title="число месяца" onChange={(e) => upd(i, { day_of_month: Number(e.target.value) })} /> : <input type="number" disabled={ro || l.event === "fixed_date"} value={l.offset_days} onChange={(e) => upd(i, { offset_days: Number(e.target.value) })} />}</td>
                <td><select disabled={ro || ["fixed_date", "monthly"].includes(l.event)} value={l.offset_kind || "calendar"} onChange={(e) => upd(i, { offset_kind: e.target.value })}><option value="calendar">календарные</option><option value="working">рабочие</option></select></td>
                <td><input type="date" disabled={ro && !c.can_edit} value={l.event_on || ""} title={l.event === "fixed_date" ? "дата платежа" : "дата события, если её нет в этапах сделки"} onChange={(e) => upd(i, { event_on: e.target.value || null })} /></td>
                <td>{!ro && <button className="btn ghost" style={{ padding: "0 6px" }} title="удалить" onClick={() => set({ payment_terms: lines.filter((_, j) => j !== i) })}>×</button>}</td>
              </tr>))}
              {lines.length === 0 && <tr><td colSpan={8} className="muted">Условия оплаты не заданы{v.direction === "none" ? " (для договора без оплаты не нужны)" : " — для доходных и расходных договоров обязательны"}.</td></tr>}</tbody>
          </table>
        </div>
        {!ro && <div className="row" style={{ gap: 8, marginTop: 6 }}><button className="btn ghost add" onClick={() => set({ payment_terms: [...lines, { share_pct: Math.max(0, 100 - sum), event: "contract", offset_days: 5, offset_kind: "working", via: "transfer" }] })}>Платёж</button>
          {lines.some((l) => l.share_pct != null) && <span className={`badge ${Math.abs(sum - 100) < 0.01 ? "ok" : "bad"}`}>итого {sum} %</span>}</div>}
      </div>
      <div className="poa-section" style={{ marginTop: 14 }}>
        <div className="poa-section-head"><b>График платежей</b><span className="muted">срок считается от плановой даты события; юридически значимый — от фактической (тогда же считается просрочка)</span>{c.finance_url && <Link to={c.finance_url} style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>Оплаты в «Финансах» →</Link>}</div>
        <table className="table" style={{ fontSize: 13 }}><thead><tr><th>№</th><th className="num">Сумма</th><th>Событие</th><th>Дата события</th><th>Срок оплаты</th><th>Просрочка</th></tr></thead>
          <tbody>{c.schedule.map((s) => (
            <tr key={s.no}><td>{s.no}</td><td className="num">{money(s.amount, s.currency)}</td><td>{s.event_title}<div className="muted" style={{ fontSize: 11 }}>{s.text}</div></td>
              <td>{d(s.event_on)} {s.event_on && <span className={`badge ${s.event_fact ? "ok" : ""}`} style={{ fontSize: 10 }}>{s.event_fact ? "факт" : "план"}</span>}<div className="muted" style={{ fontSize: 11 }}>{s.event_source}</div></td>
              <td><b>{d(s.due_on)}</b></td><td style={{ color: s.days_overdue ? "var(--bad)" : undefined }}>{s.days_overdue ? `${s.days_overdue} дн.` : "—"}</td></tr>))}
            {c.schedule.length === 0 && <tr><td colSpan={6} className="muted">Сохраните условия оплаты — график построится по ним.</td></tr>}</tbody></table>
      </div>
    </>);
}

function PerfTab({ v, cat, ro, set }: { v: Full; cat: Catalog; ro: boolean; set: (p: Partial<Full>) => void }) {
  const terms = v.performance_terms, pens = v.penalties;
  const updT = (i: number, p: Partial<PerfTerm>) => set({ performance_terms: terms.map((t, j) => (j === i ? { ...t, ...p } : t)) });
  const updP = (i: number, p: Partial<Penalty>) => set({ penalties: pens.map((t, j) => (j === i ? { ...t, ...p } : t)) });
  return (
    <>
      <div className="poa-section">
        <div className="poa-section-head"><b>Сроки исполнения</b><span className="muted">контрольные сроки для неустоек: поставка, готовность, работы</span></div>
        <table className="table" style={{ fontSize: 13 }}><thead><tr><th>Что</th><th>После события</th><th style={{ width: 90 }}>Через, дн.</th><th style={{ width: 130 }}>Дни</th><th style={{ width: 140 }}>или дата</th><th>Как в договоре</th><th style={{ width: 28 }} /></tr></thead>
          <tbody>{terms.map((t, i) => (
            <tr key={i}>
              <td><select disabled={ro} value={t.code} onChange={(e) => updT(i, { code: e.target.value })}>{Object.entries(cat.performance_codes).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
              <td><select disabled={ro} value={t.event} onChange={(e) => updT(i, { event: e.target.value })}>{Object.entries(cat.events).filter(([k]) => !["monthly"].includes(k)).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
              <td><input type="number" disabled={ro} value={t.offset_days} onChange={(e) => updT(i, { offset_days: Number(e.target.value) })} /></td>
              <td><select disabled={ro} value={t.offset_kind} onChange={(e) => updT(i, { offset_kind: e.target.value })}><option value="calendar">календарные</option><option value="working">рабочие</option></select></td>
              <td><input type="date" disabled={ro} value={t.fixed_on || ""} onChange={(e) => updT(i, { fixed_on: e.target.value || null })} /></td>
              <td><input disabled={ro} value={t.note || ""} onChange={(e) => updT(i, { note: e.target.value })} /></td>
              <td>{!ro && <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => set({ performance_terms: terms.filter((_, j) => j !== i) })}>×</button>}</td>
            </tr>))}
            {terms.length === 0 && <tr><td colSpan={7} className="muted">Сроки не заданы.</td></tr>}</tbody></table>
        {!ro && <button className="btn ghost add" style={{ marginTop: 6 }} onClick={() => set({ performance_terms: [...terms, { code: "delivery", event: "contract", offset_days: 90, offset_kind: "calendar" }] })}>Срок</button>}
      </div>
      <div className="poa-section" style={{ marginTop: 14 }}>
        <div className="poa-section-head"><b>Неустойки и штрафы</b><span className="muted">неустойка = база × ставка × (дни просрочки − льготный период), но не больше предела</span></div>
        <div style={{ overflowX: "auto" }}>
          <table className="table" style={{ fontSize: 13, minWidth: 900 }}><thead><tr><th>Кто платит</th><th>За что</th><th>Ставка</th><th style={{ width: 80 }}>Значение</th><th>База</th><th style={{ width: 70 }}>Льгота, дн.</th><th>Предел</th><th style={{ width: 80 }}>Предел, знач.</th><th style={{ width: 28 }} /></tr></thead>
            <tbody>{pens.map((p, i) => (
              <tr key={i}>
                <td><select disabled={ro} value={p.payer} onChange={(e) => updP(i, { payer: e.target.value })}>{Object.entries(cat.penalty_payers).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
                <td><select disabled={ro} value={p.reason} onChange={(e) => updP(i, { reason: e.target.value })}>{Object.entries(cat.penalty_reasons).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
                <td><select disabled={ro} value={p.rate_kind} onChange={(e) => updP(i, { rate_kind: e.target.value })}>{Object.entries(cat.rate_kinds).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
                <td><input type="number" step="0.01" disabled={ro} value={p.rate ?? ""} onChange={(e) => updP(i, { rate: e.target.value === "" ? null : Number(e.target.value) })} /></td>
                <td><select disabled={ro} value={p.base} onChange={(e) => updP(i, { base: e.target.value })}>{Object.entries(cat.penalty_bases).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
                <td><input type="number" disabled={ro} value={p.grace_days} onChange={(e) => updP(i, { grace_days: Number(e.target.value) })} /></td>
                <td><select disabled={ro} value={p.cap_kind} onChange={(e) => updP(i, { cap_kind: e.target.value })}>{Object.entries(cat.cap_kinds).map(([k, x]) => <option key={k} value={k}>{x}</option>)}</select></td>
                <td><input type="number" disabled={ro || p.cap_kind === "none"} value={p.cap_value ?? ""} onChange={(e) => updP(i, { cap_value: e.target.value === "" ? null : Number(e.target.value) })} /></td>
                <td>{!ro && <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => set({ penalties: pens.filter((_, j) => j !== i) })}>×</button>}</td>
              </tr>))}
              {pens.length === 0 && <tr><td colSpan={9} className="muted">Неустойки не заданы — просрочки не попадут в расчёт и претензии.</td></tr>}</tbody></table>
        </div>
        <div className="row" style={{ gap: 12, marginTop: 6, flexWrap: "wrap" }}>
          {!ro && <button className="btn ghost add" onClick={() => set({ penalties: [...pens, { payer: "cp", reason: "late_payment", rate_kind: "pct_day", rate: 0.1, base: "overdue_amount", grace_days: 0, cap_kind: "none", cap_value: null }] })}>Неустойка</button>}
          <label className="row" style={{ gap: 6, fontSize: 13 }}>Общий предел ответственности, % цены <input type="number" disabled={ro} style={{ width: 80 }} value={v.liability_cap_pct ?? ""} onChange={(e) => set({ liability_cap_pct: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        </div>
      </div>
    </>);
}

function F({ label, children, span }: { label: string; children: ReactNode; span?: number }) {
  return <label className="f" style={span ? { gridColumn: `span ${span}` } : undefined}><span>{label}</span>{children}</label>;
}
function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="poa-section"><div className="poa-section-head"><b>{title}</b></div>{children}</div>;
}
function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 50, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflow: "auto" }}>
      <div className="card poa-modal" onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: wide ? 1120 : 640 }}>
        <div className="poa-modal-head"><b>{title}</b><button className="btn ghost" onClick={onClose} title="закрыть">✕</button></div>
        {children}
      </div>
    </div>);
}

function RouteStrip({ c }: { c: Full }) {
  const steps = c.approval?.steps;
  if (!steps?.length) {
    if (c.status !== "draft" || !c.route_preview?.length) return null;
    return <div className="muted" style={{ fontSize: 12, margin: "0 0 12px" }}>Маршрут согласования: {c.route_preview.join(" → ")}</div>;
  }
  return (
    <div className="poa-section" style={{ marginBottom: 12 }}>
      <div className="poa-section-head"><b>Согласование</b><span className="muted">круг {c.approval.round || 1}{c.approval.submitted_by_name ? ` · отправил ${c.approval.submitted_by_name}` : ""}{c.approval.submitted_at ? ` ${d(c.approval.submitted_at)}` : ""}</span></div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${steps.length}, 1fr)`, gap: 8 }}>
        {steps.map((s) => {
          const tone = s.decision === "approve" ? "ok" : s.decision === "reject" ? "bad" : s.pending ? "warn" : "";
          return (
            <div key={s.code} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", fontSize: 13, background: tone === "ok" ? "var(--ok-bg)" : tone === "warn" ? "var(--warn-bg, #fff7e6)" : tone === "bad" ? "var(--bad-bg, #fdecec)" : "#fff" }}>
              <b>{s.title}</b>
              <div className="muted" style={{ fontSize: 12 }}>{s.decision === "approve" ? `согласовал ${s.by_name}` : s.decision === "reject" ? `на доработку · ${s.by_name}` : s.pending ? "ждёт визы" : "после юриста"}{s.at ? ` · ${d(s.at)}` : ""}</div>
              {s.comment && <div style={{ fontSize: 12, marginTop: 2 }}>{s.comment}</div>}
            </div>);
        })}
      </div>
    </div>);
}

const OB_STATUS: Record<string, [string, string]> = { paid: ["оплачено", "ok"], done: ["исполнено", "ok"], overdue: ["просрочено", "bad"], due: ["ожидается", ""], planned: ["по плану", ""] };
const today = () => new Date().toISOString().slice(0, 10);

function ExecTab({ c, ob, onSaved, onErr }: { c: Full; ob: Obligations; onSaved: (x: Full) => void; onErr: (e: unknown) => void }) {
  const [dates, setDates] = useState<Record<string, string>>({});
  const [amt, setAmt] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const post = (body: object) => api.post<Full>(`/api/legal/contracts/${c.id}/facts`, body).then(onSaved).catch(onErr);
  const side = (x: string) => (x === "cp" ? "контрагент" : "мы");
  const t = ob.totals;
  type Av = { kind: string; key?: string; title: string; amount: number | null; days?: number };
  const [claims, setClaims] = useState<{ items: ClaimRow[]; available_out: Av[]; available_in: Av[] } | null>(null);
  const [newClaim, setNewClaim] = useState<"outgoing" | "incoming" | null>(null);
  const [openClaim, setOpenClaim] = useState<string | null>(null);
  const loadClaims = () => api.get<{ items: ClaimRow[]; available_out: Av[]; available_in: Av[] }>(`/api/legal/contracts/${c.id}/claims`).then(setClaims).catch(() => setClaims(null));
  useEffect(() => { loadClaims(); }, [c.id, ob]);
  const obTable = (rows: Ob[], kind: "pay" | "perf") => (
    <table className="table" style={{ fontSize: 13 }}>
      <thead><tr><th>Обязательство</th><th>Кто должен</th><th className="num">Сумма</th><th>Срок</th><th>Исполнено</th><th>Статус</th><th style={{ width: 220 }} /></tr></thead>
      <tbody>{rows.map((o) => {
        const k = kind === "pay" ? String(o.no) : String(o.idx);
        const [st, tone] = OB_STATUS[o.status] || [o.status, ""];
        return (
          <tr key={o.key}>
            <td>{o.title}</td><td>{side(o.debtor)}</td><td className="num" style={{ whiteSpace: "nowrap" }}>{money(o.amount, o.currency)}</td>
            <td style={{ whiteSpace: "nowrap" }}>{d(o.due_on)}{o.due_on && !o.due_legal && <div className="muted" style={{ fontSize: 11 }}>от плановой даты</div>}</td>
            <td style={{ whiteSpace: "nowrap" }}>{d(o.done_on)}{o.done_source && <div className="muted" style={{ fontSize: 11 }}>{o.done_source}</div>}</td>
            <td><span className={`badge ${tone}`}>{st}</span>{o.days_late > 0 && <div style={{ fontSize: 11, color: "var(--bad)" }}>{o.days_late} дн.</div>}</td>
            <td style={{ textAlign: "right" }}>{o.done_on && (o.done_source === "отмечено вручную" || kind === "pay")
              ? <button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => post({ kind, key: k, clear: true })}>снять отметку</button>
              : <span className="row" style={{ gap: 4, justifyContent: "flex-end" }}><input type="date" style={{ width: 130 }} value={dates[o.key] || today()} onChange={(e) => setDates({ ...dates, [o.key]: e.target.value })} />
                <button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => post(kind === "pay" ? { kind, key: k, paid_on: dates[o.key] || today() } : { kind, key: k, done_on: dates[o.key] || today() })}>{kind === "pay" ? "оплачено" : "исполнено"}</button></span>}</td>
          </tr>);
      })}
        {rows.length === 0 && <tr><td colSpan={7} className="muted">{kind === "pay" ? "Условия оплаты не заданы." : "Сроки исполнения не заданы — неустойку за просрочку поставки не рассчитать."}</td></tr>}</tbody>
    </table>);
  return (
    <>
      <div className="wp-grid2" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 12 }}>
        <div className="wp-mini"><b>{money(t.receivable, t.currency)}</b><small>неустойки к получению</small></div>
        <div className="wp-mini"><b>{money(t.receivable_accruing, t.currency)}</b><small>ещё начисляются нам</small></div>
        <div className="wp-mini"><b style={{ color: t.payable ? "var(--bad)" : undefined }}>{money(t.payable, t.currency)}</b><small>неустойки к уплате</small></div>
        <div className="wp-mini"><b style={{ color: t.payable_accruing ? "var(--warn)" : undefined }}>{money(t.payable_accruing, t.currency)}</b><small>ещё начисляются нам к уплате</small></div>
      </div>
      <div className="poa-section"><div className="poa-section-head"><b>Оплата</b><span className="muted">просрочка считается от фактической даты события</span></div>{obTable(ob.payments, "pay")}</div>
      <div className="poa-section" style={{ marginTop: 14 }}><div className="poa-section-head"><b>Сроки исполнения</b><span className="muted">факт поставки, готовности, отгрузки, ПНР берётся из этапов сделки</span></div>{obTable(ob.performance, "perf")}</div>
      <div className="poa-section" style={{ marginTop: 14 }}>
        <div className="poa-section-head" style={{ justifyContent: "space-between" }}><span><b>Неустойки</b> <span className="muted">— в себестоимость сделки и денежный поток (предъявленные и признанные)</span></span>
          <input placeholder="комментарий / причина" value={note} onChange={(e) => setNote(e.target.value)} style={{ width: 240 }} /></div>
        <div style={{ overflowX: "auto" }}>
          <table className="table" style={{ fontSize: 13, minWidth: 900 }}>
            <thead><tr><th>Кто платит</th><th>За что</th><th className="num">Дней</th><th className="num">База</th><th>Ставка</th><th className="num">Сумма</th><th>Статус</th><th style={{ width: 300 }} /></tr></thead>
            <tbody>{ob.penalties.map((p) => (
              <tr key={p.key}>
                <td>{p.payer_title}</td><td>{p.reason_title}<div className="muted" style={{ fontSize: 11 }}>{p.obligation}</div></td><td className="num">{p.days}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>{money(p.base, p.currency)}</td><td style={{ fontSize: 12 }}>{p.rate_text}</td>
                <td className="num" style={{ whiteSpace: "nowrap" }}><b>{money(p.amount, p.currency)}</b>{p.capped && <div className="muted" style={{ fontSize: 11 }}>ограничено пределом</div>}{p.calc_amount !== p.amount && p.calc_amount != null && <div className="muted" style={{ fontSize: 11 }}>расчёт {money(p.calc_amount)}</div>}</td>
                <td><span className={`badge ${p.status === "waived" ? "" : p.status === "paid" ? "ok" : p.payer === "us" ? "bad" : "warn"}`}>{p.status_title}</span>{p.mark?.comment && <div className="muted" style={{ fontSize: 11 }}>{p.mark.comment}</div>}</td>
                <td style={{ textAlign: "right" }}><span className="row" style={{ gap: 4, justifyContent: "flex-end", flexWrap: "wrap" }}>
                  <input type="number" title="сумма (если отличается от расчёта)" placeholder="сумма" style={{ width: 90 }} value={amt[p.key] ?? ""} onChange={(e) => setAmt({ ...amt, [p.key]: e.target.value })} />
                  {[["claimed", "предъявлена"], ["recognized", "признана"], ["paid", "оплачена"], ["waived", "не взыскиваем"]].filter(([k]) => k !== p.status).map(([k, tt]) => (
                    <button key={k} className="btn ghost" style={{ padding: "0 6px", fontSize: 12 }} onClick={() => post({ kind: "pen", key: p.key, status: k, amount: amt[p.key] ? Number(amt[p.key]) : null, comment: note })}>{tt}</button>))}
                  {p.mark?.on && <button className="btn ghost" style={{ padding: "0 6px", fontSize: 12 }} title="снять решение" onClick={() => post({ kind: "pen", key: p.key, clear: true })}>×</button>}
                </span></td>
              </tr>))}
              {ob.penalties.length === 0 && <tr><td colSpan={8} className="muted">Просрочек нет — неустойки не начислены.</td></tr>}</tbody>
          </table>
        </div>
      </div>
      <div className="poa-section" style={{ marginTop: 14 }}>
        <div className="poa-section-head" style={{ justifyContent: "space-between" }}><span><b>Претензии</b> <span className="muted">— черновик исходящей создаётся сам на 5-й день просрочки контрагента</span></span>
          <span className="row" style={{ gap: 6 }}>
            <button className="btn ghost add" onClick={() => setNewClaim("outgoing")}>Претензия контрагенту</button>
            <button className="btn ghost add" onClick={() => setNewClaim("incoming")}>Входящая претензия</button>
          </span></div>
        <ClaimsTable items={claims?.items || []} onOpen={setOpenClaim} empty="Претензий по договору нет." />
      </div>
      {newClaim && claims && <NewClaimDialog contractId={c.id} direction={newClaim} available={newClaim === "outgoing" ? claims.available_out : claims.available_in} onClose={() => setNewClaim(null)} onCreated={(cid) => { setNewClaim(null); loadClaims(); setOpenClaim(cid); }} />}
      {openClaim && <ClaimCard id={openClaim} onClose={() => { setOpenClaim(null); loadClaims(); }} />}
    </>);
}

// ------------------------------------------------------------------ юридические пакеты сделки внутри карточки договора
type PkgFile = { kind: string; title: string; file_name: string; format?: string };
type Pkg = { id: string; deal_id: string; kind: string; version_no: number; deal_version_no: number | null; status: string; status_title: string; language: string; files: PkgFile[];
  approvals: { at: string; by_name: string; to: string; comment: string }[]; created_by_name: string; created_at: string; checks: { errors?: string[]; warnings?: string[] };
  actions: { to: string; title: string }[]; can_scan: boolean; linked: boolean };
const PKG_TONE: Record<string, string> = { draft: "warn", needs_data: "warn", lawyer_review: "info", commercial_review: "info", approved: "ok", signed: "ok", superseded: "", archived: "" };

function PackagesSection({ contractId }: { contractId: string }) {
  const [data, setData] = useState<{ items: Pkg[]; deal_id: string | null } | null>(null);
  const [old, setOld] = useState(false);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ items: Pkg[]; deal_id: string | null }>(`/api/legal/contracts/${contractId}/packages`).then(setData).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [contractId]);
  if (!data) return null;
  const items = data.items.filter((p) => old || !["superseded", "archived"].includes(p.status));
  const hidden = data.items.length - items.length;
  const act = (p: Pkg, to: string) => api.post(`/api/legal/packages/${p.id}/status`, { status: to, comment: note }).then(() => { setNote(""); setMsg(""); load(); }).catch((e) => setMsg((e as Error).message));
  return (
    <div className="poa-section">
      <div className="poa-section-head" style={{ justifyContent: "space-between" }}>
        <span><b>Юридический пакет документов</b> <span className="muted">— сформирован по сделке: договор и приложения (закупка) или рабочая копия, протокол разногласий, техспецификация (продажа)</span></span>
        {data.deal_id && <a className="btn ghost" href={`/deals/${data.deal_id}?tab=legal`}>{data.items.length ? "Новая версия в сделке" : "Сформировать в сделке"}</a>}
      </div>
      {msg && <div className="alert error">{msg}</div>}
      {items.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>{data.deal_id ? "Пакета пока нет — его формируют на вкладке «Юридические документы» сделки." : "Договор не привязан к сделке — пакета нет; документы загружаются ниже."}</div> : items.map((p) => (
        <div key={p.id} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", marginBottom: 8, opacity: ["superseded", "archived"].includes(p.status) ? 0.6 : 1 }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <span><b>Версия {p.version_no}</b> <span className={`badge ${PKG_TONE[p.status] || ""}`}>{p.status_title}</span>{p.linked && <span className="badge" style={{ marginLeft: 4 }}>условия договора взяты отсюда</span>}
              <span className="muted" style={{ fontSize: 12 }}> · {p.created_by_name} {d(p.created_at)}{p.deal_version_no ? ` · расчёт v${p.deal_version_no}` : ""}</span></span>
            <span className="row" style={{ gap: 6 }}><a className="btn ghost" href={`/api/legal/packages/${p.id}/zip`}>ZIP</a></span>
          </div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
            {p.files.map((f) => (
              <span key={f.kind + f.file_name} className="badge" style={{ fontSize: 12 }}>{f.title}:{" "}
                <a href={`/api/legal/packages/${p.id}/files/${f.kind}`}>{f.kind === "signed_scan" || (f.format && f.format !== "docx") ? "открыть" : "DOCX"}</a>
                {f.kind !== "signed_scan" && (!f.format || f.format === "docx") && <> · <a href={`/api/legal/packages/${p.id}/files/${f.kind}?fmt=pdf`} target="_blank" rel="noreferrer">PDF</a></>}</span>))}
          </div>
          {(p.checks?.errors?.length || 0) > 0 && <div style={{ fontSize: 12, color: "var(--bad)", marginTop: 6 }}>Проверка: {p.checks.errors!.join("; ")}</div>}
          {(p.actions.length > 0 || p.can_scan) && (
            <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {p.actions.length > 0 && <input placeholder="комментарий (для возврата в черновик — обязательно)" value={note} onChange={(e) => setNote(e.target.value)} style={{ width: 300 }} />}
              {p.actions.map((a) => <button key={a.to} className={`btn ${a.to === "draft" || a.to === "archived" ? "ghost" : ""}`} style={{ padding: "2px 10px" }} onClick={() => act(p, a.to)}>{a.title}</button>)}
              {p.can_scan && <label className="btn ghost" style={{ padding: "2px 10px" }}>Скан подписанного<input type="file" style={{ display: "none" }} onChange={(e) => { const fl = e.target.files?.[0]; if (fl) api.upload(`/api/legal/packages/${p.id}/signed-scan`, fl).then(load).catch((x) => setMsg((x as Error).message)); e.target.value = ""; }} /></label>}
            </div>)}
          {p.approvals.length > 0 && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{p.approvals.slice(0, 3).map((a, i) => <div key={i}>{d(a.at)} · {a.by_name} · {a.to}{a.comment ? ` — ${a.comment}` : ""}</div>)}</div>}
        </div>))}
      {hidden > 0 && <button className="btn ghost" style={{ fontSize: 12 }} onClick={() => setOld(!old)}>{old ? "скрыть старые версии" : `показать старые версии (${hidden})`}</button>}
    </div>);
}
