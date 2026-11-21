import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import { Crumbs, useQueryParam } from "../components/Levels";
import { askPrompt } from "../components/Dialogs";
import type { Me } from "../types";

/** Канцелярия (D-128): журналы входящих и исходящих писем, приказов, распоряжений, служебных записок и внутренних документов.
 *  Регистрирует делопроизводитель (офис-менеджер) или ЗГД; исполнитель видит свои документы; подписантов ведёт ЗГД. */

type Meta = {
  entities: { id: string; code: string; name: string; prefix: string }[];
  kinds: Record<string, string>; order_categories: Record<string, string>; internal_categories: Record<string, string>;
  letter_types: Record<string, string>; channels: Record<string, string>; statuses: Record<string, string>; sign_methods: Record<string, string>;
  signers: { id: string; entity_id: string; full_name: string; position: string; basis: string; valid_from: string | null; valid_to: string | null; methods: string[]; is_active: boolean; valid_today: boolean }[];
  users: { id: string; name: string }[]; can_edit: boolean; can_hr: boolean; can_signers: boolean;
};
type Doc = {
  id: string; entity_id: string; entity: string; entity_prefix: string; kind: string; kind_title: string; category: string; number: string; reg_date: string | null;
  status: string; status_title: string; subject: string; summary: string; letter_type: string; channel: string; counterparty: string; contact: string;
  sender_number: string; sender_date: string | null; due_on: string | null; overdue: boolean; executor_id: string | null; executor: string;
  co_executor_ids: string[]; deal_ids: string[]; deal_section: string; reply_to_id: string | null; signer_id: string | null; signer: string;
  signed_on: string | null; sign_method: string; sent_on: string | null; effective_on: string | null; closed_on: string | null; close_note: string;
  files: { id: string; name: string; size: number; kind: string; at: string }[]; can_edit: boolean; is_executor: boolean;
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
  const [creating, setCreating] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const journal = (me.perms?.office ?? 0) >= 1 || (me.perms?.office_hr ?? 0) >= 1;
  const cur = TABS.find((t) => t.key === tab) || TABS[0];
  const load = () => {
    const p = new URLSearchParams();
    if (!journal) p.set("mine", "true"); else if (tab !== "signers") p.set("kind", cur.kinds);
    if (q.trim()) p.set("q", q.trim());
    if (onlyOpen) p.set("status", "registered,assigned,in_work,draft");
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
          {meta.can_signers && <button className={tab === "signers" ? "active" : ""} onClick={() => { setTab("signers"); setDocId(""); }}>Подписанты</button>}
        </div>
      ) : <div className="muted">Документы, где вы исполнитель или автор. Журналы ведёт делопроизводитель.</div>}
      {tab === "signers" && meta.can_signers ? <Signers meta={meta} onChange={setMeta} /> : (
        <>
          <div className="office-bar">
            <input placeholder="№, тема, контрагент" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
            <label><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> только в работе и черновики</label>
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
                  <td>{d.subject}</td><td>{d.counterparty}</td><td>{d.kind === "in" ? d.executor : d.signer || d.executor}</td>
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
  const [f, setF] = useState<Record<string, string>>({ entity_id: ru?.id || "", kind, category: kind === "order" ? (meta.can_edit ? "od" : "hr") : "",
    letter_type: kind === "in" ? "request" : "", channel: kind === "in" ? "email" : "", subject: "", summary: "", body: "", counterparty: "", contact: "",
    sender_number: "", sender_date: "", received_on: new Date().toISOString().slice(0, 10), due_on: "", executor_id: "", deal: "", deal_section: "sale" });
  const [busy, setBusy] = useState(false);
  const [deals, setDeals] = useState<{ id: string; number: string; client_name?: string }[]>([]);
  useEffect(() => { api.get<{ id: string; number: string; client_name?: string }[]>("/api/deals").then(setDeals).catch(() => setDeals([])); }, []);
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setBusy(true);
    try {
      const d = await api.post<Doc>("/api/office/docs", { ...f, sender_date: f.sender_date || null, received_on: kind === "in" ? f.received_on : null,
        due_on: f.due_on || null, executor_id: f.executor_id || null, deal_ids: f.deal ? [f.deal] : [], deal_section: f.deal ? f.deal_section : "" });
      onDone(d);
    } finally { setBusy(false); }
  };
  const letter = kind === "in" || kind === "out";
  return (
    <div className="card office-form">
      <h2>{kind === "in" ? "Регистрация входящего" : `Новый документ: ${meta.kinds[kind].toLowerCase()}`}</h2>
      <div className="grid2">
        <label>Юр. лицо<select value={f.entity_id} onChange={set("entity_id")}>{meta.entities.map((e) => <option key={e.id} value={e.id}>{e.prefix} · {e.name}</option>)}</select></label>
        {kind === "order" && <label>Вид приказа<select value={f.category} onChange={set("category")}>
          {Object.entries(meta.order_categories).filter(([k]) => k !== "hr" || meta.can_hr).filter(([k]) => k === "hr" || meta.can_edit).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {kind === "internal" && <label>Вид документа<select value={f.category} onChange={set("category")}><option value="">—</option>{Object.entries(meta.internal_categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {kind === "in" && <label>Вид письма<select value={f.letter_type} onChange={set("letter_type")}>{Object.entries(meta.letter_types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        {letter && <label>{kind === "in" ? "Как поступило" : "Как отправим"}<select value={f.channel} onChange={set("channel")}><option value="">—</option>{Object.entries(meta.channels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        <label className="span2">Тема<input value={f.subject} onChange={set("subject")} /></label>
        {letter && <label>{kind === "in" ? "Отправитель" : "Адресат"}<input value={f.counterparty} onChange={set("counterparty")} placeholder="организация" /></label>}
        {letter && <label>Контакт<input value={f.contact} onChange={set("contact")} placeholder="ФИО, должность, email" /></label>}
        {kind === "in" && <label>Исх. № отправителя<input value={f.sender_number} onChange={set("sender_number")} /></label>}
        {kind === "in" && <label>от<input type="date" value={f.sender_date} onChange={set("sender_date")} /></label>}
        {kind === "in" && <label>Дата поступления<input type="date" value={f.received_on} onChange={set("received_on")} /></label>}
        {kind === "in" && <label>Срок ответа<input type="date" value={f.due_on} onChange={set("due_on")} />
          <span className="muted">{f.letter_type === "gov" ? "обязательно — из письма" : f.due_on ? "" : `по умолчанию: ${({ request: "2", claim: "5 (или по договору)", complaint: "5" } as Record<string, string>)[f.letter_type] ?? "—"} раб. дн.`}</span></label>}
        {kind === "in" && <label>Исполнитель<select value={f.executor_id} onChange={set("executor_id")}><option value="">назначить позже</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
        {letter && <label>Сделка<select value={f.deal} onChange={set("deal")}><option value="">без сделки</option>{deals.map((d) => <option key={d.id} value={d.id}>{d.number}{d.client_name ? ` · ${d.client_name}` : ""}</option>)}</select></label>}
        {letter && f.deal && <label>Раздел сделки<select value={f.deal_section} onChange={set("deal_section")}>
          {Object.entries({ "": "Общее", sale: "Продажа", logistics: "Логистика", service: "Сервис", legal: "Договор и юр.", execution: "Исполнение", finance: "Финансы" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
        <label className="span2">{kind === "in" ? "Краткое содержание" : "Краткое содержание (для журнала)"}<textarea rows={2} value={f.summary} onChange={set("summary")} /></label>
        {kind !== "in" && <label className="span2">Текст документа<textarea rows={6} value={f.body} onChange={set("body")} placeholder="Бланк юр. лица подставится, когда будут загружены шаблоны" /></label>}
      </div>
      <div className="row">
        <button className="btn" disabled={busy || !f.subject.trim()} onClick={save}>{kind === "in" ? "Зарегистрировать — номер сразу" : "Сохранить черновик"}</button>
        <button className="btn ghost" onClick={onCancel}>Отмена</button>
        <span className="muted">{kind === "in" ? "Номер присваивается при регистрации" : "Номер присваивается при подписании"}</span>
      </div>
    </div>
  );
}

function DocCard({ id, meta, me, onClose, onChanged, onOpen }: { id: string; meta: Meta; me: Me; onClose: () => void; onChanged: () => void; onOpen: (id: string) => void }) {
  const [d, setD] = useState<Doc | null>(null);
  const [exec, setExec] = useState("");
  const [due, setDue] = useState("");
  const [signer, setSigner] = useState("");
  const [reply, setReply] = useState(false);
  useEffect(() => { api.get<Doc>(`/api/office/docs/${id}`).then((x) => { setD(x); setExec(x.executor_id || ""); setDue(x.due_on || ""); }); }, [id]);
  if (!d) return null;
  const upd = (x: Doc) => { setD(x); onChanged(); };
  const act = (action: string, note = "") => api.post<Doc>(`/api/office/docs/${d.id}/action`, { action, note }).then(upd);
  const signers = meta.signers.filter((s) => s.entity_id === d.entity_id && s.valid_today);
  const ed = meta.can_edit || (d.kind === "order" && d.category === "hr" && meta.can_hr);
  return (
    <div className="card office-card">
      <div className="row">
        <h2>{d.number || "Черновик"} · {d.kind_title}{d.kind === "order" && d.category ? ` ${meta.order_categories[d.category]}` : ""}</h2>
        <span className={"badge" + (d.overdue ? " bad" : "")}>{d.status_title}</span>
        <a href="#" className="push" onClick={(e) => { e.preventDefault(); onClose(); }}>закрыть ✕</a>
      </div>
      <div className="office-grid">
        <div><span className="muted">Юр. лицо</span> {d.entity_prefix} · {d.entity}</div>
        {d.reg_date && <div><span className="muted">Дата</span> {fmtDate(d.reg_date)}</div>}
        <div className="span2"><span className="muted">Тема</span> <b>{d.subject}</b></div>
        {d.counterparty && <div><span className="muted">{d.kind === "in" ? "Отправитель" : "Адресат"}</span> {d.counterparty}{d.contact ? ` · ${d.contact}` : ""}</div>}
        {d.sender_number && <div><span className="muted">Исх. № отправителя</span> {d.sender_number}{d.sender_date ? ` от ${fmtDate(d.sender_date)}` : ""}</div>}
        {d.letter_type && <div><span className="muted">Вид</span> {meta.letter_types[d.letter_type]}{d.channel ? ` · ${meta.channels[d.channel]}` : ""}</div>}
        {d.due_on && <div><span className="muted">Срок</span> <b className={d.overdue ? "txt-bad" : ""}>{fmtDate(d.due_on)}</b></div>}
        {d.executor && <div><span className="muted">Исполнитель</span> {d.executor}</div>}
        {d.signer && <div><span className="muted">Подписал</span> {d.signer} · {fmtDate(d.signed_on)} · {meta.sign_methods[d.sign_method]}</div>}
        {d.sent_on && <div><span className="muted">Отправлено</span> {fmtDate(d.sent_on)}</div>}
        {d.effective_on && <div><span className="muted">Вступает в силу</span> {fmtDate(d.effective_on)}</div>}
        {d.deals && d.deals.length > 0 && <div><span className="muted">Сделка</span> {d.deals.map((x) => <a key={x.id} href={`/deals/${x.id}?tab=feed`}>{x.number} </a>)}</div>}
        {d.reply_to && <div><span className="muted">В ответ на</span> <a href="#" onClick={(e) => { e.preventDefault(); onOpen(d.reply_to!.id); }}>{d.reply_to.number}</a></div>}
        {d.replies && d.replies.length > 0 && <div><span className="muted">Ответ</span> {d.replies.map((r) => <a key={r.id} href="#" onClick={(e) => { e.preventDefault(); onOpen(r.id); }}>{r.number || "черновик"} </a>)}</div>}
        {d.close_note && <div className="span2"><span className="muted">Исполнение</span> {d.close_note}</div>}
        {d.summary && <div className="span2"><span className="muted">Кратко</span> {d.summary}</div>}
        {d.body && <div className="span2 office-body">{d.body}</div>}
      </div>
      <div className="office-files">
        {d.files.map((f) => <a key={f.id} href={`/api/office/docs/${d.id}/files/${f.id}`} target="_blank" rel="noreferrer">📎 {f.name} <span className="muted">({FILE_KIND[f.kind] || f.kind})</span></a>)}
        {(ed || d.is_executor) && <label className="btn ghost">+ файл<input type="file" hidden onChange={(e) => { const x = e.target.files?.[0]; if (x) api.upload<Doc>(`/api/office/docs/${d.id}/files`, x, { kind: d.kind === "in" ? "original" : d.status === "draft" ? "draft" : "scan" }).then(upd); }} /></label>}
      </div>
      <div className="office-acts">
        {ed && ["registered", "assigned", "in_work"].includes(d.status) && (
          <span className="row">
            <select value={exec} onChange={(e) => setExec(e.target.value)}><option value="">исполнитель…</option>{meta.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
            <button className="btn ghost" disabled={!exec} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/assign`, { executor_id: exec, due_on: due || null }).then(upd)}>{d.executor_id ? "Переназначить" : "Назначить"}</button>
          </span>
        )}
        {d.is_executor && d.status === "assigned" && <button className="btn" onClick={() => act("take")}>Взять в работу</button>}
        {d.kind === "in" && (d.is_executor || ed) && ["assigned", "in_work", "registered"].includes(d.status) && !reply && <button className="btn" onClick={() => setReply(true)}>Подготовить ответ</button>}
        {(d.is_executor || ed) && ["registered", "assigned", "in_work"].includes(d.status) && (
          <button className="btn ghost" onClick={async () => { const n = await askPrompt("Как исполнено? Например: «ответ не требуется: …» или «ответ дан по телефону»"); if (n) act("close", n); }}>Закрыть без письма</button>)}
        {d.status === "draft" && (ed || d.can_edit) && d.kind !== "in" && (
          <span className="row">
            <select value={signer} onChange={(e) => setSigner(e.target.value)}><option value="">подписант…</option>
              {signers.map((s) => <option key={s.id} value={s.id}>{s.position} {s.full_name}</option>)}</select>
            <button className="btn" disabled={!signer} onClick={() => api.post<Doc>(`/api/office/docs/${d.id}/sign`, { signer_id: signer, method: "manual" }).then(upd)}>Подписано — присвоить номер</button>
            {signers.length === 0 && <span className="muted">нет действующих подписантов у этого юр. лица — добавляет ЗГД во вкладке «Подписанты»</span>}
          </span>
        )}
        {d.kind === "out" && d.status === "signed" && (ed || d.can_edit) && <button className="btn" onClick={() => act("sent")}>Отправлено</button>}
        {d.kind !== "in" && d.kind !== "out" && d.status === "signed" && ed && <button className="btn" onClick={() => act("in_force")}>Ввести в действие</button>}
        {ed && !["closed", "cancelled"].includes(d.status) && (
          <button className="btn ghost" onClick={async () => { const n = await askPrompt("Причина отмены (номер останется в журнале с отметкой)"); if (n) act("cancel", n); }}>Отменить</button>)}
      </div>
      {reply && <NewReply parent={d} meta={meta} onDone={(x) => { setReply(false); onOpen(x.id); onChanged(); }} onCancel={() => setReply(false)} />}
      {d.history && d.history.length > 0 && (
        <details><summary className="muted">История ({d.history.length})</summary>
          {d.history.map((h, i) => <div key={i} className="muted">{new Date(h.at).toLocaleString("ru-RU")} · {h.action}{h.note ? ` · ${h.note}` : ""}</div>)}
        </details>
      )}
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
