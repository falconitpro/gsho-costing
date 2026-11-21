import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import { Crumbs, useQueryParam } from "../components/Levels";
import { askPrompt } from "../components/Dialogs";
import type { Me } from "../types";
import OfficeAi from "../components/OfficeAi";
import OfficeGuide from "../components/OfficeGuide";

/** Канцелярия (D-128): журналы входящих и исходящих писем, приказов, распоряжений, служебных записок и внутренних документов.
 *  Регистрирует делопроизводитель (офис-менеджер) или ЗГД; исполнитель видит свои документы; подписантов ведёт ЗГД. */

type Meta = {
  entities: { id: string; code: string; name: string; prefix: string }[];
  kinds: Record<string, string>; order_categories: Record<string, string>; internal_categories: Record<string, string>;
  letter_types: Record<string, string>; channels: Record<string, string>; statuses: Record<string, string>; sign_methods: Record<string, string>;
  signers: { id: string; entity_id: string; full_name: string; position: string; basis: string; valid_from: string | null; valid_to: string | null; methods: string[]; is_active: boolean; valid_today: boolean }[];
  users: { id: string; name: string }[]; can_edit: boolean; can_hr: boolean; can_signers: boolean;
  channel_detail: Record<string, string>; routes: Route[]; roles: Record<string, string>; ai: { enabled: boolean; configured: boolean };
};
type Step = { id?: string; user_id?: string | null; role?: string | null; title?: string; status?: string; status_title?: string; who?: string; by_name?: string; comment?: string; at?: string };
type Route = { id: string; name: string; kinds: string[]; categories: string[]; entity_id: string | null; mode: string; steps: Step[]; required: boolean; is_active: boolean };
type Approval = { name: string; mode: string; round: number; my_step: string | null; steps: Step[] };
type Resolution = { id: string; at: string; kind: string; kind_title: string; text: string; author: string; executor: string; co_names: string[]; due_on: string | null };
type Doc = {
  id: string; entity_id: string; entity: string; entity_prefix: string; kind: string; kind_title: string; category: string; number: string; reg_date: string | null;
  status: string; status_title: string; subject: string; summary: string; letter_type: string; channel: string; counterparty: string; contact: string;
  sender_number: string; sender_date: string | null; due_on: string | null; overdue: boolean; executor_id: string | null; executor: string;
  co_executor_ids: string[]; deal_ids: string[]; deal_section: string; reply_to_id: string | null; signer_id: string | null; signer: string;
  signed_on: string | null; sign_method: string; sent_on: string | null; effective_on: string | null; closed_on: string | null; close_note: string;
  files: { id: string; name: string; size: number; kind: string; at: string }[]; can_edit: boolean; is_executor: boolean;
  urgent: boolean; confidential: boolean; needs_reply: boolean; has_scan: boolean; pages: number | null; attachments_count: number | null;
  address: string; tracking: string; delivered_on: string | null; note: string; comments_count: number;
  reviewer_id: string | null; reviewer: string; is_reviewer: boolean; approval: Approval | null;
  comments?: { id: string; text: string; at: string; author: string }[]; resolutions?: Resolution[];
  deals?: { id: string; number: string }[]; body?: string; history?: { at: string; by: string; action: string; note: string }[];
  replies?: { id: string; number: string; status: string; subject: string }[]; reply_to?: { id: string; number: string; subject: string } | null;
};

const TABS: { key: string; label: string; kinds: string }[] = [
  { key: "in", label: "Входящие", kinds: "in" },
  { key: "out", label: "Исходящие", kinds: "out" },
  { key: "orders", label: "Приказы и распоряжения", kinds: "order,directive" },
  { key: "internal", label: "Служебные и внутренние", kinds: "memo,internal" },
];
const FILE_KIND: Record<string, string> = { original: "оригинал", scan: "скан", attachment: "приложение", draft: "проект" };
const OPEN_IN = ["registered", "review", "assigned", "in_work"];
const routesFor = (meta: Meta, d: Doc) => meta.routes.filter((r) => r.is_active && (!r.kinds.length || r.kinds.includes(d.kind))
  && (!r.categories.length || r.categories.includes(d.category)) && (!r.entity_id || r.entity_id === d.entity_id));

