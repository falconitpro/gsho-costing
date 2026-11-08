import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";

// Реестр договоров (юридический блок, этап 1): договоры с клиентами, поставщиками, подрядчиками и прочие.
// По каждому договору обязательны условия оплаты «сколько — через сколько дней — после какого события»; неустойки — для потока и претензий.

type PayLine = { no?: number; share_pct?: number | null; amount?: number | null; event: string; offset_days: number; offset_kind: string; via?: string; event_on?: string | null; day_of_month?: number | null; note?: string };
type PerfTerm = { code: string; title?: string; event: string; offset_days: number; offset_kind: string; fixed_on?: string | null; note?: string };
type Penalty = { payer: string; reason: string; rate_kind: string; rate: number | null; base: string; grace_days: number; cap_kind: string; cap_value: number | null; term?: string; note?: string };
type Sched = { no: number; amount: number | null; currency: string; event_title: string; event_on: string | null; event_fact: boolean; event_source: string; due_on: string | null; due_legal: boolean; days_overdue: number; text: string };
type CFile = { id: string; kind: string; kind_title: string; name: string; size: number; version: number; uploaded_by_name: string; uploaded_at: string; note: string };
type Row = {
  id: string; doc_type: string; doc_type_title: string; kind: string; kind_title: string; direction: string; direction_title: string; number: string; cp_number: string; title: string; subject: string;
  status: string; status_title: string; locked: boolean; own_entity_id: string | null; own_entity_name: string; cp_kind: string; cp_id: string | null; cp_name: string; cp_inn: string;
  deal_id: string | null; deal_number: string; amount: number | null; currency: string; signed_on: string | null; valid_to: string | null; days_to_expiry: number | null; sign_method: string;
  has_scan: boolean; curator_name: string; terms_text: string;
};
type Full = Row & {
  vat_mode: string; vat_rate: number | null; price_type: string; fx_clause: string; effective_from: string | null; auto_renew: boolean; signatory: { name?: string; position?: string; basis?: string };
  payment_terms: PayLine[]; performance_terms: PerfTerm[]; penalties: Penalty[]; liability_cap_pct: number | null; security: Record<string, string>; warranty: Record<string, string>;
  law: { law?: string; court?: string; claim_days?: number; pre_trial?: boolean }; edo: { operator?: string; doc_id?: string }; original: { received_on?: string; location?: string };
  files: CFile[]; history: { at: string; by_name: string; action: string; comment: string }[]; notes: string; schedule: Sched[]; check: { ok: boolean; errors: string[]; warnings: string[] };
  children: Row[]; can_edit: boolean; can_edit_number: boolean; created_by_name: string;
};
type Catalog = { kinds: Record<string, { title: string; direction: string; cp_kind: string }>; directions: Record<string, string>; statuses: Record<string, string>; file_kinds: Record<string, string>;
  events: Record<string, string>; performance_codes: Record<string, string>; penalty_payers: Record<string, string>; penalty_reasons: Record<string, string>; rate_kinds: Record<string, string>;
  penalty_bases: Record<string, string>; cap_kinds: Record<string, string> };
type Registry = { items: Row[]; summary: Record<string, number>; entities: { id: string; name: string }[]; can_manage: boolean };

const STATUS_CLASS: Record<string, string> = { draft: "", legal_review: "info", approval: "info", approved: "info", signing: "warn", active: "ok", closed: "", terminated: "bad", cancelled: "" };
const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");
const money = (v: number | null | undefined, cur = "") => (v == null ? "—" : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}${cur ? " " + cur : ""}`);
const ACTION: Record<string, string> = { created: "создан", edited: "изменён", package: "обновлён из пакета", pull_terms: "условия из расчёта", status: "статус", file: "файл", file_removed: "файл удалён", imported: "импорт из сделки" };

export default function ContractsBlock({ dealId, compact }: { dealId?: string; compact?: boolean }) {
  const [reg, setReg] = useState<Registry | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [f, setF] = useState({ q: "", status: "", direction: "", kind: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
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
            {s.no_scan ? <> · <span style={{ color: "var(--bad)" }}>без скана: {s.no_scan}</span></> : null}{s.no_terms ? <> · <span style={{ color: "var(--bad)" }}>без условий оплаты: {s.no_terms}</span></> : null}</div>
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
                <td style={{ whiteSpace: "nowrap" }}>{d(r.signed_on)}{r.locked && !r.has_scan && <div style={{ fontSize: 11, color: "var(--bad)" }}>нет скана</div>}{r.sign_method === "edo" && <div className="muted" style={{ fontSize: 11 }}>ЭДО</div>}</td>
                <td><span className={`badge ${STATUS_CLASS[r.status] || ""}`}>{r.status_title}</span>{r.days_to_expiry != null && r.status === "active" && r.days_to_expiry <= 30 && <div style={{ fontSize: 11, color: "var(--warn)" }}>до {d(r.valid_to)}</div>}</td>
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
type Tab = "main" | "pay" | "perf" | "files" | "history";
function ContractCard({ id, cat, onClose, onOpen }: { id: string; cat: Catalog; onClose: () => void; onOpen: (id: string) => void }) {
  const [c, setC] = useState<Full | null>(null);
  const [draft, setDraft] = useState<Partial<Full>>({});
  const [tab, setTab] = useState<Tab>("main");
  const [err, setErr] = useState<string[]>([]);
  const [info, setInfo] = useState("");
  const [fileKind, setFileKind] = useState("draft");
  const [reason, setReason] = useState("");
  const load = () => api.get<Full>(`/api/legal/contracts/${id}`).then((x) => { setC(x); setDraft({}); }).catch((e) => setErr([(e as Error).message]));
  useEffect(() => { load(); setTab("main"); }, [id]);
  const v = useMemo(() => (c ? { ...c, ...draft } as Full : null), [c, draft]);
  if (!c || !v) return <Modal title="Договор" onClose={onClose}><div className="poa-body">{err.join("; ") || "Загрузка…"}</div></Modal>;
  const ro = !c.can_edit || c.locked;
  const set = (p: Partial<Full>) => setDraft({ ...draft, ...p });
  const dirty = Object.keys(draft).length > 0;
  const fail = (e: unknown) => { const det = (e as { detail?: { errors?: string[] } }).detail; setErr(det?.errors?.length ? det.errors : [(e as Error).message]); };
  const save = () => api.put<Full>(`/api/legal/contracts/${id}`, draft).then((x) => { setC(x); setDraft({}); setErr([]); setInfo("Сохранено"); }).catch(fail);
  const status = (to: string) => api.post<Full>(`/api/legal/contracts/${id}/status`, { status: to, comment: reason }).then((x) => { setC({ ...c, ...x }); setReason(""); setErr([]); setInfo(`Статус: ${x.status_title}`); }).catch(fail);
  const pull = () => api.post<Full>(`/api/legal/contracts/${id}/pull-terms`).then((x) => { setC(x); setDraft({}); setInfo("Цена и условия оплаты перенесены из расчёта сделки"); }).catch(fail);
  const tabs: [Tab, string][] = [["main", "Основное"], ["pay", "Оплата"], ["perf", "Сроки и неустойки"], ["files", `Файлы (${c.files.length})`], ["history", "История"]];
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
        {c.locked && <div className="alert info">Договор подписан: условия меняются только дополнительным соглашением. Номер и дату у подписанного меняет ЗГД или КД.</div>}
        {err.length > 0 && <div className="alert error"><ul style={{ margin: 0, paddingLeft: 18 }}>{err.map((e) => <li key={e}>{e}</li>)}</ul></div>}
        {info && <div className="alert ok" onClick={() => setInfo("")}>{info}</div>}
        {!c.check.ok && !c.locked && <div className="alert warn"><b>Не готов к подписанию:</b> {c.check.errors.join("; ")}</div>}

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

        {tab === "files" && (
          <div className="poa-section">
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
          {!c.locked && c.status !== "cancelled" && <button className="btn ghost" onClick={() => { setErr([]); status("active"); }}>Отметить подписанным</button>}
          {c.status === "active" && <button className="btn ghost" onClick={() => status("closed")}>Исполнен</button>}
          {(c.status === "active" || (!c.locked && c.status !== "cancelled")) && <>
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="причина (для расторжения / аннулирования)" style={{ width: 240 }} />
            {c.status === "active" ? <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => status("terminated")}>Расторгнут</button>
              : <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => status("cancelled")}>Аннулировать</button>}
          </>}
          {c.doc_type === "contract" && c.status === "active" && <button className="btn ghost add" onClick={() => api.post<Full>("/api/legal/contracts", { parent_id: c.id, doc_type: "amendment" }).then((x) => onOpen(x.id)).catch(fail)}>Доп. соглашение</button>}
        </div>
        <div className="row" style={{ gap: 8 }}>
          {dirty && <button className="btn ghost" onClick={() => setDraft({})}>Отменить изменения</button>}
          <button className="btn" disabled={!dirty} onClick={save}>Сохранить</button>
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
        <div className="poa-section-head"><b>График платежей</b><span className="muted">срок считается от плановой даты события; юридически значимый — от фактической (тогда же считается просрочка)</span></div>
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