export default function OfficePage({ me }: { me: Me }) {
  const [tabQ, setTabQ] = useQueryParam("t");
  const [docQ, setDocQ] = useQueryParam("doc");
  const tab = tabQ || "in";
  const docId = docQ || "";
  const setTab = (v: string) => setTabQ(v, { drop: ["doc"] });
  const setDocId = (v: string) => setDocQ(v || null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rows, setRows] = useState<Doc[]>([]);
  const [counts, setCounts] = useState<{ in_open: number; overdue: number; drafts: number }>({ in_open: 0, overdue: 0, drafts: 0 });
  const [q, setQ] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [exFrom, setExFrom] = useState(() => `${new Date().getFullYear()}-01-01`);
  const [exTo, setExTo] = useState("");
  const [creating, setCreating] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const journal = (me.perms?.office ?? 0) >= 1 || (me.perms?.office_hr ?? 0) >= 1;
  const cur = TABS.find((t) => t.key === tab) || TABS[0];
  const load = () => {
    const p = new URLSearchParams();
    if (!journal) p.set("mine", "true"); else if (tab !== "signers") p.set("kind", cur.kinds);
    if (q.trim()) p.set("q", q.trim());
    if (onlyOpen) p.set("status", "registered,review,assigned,in_work,draft,approval,approved");
    api.get<{ items: Doc[]; counts: typeof counts }>(`/api/office/docs?${p}`).then((r) => { setRows(r.items); setCounts(r.counts); }).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(() => { api.get<Meta>("/api/office/meta").then(setMeta); }, []);
  useEffect(load, [tab, onlyOpen]);
  if (!meta) return <div className="muted">{err || "Загрузка…"}</div>;
  return (
    <div>
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Канцелярия" }]} />
      <div className="page-head"><h1>Канцелярия</h1>
        {journal && <span className="muted">входящих в работе: <b>{counts.in_open}</b> · просрочено: <b className={counts.overdue ? "txt-bad" : ""}>{counts.overdue}</b></span>}
      </div>
      {journal ? (
        <div className="tabs">
          {TABS.map((t) => <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => { setTab(t.key); setDocId(""); setCreating(null); }}>{t.label}</button>)}
          <button className={tab === "control" ? "active" : ""} onClick={() => { setTab("control"); setDocId(""); setCreating(null); }}>На контроле{counts.overdue ? ` · ${counts.overdue}` : ""}</button>
          <button className={tab === "guide" ? "active" : ""} onClick={() => { setTab("guide"); setDocId(""); setCreating(null); }}>Как работать</button>
          {meta.can_signers && <button className={tab === "signers" ? "active" : ""} onClick={() => { setTab("signers"); setDocId(""); }}>Подписанты и схемы согласования</button>}
        </div>
      ) : <div className="muted">Документы, где вы исполнитель или автор. Журналы ведёт делопроизводитель. <a href="#" onClick={(e) => { e.preventDefault(); setTab(tab === "guide" ? "in" : "guide"); }}>{tab === "guide" ? "К документам" : "Как работать"}</a></div>}
      {tab === "guide" ? <OfficeGuide /> : tab === "control" && journal ? <>{docId && <DocCard id={docId} meta={meta} me={me} onClose={() => setDocId("")} onChanged={load} onOpen={setDocId} />}<Control onOpen={setDocId} /></> : tab === "signers" && meta.can_signers ? <><Signers meta={meta} onChange={setMeta} /><Routes meta={meta} onChange={setMeta} /></> : (
        <>
          <div className="office-bar">
            <input placeholder="№, тема, контрагент" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
            <label><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> только в работе и черновики</label>
            {journal && <span className="office-export">
              <input type="date" value={exFrom} onChange={(e) => setExFrom(e.target.value)} aria-label="Выгрузка с" />—<input type="date" value={exTo} onChange={(e) => setExTo(e.target.value)} aria-label="по" />
              <a className="btn ghost" href={`/api/office/export?${new URLSearchParams({ kind: cur.kinds, ...(exFrom ? { date_from: exFrom } : {}), ...(exTo ? { date_to: exTo } : {}), ...(q.trim() ? { q: q.trim() } : {}), ...(onlyOpen ? { status: "registered,review,assigned,in_work,draft,approval,approved" } : {}) })}`}>В Excel</a>
            </span>}
            {journal && (meta.can_edit || meta.can_hr) && !creating && (
              <span className="push row">
                {cur.kinds.split(",").filter((k) => meta.can_edit || k === "order").map((k) => (
                  <button key={k} className="btn" onClick={() => { setCreating(k); setDocId(""); }}>{k === "in" ? "Зарегистрировать входящее" : `Новый: ${meta.kinds[k].toLowerCase()}`}</button>))}
              </span>
            )}
          </div>
          {creating && <NewDoc meta={meta} kind={creating} onCancel={() => setCreating(null)} onDone={(d) => { setCreating(null); setDocId(d.id); load(); }} />}
          {docId && <DocCard id={docId} meta={meta} me={me} onClose={() => setDocId("")} onChanged={load} onOpen={setDocId} />}
          <table className="office-table">
            <thead><tr><th>№</th><th>Дата</th><th>Вид</th><th>Тема</th><th>Контрагент</th><th>Исполнитель / подписант</th><th>Срок</th><th>Статус</th></tr></thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={8} className="muted">Документов нет</td></tr>}
              {rows.map((d) => (
                <tr key={d.id} className={(docId === d.id ? "sel " : "") + (d.overdue ? "late" : "")} onClick={() => { setDocId(d.id); setCreating(null); }}>
                  <td className="nowrap"><b>{d.number || "черновик"}</b></td><td className="nowrap">{fmtDate(d.reg_date)}</td>
                  <td>{d.kind_title}{d.kind === "order" && d.category ? ` ${meta.order_categories[d.category]}` : ""}{d.letter_type ? ` · ${meta.letter_types[d.letter_type]}` : ""}</td>
                  <td>{d.urgent && <span className="badge bad">срочно</span>} {d.confidential && <span className="badge">конф.</span>} {d.subject}
                    {d.kind === "in" && !d.has_scan && <span className="muted"> · нет скана</span>}{d.comments_count > 0 && <span className="muted"> · 💬 {d.comments_count}</span>}</td><td>{d.counterparty}</td><td>{d.kind === "in" ? (d.status === "review" ? `на рассмотрении: ${d.reviewer}` : d.executor) : d.signer || d.executor}</td>
                  <td className={"nowrap" + (d.overdue ? " txt-bad" : "")}>{fmtDate(d.due_on)}</td>
                  <td><span className={"badge" + (d.overdue ? " bad" : d.status === "draft" ? " warn" : ["closed", "sent", "in_force"].includes(d.status) ? " ok" : "")}>{d.status_title}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function NewDoc({ meta, kind, onCancel, onDone }: { meta: Meta; kind: string; onCancel: () => void; onDone: (d: Doc) => void }) {
  const ru = meta.entities.find((e) => e.code === "ru") || meta.entities[0];
  const gd = meta.users.find((u) => u.name.startsWith("Добров"));
  const [f, setF] = useState<Record<string, string>>({ entity_id: ru?.id || "", kind, category: kind === "order" ? (meta.can_edit ? "od" : "hr") : "",
    letter_type: kind === "in" ? "request" : "", channel: "", tracking: "", subject: "", summary: "", body: "", counterparty: "", contact: "", address: "",
    sender_number: "", sender_date: "", received_on: new Date().toISOString().slice(0, 10), due_on: "", executor_id: "", reviewer_id: gd?.id || "",
    route: kind === "in" ? "review" : "", deal: "", deal_section: "sale", pages: "", attachments_count: "", note: "" });
  const [flags, setFlags] = useState({ urgent: false, confidential: kind === "order" && f.category === "hr", needs_reply: true });
  const [scan, setScan] = useState<File | null>(null);
  const [extra, setExtra] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [deals, setDeals] = useState<{ id: string; number: string; client_name?: string }[]>([]);
  useEffect(() => { api.get<{ id: string; number: string; client_name?: string }[]>("/api/deals").then(setDeals).catch(() => setDeals([])); }, []);
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const isIn = kind === "in";
  const letter = isIn || kind === "out";
  const noReplyTypes = ["notice", "other"];
  const missing = [!f.subject.trim() && "тема", isIn && !f.channel && "источник поступления", isIn && !scan && "скан письма",
    isIn && f.route === "review" && !f.reviewer_id && "руководитель", isIn && f.route === "exec" && !f.executor_id && "исполнитель",
    isIn && f.letter_type === "gov" && !f.due_on && "срок ответа из письма"].filter(Boolean) as string[];
  const save = async () => {
    setBusy(true);
    try {
      let d = await api.post<Doc>("/api/office/docs", { ...f, ...flags, sender_date: f.sender_date || null, received_on: isIn ? f.received_on : null,
        due_on: f.due_on || null, executor_id: isIn && f.route === "exec" ? f.executor_id || null : null, deal_ids: f.deal ? [f.deal] : [],
        deal_section: f.deal ? f.deal_section : "", pages: f.pages ? Number(f.pages) : null, attachments_count: f.attachments_count ? Number(f.attachments_count) : null,
        needs_reply: isIn ? flags.needs_reply : null });
      if (scan) d = await api.upload<Doc>(`/api/office/docs/${d.id}/files`, scan, { kind: isIn ? "scan" : "draft" });
      for (const x of extra) d = await api.upload<Doc>(`/api/office/docs/${d.id}/files`, x, { kind: "attachment" });
      if (isIn && f.route === "review" && f.reviewer_id) d = await api.post<Doc>(`/api/office/docs/${d.id}/review`, { reviewer_id: f.reviewer_id });
      onDone(d);
    } finally { setBusy(false); }
  };
  return (
    <div className="card office-form">
      <h2>{isIn ? "Регистрация входящего" : `Новый документ: ${meta.kinds[kind].toLowerCase()}`}</h2>
      <div className="grid2">
        <label>Юр. лицо<select value={f.entity_id} onChange={set("entity_id")}>{meta.entities.map((e) => <option key={e.id} value={e.id}>{e.prefix} · {e.name}</option>)}</select></label>
        {kind === "order" && <label>Вид приказа<select value={f.category} onChange={set("category")}>
          {Object.entries(meta.order_categories).filter(([k]) => k !== "hr" || meta.can_hr).filter(([k]) => k === "hr" || meta.can_edit).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {kind === "internal" && <label>Вид документа<select value={f.category} onChange={set("category")}><option value="">—</option>{Object.entries(meta.internal_categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {isIn && <label>Вид письма<select value={f.letter_type} onChange={(e) => { setF({ ...f, letter_type: e.target.value }); setFlags({ ...flags, needs_reply: !noReplyTypes.includes(e.target.value) }); }}>
          {Object.entries(meta.letter_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {letter && <label>{isIn ? "Источник поступления *" : "Как отправим"}<select value={f.channel} onChange={set("channel")} className={isIn && !f.channel ? "need" : ""}>
          <option value="">{isIn ? "выберите…" : "решим при отправке"}</option>{Object.entries(meta.channels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {letter && f.channel && meta.channel_detail[f.channel] && <label>{meta.channel_detail[f.channel][0].toUpperCase() + meta.channel_detail[f.channel].slice(1)}<input value={f.tracking} onChange={set("tracking")} /></label>}
        <label className="span2">Тема *<input value={f.subject} onChange={set("subject")} /></label>
        {letter && <label>{isIn ? "Отправитель" : "Адресат"}<input value={f.counterparty} onChange={set("counterparty")} placeholder="организация" /></label>}
        {letter && <label>Контакт<input value={f.contact} onChange={set("contact")} placeholder="ФИО, должность, телефон" /></label>}
        {kind === "out" && <label className="span2">Адрес / e-mail адресата<input value={f.address} onChange={set("address")} /></label>}
        {isIn && <label>Исх. № отправителя<input value={f.sender_number} onChange={set("sender_number")} /></label>}
        {isIn && <label>от<input type="date" value={f.sender_date} onChange={set("sender_date")} /></label>}
        {isIn && <label>Дата поступления<input type="date" value={f.received_on} onChange={set("received_on")} /></label>}
        {letter && <label>Листов / приложений<span className="row"><input type="number" min={0} value={f.pages} onChange={set("pages")} placeholder="листов" /><input type="number" min={0} value={f.attachments_count} onChange={set("attachments_count")} placeholder="прил." /></span></label>}
        <div className="span2 row">
          <label className="row"><input type="checkbox" checked={flags.urgent} onChange={(e) => setFlags({ ...flags, urgent: e.target.checked })} /> срочно</label>
          <label className="row"><input type="checkbox" checked={flags.confidential} onChange={(e) => setFlags({ ...flags, confidential: e.target.checked })} /> конфиденциально (видят исполнители и Канцелярия)</label>
          {isIn && <label className="row"><input type="checkbox" checked={flags.needs_reply} onChange={(e) => setFlags({ ...flags, needs_reply: e.target.checked })} /> требует ответа</label>}
        </div>
        {isIn && flags.needs_reply && <label>Срок ответа<input type="date" value={f.due_on} onChange={set("due_on")} />
          <span className="muted">{f.letter_type === "gov" ? "обязательно — из письма" : f.due_on ? "" : `по умолчанию: ${({ request: "2", claim: "5 (или по договору)", complaint: "5" } as Record<string, string>)[f.letter_type] ?? "—"} раб. дн.`}</span></label>}
        {isIn && <label>Дальше<select value={f.route} onChange={set("route")}>
          <option value="review">на рассмотрение руководителю</option><option value="exec">сразу исполнителю</option><option value="later">решу позже</option></select></label>}
        {isIn && f.route === "review" && <label>Руководитель<select value={f.reviewer_id} onChange={set("reviewer_id")}><option value="">—</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
          <span className="muted">наложит резолюцию: исполнитель, срок, указание — или «к сведению»</span></label>}
        {isIn && f.route === "exec" && <label>Исполнитель<select value={f.executor_id} onChange={set("executor_id")}><option value="">—</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
        {letter && <label>Сделка<select value={f.deal} onChange={set("deal")}><option value="">без сделки</option>{deals.map((d) => <option key={d.id} value={d.id}>{d.number}{d.client_name ? ` · ${d.client_name}` : ""}</option>)}</select></label>}
        {letter && f.deal && <label>Раздел сделки<select value={f.deal_section} onChange={set("deal_section")}>
          {Object.entries({ "": "Общее", sale: "Продажа", logistics: "Логистика", service: "Сервис", legal: "Договор и юр.", execution: "Исполнение", finance: "Финансы" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        <label className="span2">{isIn ? "Краткое содержание" : "Краткое содержание (для журнала)"}<textarea rows={2} value={f.summary} onChange={set("summary")} /></label>
        {!isIn && <label className="span2">Текст документа<textarea rows={6} value={f.body} onChange={set("body")} placeholder="Бланк юр. лица подставится, когда будут загружены шаблоны" /></label>}
        <label className="span2">Комментарий делопроизводителя<input value={f.note} onChange={set("note")} placeholder="например: оригинал в папке «Претензии 2026», конверт сохранён" /></label>
        <label>{isIn ? "Скан письма *" : "Файл проекта"}<input type="file" onChange={(e) => setScan(e.target.files?.[0] || null)} />
          {isIn && <span className="muted">скан бумажного письма или исходный файл (PDF, письмо .eml)</span>}</label>
        <label>Приложения<input type="file" multiple onChange={(e) => setExtra(Array.from(e.target.files || []))} /></label>
      </div>
      <div className="row">
        <button className="btn" disabled={busy || missing.length > 0} onClick={save}>{isIn ? "Зарегистрировать — номер сразу" : "Сохранить черновик"}</button>
        <button className="btn ghost" onClick={onCancel}>Отмена</button>
        <span className="muted">{missing.length ? `Не заполнено: ${missing.join(", ")}` : isIn ? "Номер присваивается при регистрации" : "Номер присваивается при подписании"}</span>
      </div>
    </div>
  );
}

function DocCard({ id, meta, me, onClose, onChanged, onOpen }: { id: string; meta: Meta; me: Me; onClose: () => void; onChanged: () => void; onOpen: (id: string) => void }) {
  const [d, setD] = useState<Doc | null>(null);
  const [exec, setExec] = useState("");
  const [due, setDue] = useState("");
  const [signer, setSigner] = useState("");
  const [reviewer, setReviewer] = useState(meta.users.find((u) => u.name.startsWith("Добров"))?.id || "");
  const [sendCh, setSendCh] = useState("");
  const [reply, setReply] = useState(false);
  useEffect(() => { api.get<Doc>(`/api/office/docs/${id}`).then((x) => { setD(x); setExec(x.executor_id || ""); setDue(x.due_on || ""); setSendCh(x.channel || ""); }); }, [id]);
  if (!d) return null;
  const upd = (x: Doc) => { setD(x); onChanged(); };
  const act = (action: string, note = "", extra: Record<string, unknown> = {}) => api.post<Doc>(`/api/office/docs/${d.id}/action`, { action, note, ...extra }).then(upd);
  const signers = meta.signers.filter((s) => s.entity_id === d.entity_id && s.valid_today);
  const ed = meta.can_edit || (d.kind === "order" && d.category === "hr" && meta.can_hr);
  const isIn = d.kind === "in";
  const needRoute = !isIn && routesFor(meta, d).some((r) => r.required);
  const canSign = (d.status === "approved" || (d.status === "draft" && !needRoute)) && (ed || d.can_edit) && !isIn;
  const executorMain = d.executor_id === me.id;
  return (
    <div className="card office-card">
      <div className="row">
        <h2>{d.number || "Черновик"} · {d.kind_title}{d.kind === "order" && d.category ? ` ${meta.order_categories[d.category]}` : ""}</h2>
        <span className={"badge" + (d.overdue ? " bad" : "")}>{d.status_title}</span>
        {d.urgent && <span className="badge bad">срочно</span>}{d.confidential && <span className="badge">конфиденциально</span>}
        <a href="#" className="push" onClick={(e) => { e.preventDefault(); onClose(); }}>закрыть ✕</a>
      </div>
      <div className="office-grid">
        <div><span className="muted">Юр. лицо</span> {d.entity_prefix} · {d.entity}</div>
        {d.reg_date && <div><span className="muted">Дата</span> {fmtDate(d.reg_date)}</div>}
        <div className="span2"><span className="muted">Тема</span> <b>{d.subject}</b></div>
        {d.counterparty && <div><span className="muted">{isIn ? "Отправитель" : "Адресат"}</span> {d.counterparty}{d.contact ? ` · ${d.contact}` : ""}</div>}
        {d.address && <div><span className="muted">Адрес</span> {d.address}</div>}
        {d.sender_number && <div><span className="muted">Исх. № отправителя</span> {d.sender_number}{d.sender_date ? ` от ${fmtDate(d.sender_date)}` : ""}</div>}
        {d.letter_type && <div><span className="muted">Вид</span> {meta.letter_types[d.letter_type]}{d.needs_reply ? "" : " · ответа не требует"}</div>}
        {d.channel && <div><span className="muted">{isIn ? "Источник поступления" : "Способ отправки"}</span> {meta.channels[d.channel]}{d.tracking ? ` · ${d.tracking}` : ""}</div>}
        {(d.pages || d.attachments_count) && <div><span className="muted">Объём</span> {d.pages ? `${d.pages} л.` : ""}{d.attachments_count ? ` + прил. ${d.attachments_count}` : ""}</div>}
        {d.due_on && <div><span className="muted">Срок</span> <b className={d.overdue ? "txt-bad" : ""}>{fmtDate(d.due_on)}</b></div>}
        {d.reviewer && <div><span className="muted">Рассматривает</span> {d.reviewer}</div>}
        {d.executor && <div><span className="muted">Исполнитель</span> {d.executor}{d.co_executor_ids.length ? ` + соисп. ${d.co_executor_ids.map((x) => meta.users.find((u) => u.id === x)?.name || "").join(", ")}` : ""}</div>}
        {d.signer && <div><span className="muted">Подписал</span> {d.signer} · {fmtDate(d.signed_on)} · {meta.sign_methods[d.sign_method]}</div>}
        {d.sent_on && <div><span className="muted">Отправлено</span> {fmtDate(d.sent_on)}{d.delivered_on ? ` · вручено ${fmtDate(d.delivered_on)}` : ""}</div>}
        {d.effective_on && <div><span className="muted">Вступает в силу</span> {fmtDate(d.effective_on)}</div>}
        {d.deals && d.deals.length > 0 && <div><span className="muted">Сделка</span> {d.deals.map((x) => <a key={x.id} href={`/deals/${x.id}?tab=feed`}>{x.number} </a>)}</div>}
        {d.reply_to && <div><span className="muted">В ответ на</span> <a href="#" onClick={(e) => { e.preventDefault(); onOpen(d.reply_to!.id); }}>{d.reply_to.number}</a></div>}
        {d.replies && d.replies.length > 0 && <div><span className="muted">Ответ</span> {d.replies.map((r) => <a key={r.id} href="#" onClick={(e) => { e.preventDefault(); onOpen(r.id); }}>{r.number || "черновик"} </a>)}</div>}
        {d.close_note && <div className="span2"><span className="muted">Исполнение</span> {d.close_note}</div>}
        {d.summary && <div className="span2"><span className="muted">Кратко</span> {d.summary}</div>}
        {d.note && <div className="span2"><span className="muted">Отметка Канцелярии</span> {d.note}</div>}
        {d.body && !isIn && <div className="span2 office-body">{d.body}</div>}
      </div>
      <div className="office-files">
        {isIn && !d.has_scan && <span className="badge warn">нет скана — без него письмо не уйдёт на рассмотрение и исполнителю</span>}
        {d.kind === "out" && d.status === "signed" && !d.files.some((f) => f.kind === "scan") && <span className="badge warn">приложите скан подписанного письма</span>}
        {d.files.map((f) => <a key={f.id} href={`/api/office/docs/${d.id}/files/${f.id}`} target="_blank" rel="noreferrer">📎 {f.name} <span className="muted">({FILE_KIND[f.kind] || f.kind})</span></a>)}
        {(ed || d.is_executor || d.can_edit) && (
          <>
            <label className="btn ghost">+ скан<input type="file" hidden onChange={(e) => { const x = e.target.files?.[0]; if (x) api.upload<Doc>(`/api/office/docs/${d.id}/files`, x, { kind: "scan" }).then(upd); }} /></label>
            <label className="btn ghost">+ {d.status === "draft" ? "проект / приложение" : "приложение"}<input type="file" hidden onChange={(e) => { const x = e.target.files?.[0]; if (x) api.upload<Doc>(`/api/office/docs/${d.id}/files`, x, { kind: d.status === "draft" ? "draft" : "attachment" }).then(upd); }} /></label>
          </>
        )}
      </div>
      {d.resolutions && d.resolutions.length > 0 && (
        <div className="office-res">
          {d.resolutions.map((r) => (
            <div key={r.id}><b>Резолюция</b> {r.author}, {new Date(r.at).toLocaleDateString("ru-RU")}: <b>{r.kind_title}</b>
              {r.executor ? ` — ${r.executor}` : ""}{r.co_names.length ? ` (соисп.: ${r.co_names.join(", ")})` : ""}{r.due_on ? `, срок ${fmtDate(r.due_on)}` : ""}{r.text ? ` · «${r.text}»` : ""}</div>
          ))}
        </div>
      )}
      {d.is_reviewer || (meta.can_signers && isIn && OPEN_IN.includes(d.status)) ? <ResolutionForm d={d} meta={meta} onDone={upd} /> : null}
      {d.approval && <ApprovalView d={d} onDone={upd} />}
      {!isIn && d.status === "draft" && (ed || d.can_edit) && <ApprovalStart d={d} meta={meta} onDone={upd} />}
      <div className="office-acts">
        {ed && isIn && ["registered", "assigned"].includes(d.status) && (
          <span className="row">
            <select value={reviewer} onChange={(e) => setReviewer(e.target.value)}><option value="">руководитель…</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
            <button className="btn ghost" disabled={!reviewer || !d.has_scan} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/review`, { reviewer_id: reviewer }).then(upd)}>На рассмотрение</button>
          </span>
        )}
        {ed && ["registered", "assigned", "in_work"].includes(d.status) && (
          <span className="row">
            <select value={exec} onChange={(e) => setExec(e.target.value)}><option value="">исполнитель…</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            <button className="btn ghost" disabled={!exec || (isIn && !d.has_scan)} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/assign`, { executor_id: exec, due_on: due || null }).then(upd)}>{d.executor_id ? "Переназначить" : "Назначить без рассмотрения"}</button>
          </span>
        )}
        {d.is_executor && d.status === "assigned" && <button className="btn" onClick={() => act("take")}>Взять в работу</button>}
        {isIn && (d.is_executor || ed) && ["assigned", "in_work", "registered"].includes(d.status) && !reply && <button className="btn" onClick={() => setReply(true)}>Подготовить ответ</button>}
        {executorMain && ["assigned", "in_work"].includes(d.status) && (
          <button className="btn ghost" onClick={async () => { const n = await askPrompt("Почему возвращаете? Например: «не мой вопрос — прошу передать юристу» или «прошу продлить срок до 05.12: ждём завод»"); if (n) api.post<Doc>(`/api/office/docs/${d.id}/return`, { reason: n }).then(upd); }}>Вернуть / продлить срок</button>)}
        {(d.is_executor || ed) && ["registered", "assigned", "in_work"].includes(d.status) && (
          <button className="btn ghost" onClick={async () => { const n = await askPrompt(d.needs_reply ? "Как исполнено? Например: «ответ не требуется: …» или «ответ дан по телефону»" : "Отметка об исполнении (можно пусто — «к сведению»)"); if (n !== null) act("close", n); }}>Закрыть без письма</button>)}
        {canSign && (
          <span className="row">
            <select value={signer} onChange={(e) => setSigner(e.target.value)}><option value="">подписант…</option>
              {signers.map((s) => <option key={s.id} value={s.id}>{s.position} {s.full_name}</option>)}</select>
            <button className="btn" disabled={!signer} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/sign`, { signer_id: signer, method: "manual" }).then(upd)}>Подписано — присвоить номер</button>
            {signers.length === 0 && <span className="muted">нет действующих подписантов у этого юр. лица — добавляет ЗГД во вкладке «Подписанты»</span>}
          </span>
        )}
        {!isIn && d.status === "draft" && needRoute && <span className="muted">для этого документа согласование обязательно — подписать можно после него</span>}
        {d.kind === "out" && d.status === "signed" && (ed || d.can_edit) && (
          <span className="row">
            <select value={sendCh} onChange={(e) => setSendCh(e.target.value)}><option value="">способ отправки…</option>{Object.entries(meta.channels).filter(([k]) => k !== "phone").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <button className="btn" disabled={!sendCh} onClick={async () => { const t = meta.channel_detail[sendCh] ? await askPrompt(`${meta.channel_detail[sendCh]} (можно пусто)`) : ""; if (t === null) return;
              if (t) await api.patch<Doc>(`/api/office/docs/${d.id}`, { tracking: t }); act("sent", "", { channel: sendCh }); }}>Отправлено</button>
          </span>
        )}
        {d.kind === "out" && d.status === "sent" && !d.delivered_on && ed && <button className="btn ghost" onClick={() => act("delivered")}>Вручено адресату</button>}
        {!isIn && d.kind !== "out" && d.status === "signed" && ed && <button className="btn" onClick={() => act("in_force")}>Ввести в действие</button>}
        {ed && !["closed", "cancelled"].includes(d.status) && (
          <button className="btn ghost" onClick={async () => { const n = await askPrompt("Причина отмены (номер останется в журнале с отметкой)"); if (n) act("cancel", n); }}>Отменить</button>)}
      </div>
      {reply && <NewReply parent={d} meta={meta} onDone={(x) => { setReply(false); onOpen(x.id); onChanged(); }} onCancel={() => setReply(false)} />}
      {isIn && (ed || d.body) && <InText d={d} ed={ed} onSave={(body) => api.patch<Doc>(`/api/office/docs/${d.id}`, { body }).then(upd)} />}
      {meta.ai.enabled && <OfficeAi d={d} letterTypes={meta.letter_types} roles={meta.roles} onPatch={(b) => api.patch<Doc>(`/api/office/docs/${d.id}`, b).then(upd)} />}
      <Comments d={d} onDone={upd} />
      {d.history && d.history.length > 0 && (
        <details><summary className="muted">История ({d.history.length})</summary>
          {d.history.map((h, i) => <div key={i} className="muted">{new Date(h.at).toLocaleString("ru-RU")} · {h.action}{h.note ? ` · ${h.note}` : ""}</div>)}
        </details>
      )}
    </div>
  );
}

/** D-130: резолюция руководителя — исполнитель (ответственный), соисполнители, срок, указание; или «к сведению» / «в дело». */
function ResolutionForm({ d, meta, onDone }: { d: Doc; meta: Meta; onDone: (d: Doc) => void }) {
  const [kind, setKind] = useState("execute");
  const [ex, setEx] = useState(d.executor_id || "");
  const [co, setCo] = useState<string[]>([]);
  const [due, setDue] = useState(d.due_on || "");
  const [text, setText] = useState("");
  return (
    <div className="office-form office-box">
      <b>{d.is_reviewer ? "Ваша резолюция" : "Резолюция"}</b>
      <div className="grid2">
        <label>Решение<select value={kind} onChange={(e) => setKind(e.target.value)}><option value="execute">к исполнению</option><option value="info">к сведению — закрыть</option><option value="file">в дело — закрыть</option></select></label>
        {kind === "execute" && <label>Ответственный исполнитель<select value={ex} onChange={(e) => setEx(e.target.value)}><option value="">—</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
        {kind === "execute" && <label>Соисполнители<select multiple value={co} onChange={(e) => setCo(Array.from(e.target.selectedOptions).map((o) => o.value))}>{meta.users.filter((u) => u.id !== ex).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
        {kind === "execute" && <label>Срок<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>}
        <label className="span2">Указание<input value={text} onChange={(e) => setText(e.target.value)} placeholder="например: подготовить ответ с расчётом, согласовать с юристом" /></label>
      </div>
      <button className="btn" disabled={kind === "execute" && !ex} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/resolution`, { kind, executor_id: ex || null, co_executor_ids: co, due_on: due || null, text }).then(onDone)}>Наложить резолюцию</button>
    </div>
  );
}

/** D-129: ход согласования и решение согласующего. */
function ApprovalView({ d, onDone }: { d: Doc; onDone: (d: Doc) => void }) {
  const a = d.approval!;
  const decide = async (decision: string) => {
    const c = await askPrompt(decision === "reject" ? "Что исправить?" : "Комментарий (можно пусто)");
    if (c === null || (decision === "reject" && !c.trim())) return;
    api.post<Doc>(`/api/office/docs/${d.id}/approval/decide`, { decision, comment: c }).then(onDone);
  };
  return (
    <div className="office-box">
      <b>Согласование</b> <span className="muted">{a.name} · {a.mode === "seq" ? "по очереди" : "одновременно"}{a.round > 1 ? ` · круг ${a.round}` : ""}</span>
      <ol className="office-steps">
        {a.steps.map((s) => <li key={s.id} className={s.status}>{s.title ? `${s.title}: ` : ""}{s.who}{s.by_name && s.by_name !== s.who ? ` (${s.by_name})` : ""} — <b>{s.status_title}</b>
          {s.at ? ` ${new Date(s.at).toLocaleString("ru-RU")}` : ""}{s.comment ? ` · «${s.comment}»` : ""}</li>)}
      </ol>
      <div className="row">
        {a.my_step && <><button className="btn" onClick={() => decide("approve")}>Согласовать</button><button className="btn ghost" onClick={() => decide("reject")}>Отклонить — на доработку</button></>}
        {["approval", "approved"].includes(d.status) && d.can_edit && <button className="btn ghost" onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/approval/recall`, {}).then(onDone)}>Отозвать для правки</button>}
      </div>
    </div>
  );
}

function StepsEditor({ meta, steps, onChange }: { meta: Meta; steps: Step[]; onChange: (s: Step[]) => void }) {
  return (
    <div className="office-steps-ed">
      {steps.map((s, i) => (
        <div key={i} className="row">
          <span>{i + 1}.</span>
          <select value={s.user_id ? `u:${s.user_id}` : s.role ? `r:${s.role}` : ""} onChange={(e) => { const v = e.target.value; const n = [...steps];
            n[i] = { ...s, user_id: v.startsWith("u:") ? v.slice(2) : null, role: v.startsWith("r:") ? v.slice(2) : null }; onChange(n); }}>
            <option value="">кто согласует…</option>
            <optgroup label="Любой сотрудник с ролью">{Object.entries(meta.roles).filter(([k]) => !["admin", "observer"].includes(k)).map(([k, v]) => <option key={k} value={`r:${k}`}>{v}</option>)}</optgroup>
            <optgroup label="Сотрудник">{meta.users.map((u) => <option key={u.id} value={`u:${u.id}`}>{u.name}</option>)}</optgroup>
          </select>
          <input value={s.title || ""} placeholder="что проверяет (необязательно)" onChange={(e) => { const n = [...steps]; n[i] = { ...s, title: e.target.value }; onChange(n); }} />
          <a href="#" onClick={(e) => { e.preventDefault(); onChange(steps.filter((_, j) => j !== i)); }}>убрать</a>
        </div>
      ))}
      <a href="#" onClick={(e) => { e.preventDefault(); onChange([...steps, {}]); }}>+ согласующий</a>
    </div>
  );
}

/** Отправить черновик на согласование: типовая схема (можно дополнить) или свой маршрут. */
function ApprovalStart({ d, meta, onDone }: { d: Doc; meta: Meta; onDone: (d: Doc) => void }) {
  const routes = routesFor(meta, d);
  const [open, setOpen] = useState(false);
  const [rid, setRid] = useState(routes.find((r) => r.required)?.id || routes[0]?.id || "");
  const [mode, setMode] = useState("seq");
  const [steps, setSteps] = useState<Step[]>([]);
  if (!open) return <div className="office-acts"><button className="btn ghost" onClick={() => setOpen(true)}>На согласование…</button>
    {routes.length > 0 && <span className="muted">типовые схемы: {routes.map((r) => r.name + (r.required ? " (обязательна)" : "")).join(", ")}</span>}</div>;
  const ok = (rid || steps.length > 0) && steps.every((s) => s.user_id || s.role);
  return (
    <div className="office-form office-box">
      <b>Отправить на согласование</b>
      <div className="grid2">
        <label>Схема<select value={rid} onChange={(e) => setRid(e.target.value)}>
          {routes.map((r) => <option key={r.id} value={r.id}>{r.name}{r.required ? " — обязательна" : ""}</option>)}<option value="">свой маршрут</option></select></label>
        {!rid && <label>Порядок<select value={mode} onChange={(e) => setMode(e.target.value)}><option value="seq">по очереди</option><option value="par">одновременно</option></select></label>}
        {rid && <div className="muted">{routes.find((r) => r.id === rid)?.steps.map((s, i) => `${i + 1}. ${s.user_id ? meta.users.find((u) => u.id === s.user_id)?.name : meta.roles[s.role || ""]}${s.title ? ` (${s.title})` : ""}`).join("; ")}</div>}
      </div>
      <div className="muted">{rid ? "Добавить согласующих к схеме:" : "Согласующие:"}</div>
      <StepsEditor meta={meta} steps={steps} onChange={setSteps} />
      <div className="row">
        <button className="btn" disabled={!ok} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/approval`, { route_id: rid || null, mode, steps }).then(onDone)}>Отправить</button>
        <button className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
      </div>
    </div>
  );
}

function Comments({ d, onDone }: { d: Doc; onDone: (d: Doc) => void }) {
  const [t, setT] = useState("");
  const list = d.comments || [];
  return (
    <div className="office-comments">
      <b>Комментарии{list.length ? ` (${list.length})` : ""}</b>
      {list.map((c) => <div key={c.id}><span className="muted">{c.author}, {new Date(c.at).toLocaleString("ru-RU")}:</span> {c.text}</div>)}
      <div className="row">
        <input value={t} onChange={(e) => setT(e.target.value)} placeholder="вопрос, пояснение, где лежит оригинал…" onKeyDown={(e) => { if (e.key === "Enter" && t.trim()) api.post<Doc>(`/api/office/docs/${d.id}/comments`, { text: t }).then((x) => { setT(""); onDone(x); }); }} />
        <button className="btn ghost" disabled={!t.trim()} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/comments`, { text: t }).then((x) => { setT(""); onDone(x); })}>Добавить</button>
      </div>
    </div>
  );
}

function NewReply({ parent, meta, onDone, onCancel }: { parent: Doc; meta: Meta; onDone: (d: Doc) => void; onCancel: () => void }) {
  const [subject, setSubject] = useState(`Ответ на ${parent.sender_number ? `исх. № ${parent.sender_number}` : parent.number}${parent.subject ? ` — ${parent.subject}` : ""}`);
  const [body, setBody] = useState("");
  return (
    <div className="office-form">
      <label>Тема<input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
      <label>Текст ответа<textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <div className="row">
        <button className="btn" disabled={!subject.trim()} onClick={() => api.post<Doc>("/api/office/docs", { entity_id: parent.entity_id, kind: "out", subject, body, reply_to_id: parent.id }).then(onDone)}>Сохранить черновик ответа</button>
        <button className="btn ghost" onClick={onCancel}>Отмена</button>
        <span className="muted">Адресат, сделка и договор возьмутся из входящего ({meta.kinds.in.toLowerCase()} {parent.number}); номер — при подписании</span>
      </div>
    </div>
  );
}

function Signers({ meta, onChange }: { meta: Meta; onChange: (m: Meta) => void }) {
  const empty = { entity_id: meta.entities[0]?.id || "", full_name: "", position: "", basis: "", valid_from: "", valid_to: "", methods: ["manual"] as string[], is_active: true };
  const [f, setF] = useState<typeof empty & { id?: string }>(empty);
  const save = () => {
    const body = { ...f, valid_from: f.valid_from || null, valid_to: f.valid_to || null };
    (f.id ? api.patch<Meta>(`/api/office/signers/${f.id}`, body) : api.post<Meta>("/api/office/signers", body)).then((m) => { onChange(m); setF(empty); });
  };
  return (
    <div>
      <table className="office-table">
        <thead><tr><th>Юр. лицо</th><th>Подписант</th><th>Основание</th><th>Полномочия</th><th>Способы подписи</th><th /></tr></thead>
        <tbody>
          {meta.signers.length === 0 && <tr><td colSpan={6} className="muted">Подписантов нет — добавьте ниже</td></tr>}
          {meta.signers.map((s) => (
            <tr key={s.id} className={s.valid_today ? "" : "muted"}>
              <td>{meta.entities.find((e) => e.id === s.entity_id)?.prefix}</td><td>{s.position} <b>{s.full_name}</b></td><td>{s.basis}</td>
              <td>{s.valid_from ? `с ${fmtDate(s.valid_from)} ` : ""}{s.valid_to ? `по ${fmtDate(s.valid_to)}` : ""}{!s.valid_today && " · не действует"}</td>
              <td>{s.methods.map((m) => meta.sign_methods[m]).join(", ")}</td>
              <td><a href="#" onClick={(e) => { e.preventDefault(); setF({ ...empty, ...s, valid_from: s.valid_from || "", valid_to: s.valid_to || "" }); }}>изменить</a></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="card office-form">
        <h2>{f.id ? "Изменить подписанта" : "Новый подписант"}</h2>
        <div className="grid2">
          <label>Юр. лицо<select value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })}>{meta.entities.map((e) => <option key={e.id} value={e.id}>{e.prefix} · {e.name}</option>)}</select></label>
          <label>ФИО<input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
          <label>Должность<input value={f.position} onChange={(e) => setF({ ...f, position: e.target.value })} /></label>
          <label>Основание подписи<input value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })} placeholder="Устав / Доверенность № … от …" /></label>
          <label>Полномочия с<input type="date" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></label>
          <label>по<input type="date" value={f.valid_to} onChange={(e) => setF({ ...f, valid_to: e.target.value })} /></label>
          <div className="span2 row">Способы подписи:
            {Object.entries(meta.sign_methods).map(([k, v]) => <label key={k}><input type="checkbox" checked={f.methods.includes(k)} onChange={(e) => setF({ ...f, methods: e.target.checked ? [...f.methods, k] : f.methods.filter((x) => x !== k) })} /> {v}</label>)}
            <span className="muted">факсимиле и ЭЦП заработают следующими этапами</span>
          </div>
          <label><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> действует</label>
        </div>
        <div className="row"><button className="btn" disabled={f.full_name.trim().length < 2} onClick={save}>{f.id ? "Сохранить" : "Добавить"}</button>
          {f.id && <button className="btn ghost" onClick={() => setF(empty)}>Отмена</button>}</div>
      </div>
    </div>
  );
}

/** Типовые схемы согласования (ведёт ЗГД): для каких документов, кто согласует, по очереди или одновременно, обязательна ли. */
function Routes({ meta, onChange }: { meta: Meta; onChange: (m: Meta) => void }) {
  const empty: Omit<Route, "id"> & { id?: string } = { name: "", kinds: ["out"], categories: [], entity_id: null, mode: "seq", steps: [{}], required: false, is_active: true };
  const [f, setF] = useState(empty);
  const kinds = { out: "исходящие", order: "приказы", directive: "распоряжения", memo: "служебные записки", internal: "внутренние" } as Record<string, string>;
  const save = () => (f.id ? api.patch<Meta>(`/api/office/routes/${f.id}`, f) : api.post<Meta>("/api/office/routes", f)).then((m) => { onChange(m); setF(empty); });
  const who = (s: Step) => (s.user_id ? meta.users.find((u) => u.id === s.user_id)?.name : meta.roles[s.role || ""]) + (s.title ? ` (${s.title})` : "");
  return (
    <div>
      <h2>Схемы согласования</h2>
      <table className="office-table">
        <thead><tr><th>Схема</th><th>Документы</th><th>Юр. лицо</th><th>Согласующие</th><th>Обязательна</th><th /></tr></thead>
        <tbody>
          {meta.routes.length === 0 && <tr><td colSpan={6} className="muted">Схем нет — без схемы автор собирает маршрут сам</td></tr>}
          {meta.routes.map((r) => (
            <tr key={r.id} className={r.is_active ? "" : "muted"}>
              <td><b>{r.name}</b></td><td>{r.kinds.map((k) => kinds[k]).join(", ") || "все"}{r.categories.length ? ` · ${r.categories.map((c) => meta.order_categories[c] || c).join(", ")}` : ""}</td>
              <td>{meta.entities.find((e) => e.id === r.entity_id)?.prefix || "все"}</td>
              <td>{r.steps.map(who).join(r.mode === "seq" ? " → " : " + ")}</td><td>{r.required ? "да" : "нет"}</td>
              <td><a href="#" onClick={(e) => { e.preventDefault(); setF({ ...r }); }}>изменить</a></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="card office-form">
        <h2>{f.id ? "Изменить схему" : "Новая схема"}</h2>
        <div className="grid2">
          <label>Название<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Претензионные письма — юрист и ФД" /></label>
          <label>Юр. лицо<select value={f.entity_id || ""} onChange={(e) => setF({ ...f, entity_id: e.target.value || null })}><option value="">все</option>{meta.entities.map((e) => <option key={e.id} value={e.id}>{e.prefix} · {e.name}</option>)}</select></label>
          <div className="span2 row">Документы:
            {Object.entries(kinds).map(([k, v]) => <label key={k} className="row"><input type="checkbox" checked={f.kinds.includes(k)} onChange={(e) => setF({ ...f, kinds: e.target.checked ? [...f.kinds, k] : f.kinds.filter((x) => x !== k) })} /> {v}</label>)}
          </div>
          {f.kinds.includes("order") && <div className="span2 row">Вид приказа:
            {Object.entries(meta.order_categories).map(([k, v]) => <label key={k} className="row"><input type="checkbox" checked={f.categories.includes(k)} onChange={(e) => setF({ ...f, categories: e.target.checked ? [...f.categories, k] : f.categories.filter((x) => x !== k) })} /> {v}</label>)}
            <span className="muted">пусто — все</span></div>}
          <label>Порядок<select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="seq">по очереди</option><option value="par">одновременно</option></select></label>
          <div className="row"><label className="row"><input type="checkbox" checked={f.required} onChange={(e) => setF({ ...f, required: e.target.checked })} /> обязательна — без неё не подписать</label>
            <label className="row"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> действует</label></div>
        </div>
        <StepsEditor meta={meta} steps={f.steps} onChange={(steps) => setF({ ...f, steps })} />
        <div className="row"><button className="btn" disabled={f.name.trim().length < 2 || !f.steps.length || !f.steps.every((s) => s.user_id || s.role)} onClick={save}>{f.id ? "Сохранить" : "Добавить схему"}</button>
          {f.id && <button className="btn ghost" onClick={() => setF(empty)}>Отмена</button>}</div>
      </div>
    </div>
  );
}


/** Текст входящего (D-134): для поиска и ИИ, если скан без текстового слоя — делопроизводитель вставляет текст письма. */
function InText({ d, ed, onSave }: { d: Doc; ed: boolean; onSave: (body: string) => Promise<unknown> }) {
  const [t, setT] = useState(d.body || "");
  const [open, setOpen] = useState(false);
  return (
    <details className="office-box" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary><b>Текст письма</b> <span className="muted">{d.body ? `${d.body.length} знаков` : "не внесён — нужен для поиска и ИИ, если скан без текстового слоя"}</span></summary>
      {ed ? <div className="office-form"><textarea rows={8} value={t} onChange={(e) => setT(e.target.value)} placeholder="Скопируйте текст из письма или PDF" />
        <div className="row"><button className="btn ghost" disabled={t === (d.body || "")} onClick={() => onSave(t)}>Сохранить текст</button></div></div>
        : <div className="office-body">{d.body}</div>}
    </details>
  );
}

type CtlRow = { user_id: string | null; name: string; open: number; today: number; overdue: number; closed_30: number; on_time_30: number;
  late_docs: { id: string; number: string; subject: string; due_on: string; days: number }[] };

/** Панель «на контроле» (D-133): у кого сколько документов в работе, срок сегодня, просрочено; за 30 дней — закрыто и в срок. */
function Control({ onOpen }: { onOpen: (id: string) => void }) {
  const [data, setData] = useState<{ as_of: string; items: CtlRow[]; total: Omit<CtlRow, "user_id" | "name" | "late_docs"> } | null>(null);
  useEffect(() => { api.get<typeof data>("/api/office/control").then(setData); }, []);
  if (!data) return <div className="muted">Загрузка…</div>;
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "—");
  return (
    <div>
      <p className="muted">Напоминания уходят сами: исполнителю — за рабочий день до срока и в день срока; после срока — исполнителю, руководителю, наложившему резолюцию, и делопроизводителю, дальше раз в 3 рабочих дня.</p>
      <table className="office-table office-control">
        <thead><tr><th>Исполнитель</th><th className="num">В работе</th><th className="num">Срок сегодня</th><th className="num">Просрочено</th><th className="num">Закрыто за 30 дней</th><th className="num">В срок</th><th>Просроченные</th></tr></thead>
        <tbody>
          {data.items.length === 0 && <tr><td colSpan={7} className="muted">Документов в работе нет</td></tr>}
          {data.items.map((r) => (
            <tr key={r.user_id || "none"} className={r.overdue ? "late" : ""}>
              <td><b>{r.name}</b></td><td className="num">{r.open}</td><td className="num">{r.today || ""}</td><td className={"num" + (r.overdue ? " txt-bad" : "")}>{r.overdue || ""}</td>
              <td className="num">{r.closed_30 || ""}</td><td className="num">{pct(r.on_time_30, r.closed_30)}</td>
              <td>{r.late_docs.map((x) => <div key={x.id}><a href="#" onClick={(e) => { e.preventDefault(); onOpen(x.id); }}>{x.number}</a> {x.subject} <span className="txt-bad">· {x.days} р. д.</span></div>)}</td>
            </tr>
          ))}
          <tr><td><b>Итого</b></td><td className="num"><b>{data.total.open}</b></td><td className="num">{data.total.today}</td><td className="num txt-bad">{data.total.overdue}</td>
            <td className="num">{data.total.closed_30}</td><td className="num">{pct(data.total.on_time_30, data.total.closed_30)}</td><td /></tr>
        </tbody>
      </table>
    </div>
  );
}
