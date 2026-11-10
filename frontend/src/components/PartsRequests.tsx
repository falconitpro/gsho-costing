import { MeContext } from "../App";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Me } from "../types";

// Заявки клиентов на запчасти: попозиционный учёт, подбор по каталогу, цена, маржа и визы (ТЗ запчастей, разделы 5–6).

type Line = {
  id: string; no: number; parent_line_id: string | null; raw_article: string; raw_name: string; qty: number; unit: string;
  part: { id: string; article: string; name_ru: string; manufacturer: string } | null; match_kind: string; candidates: string[];
  source: string; lead_days: number | null; price: number | null; currency: string; price_source: string; price_rub: number | null; sum_rub: number | null;
  available: number | null; status: string; comment: string; threshold_pct: number | null; approval: string | null;
  cost?: number | null; margin_pct?: number | null; profit_rub?: number | null; cost_note?: string;
  eta?: string | null; shipped_qty?: number; supplier_id?: string | null; rfq_line_id?: string | null; quote_price?: number | null; quote_currency?: string;
};
type Req = {
  id: string; number: string; client_id: string; client_name: string; manager_name: string; machine: string; urgent: boolean; warranty: boolean;
  contact: string; channel: string; comment: string; closed: string; close_reason: string; created_at: string; status_text: string;
  stage?: string; stage_title?: string; deal?: { id: string; number: string } | null;
  lines: Line[]; approvals: { level: string; name: string; at: string; comment: string }[];
  kps: Kp[];
  summary: { lines: number; sum_rub: number; profit_rub?: number | null; margin_pct?: number | null; handling_fee_rub: number; approval_needed: string | null; uncosted_lines: number };
};
type Kp = { id: string; number: string; revision_no: number; currency: string; total: number; handling_fee: number; handling_fee_removed_by: string | null;
  valid_until: string; expired: boolean; created_at: string; sent_at: string | null; comment: string;
  lines: { line_id: string; article: string; name: string; qty: number; unit: string; price: number; sum: number; lead: string }[];
  changes: { article: string; change: string; old?: unknown; new?: unknown }[] };
type Row = { id: string; number: string; client_name: string; machine: string; urgent: boolean; created_at: string; lines: number; status_text: string; price_rub: number };
type Meta = { channels: Record<string, string>; line_status: Record<string, string>; close_kinds: Record<string, string>; can_create: boolean };
type Ref = { id: string; name: string };

const MATCH: Record<string, string> = { article: "по артикулу", oem: "по OEM", supplier: "по номеру поставщика", analog: "аналог", manual: "вручную" };
const PSRC: Record<string, string> = { contract: "договорная", pricelist: "прайс", calc: "расчёт", manual: "вручную" };
const CUR = ["RUB", "EUR", "CNY", "USD", "KGS"];
const DELETABLE = ["new", "rfq", "quoted", "unavailable", "replaced", "cancelled"];   // как DELETABLE_LINE на сервере

export default function PartsRequests({ me }: { me: Me }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState("");
  const [clients, setClients] = useState<Ref[]>([]);
  void me;

  const load = () => api.get<Row[]>(`/api/parts/requests?open_only=${openOnly}&q=${encodeURIComponent(q)}`).then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => { api.get<Meta>("/api/parts/requests/meta").then(setMeta); api.get<Ref[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); }, []);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [openOnly, q]);
  const nav = useNavigate();
  const open = (id: string) => nav(`/deals/parts/${id}`);

  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input placeholder="номер, клиент, машина" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="row"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> только открытые</label>
          {meta?.can_create && <button className="btn add" style={{ marginLeft: "auto" }} onClick={() => setCreating(true)}>Заявка</button>}
        </div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Номер</th><th>Дата</th><th>Клиент</th><th>Машина</th><th className="num">Строк</th><th>Статус</th><th className="num">Сумма, ₽</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => open(r.id)}>
              <td><b>{r.number}</b>{r.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td><td>{fmtDate(r.created_at)}</td>
              <td>{r.client_name}</td><td>{r.machine}</td><td className="num">{r.lines}</td><td>{r.status_text}</td><td className="num">{r.price_rub ? fmtRub(r.price_rub) : "—"}</td></tr>))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">Заявок нет</td></tr>}</tbody>
        </table>
      </div>
      {creating && meta && <CreateForm meta={meta} clients={clients} onCreated={(r) => { setCreating(false); nav(`/deals/parts/${r.id}`); }} onCancel={() => setCreating(false)} setMsg={setMsg} />}
    </>
  );
}

export function CreateForm({ meta, clients, onCreated, onCancel, setMsg }: { meta: Meta; clients: Ref[]; onCreated: (r: Req) => void; onCancel: () => void; setMsg: (s: string) => void }) {
  const [f, setF] = useState({ client_id: "", contact: "", channel: "email", machine: "", urgent: false, warranty: false, comment: "", lines_text: "" });
  async function save() {
    try { onCreated(await api.post<Req>("/api/parts/requests", f)); setMsg("Заявка создана, строки подобраны по каталогу"); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Новая заявка</h2>
      <div className="grid cols-3">
        <label className="f">Клиент *<select value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })}><option value="">— выберите —</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="f">Контакт<input value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} /></label>
        <label className="f">Канал<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{Object.entries(meta.channels).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Машина клиента<input value={f.machine} onChange={(e) => setF({ ...f, machine: e.target.value })} placeholder="напр. Sandvik LH410, зав. № …" /></label>
        <label className="f">Машина стоит<div className="row"><input type="checkbox" checked={f.urgent} onChange={(e) => setF({ ...f, urgent: e.target.checked })} /> аварийная заявка</div></label>
        <label className="f">Гарантия<div className="row"><input type="checkbox" checked={f.warranty} onChange={(e) => setF({ ...f, warranty: e.target.checked })} /> гарантийная замена</div></label>
      </div>
      <label className="f" style={{ marginTop: 6 }}>Строки заявки — вставьте из письма или Excel: артикул, наименование, количество (через Tab или «;»)
        <textarea rows={8} value={f.lines_text} onChange={(e) => setF({ ...f, lines_text: e.target.value })} placeholder={"554831\tВедущий мост\t1\n522608\tТопливный фильтр\t12"} /></label>
      <label className="f">Комментарий<input value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} /></label>
      <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save} disabled={!f.client_id || !f.lines_text.trim()}>Создать и подобрать</button><button className="btn ghost" onClick={onCancel}>отмена</button></div>
    </div>
  );
}

function RequestCard({ r, meta, seesCost, approver, act }: { r: Req; meta: Meta; seesCost: boolean; approver: boolean;
  act: <T>(fn: () => Promise<T>, ok?: string) => Promise<void> }) {
  const [add, setAdd] = useState("");
  const [closeKind, setCloseKind] = useState("");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  const [kpCur, setKpCur] = useState("RUB");
  const [noFee, setNoFee] = useState(false);
  const [pick, setPick] = useState<{ line: string; q: string; items: { id: string; article: string; name_ru: string; manufacturer: string }[] } | null>(null);
  const [sups, setSups] = useState<{ id: string; name: string; short_name: string; effective_currency: string }[]>([]);
  useEffect(() => { api.get<typeof sups>("/api/refs/factories?supplies=parts").then(setSups).catch(() => setSups([])); }, []);
  const base = `/api/parts/requests/${r.id}`;
  const patch = (l: Line, body: Record<string, unknown>) => act(() => api.patch<Req>(`${base}/lines/${l.id}`, body));
  async function search(line: string, q: string) {
    const res = await api.get<{ items: { id: string; article: string; name_ru: string; manufacturer: string }[] }>(`/api/parts/catalog?q=${encodeURIComponent(q)}&limit=10`);
    setPick({ line, q, items: res.items });
  }
  const closed = !!r.closed;
  const kpSel = sel.filter((id) => r.lines.find((l) => l.id === id)?.price != null);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{r.number} · {r.client_name} {r.urgent && <span className="badge bad">машина стоит</span>} {r.warranty && <span className="badge">гарантия</span>}{r.stage_title && <span className="badge" style={{ marginLeft: 6 }}>{r.stage_title}</span>}{r.deal && <Link to={`/deals/${r.deal.id}`} className="badge" style={{ marginLeft: 6 }} title="сделка ГШО, по которой поставлена машина">сделка {r.deal.number}</Link>}</h2>
        <span className="muted">{r.status_text}</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>{[r.machine, r.contact, meta.channels[r.channel], r.manager_name && `менеджер ${r.manager_name}`].filter(Boolean).join(" · ")}{r.comment && ` · ${r.comment}`}</p>
      <div className="row" style={{ marginBottom: 6 }}>
        {!closed && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/match`), "Подбор повторён")}>Подобрать по каталогу</button>}
        {!closed && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/reprice`), "Цены пересчитаны")}>Пересчитать цены</button>}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th title="отметить для КП или запроса поставщикам">✓</th><th>№</th><th>Запрос клиента</th><th>Подобрано</th><th className="num">Кол-во</th><th>Источник</th><th className="num">Срок, дн</th><th className="num">Цена</th><th className="num">Сумма, ₽</th>
            {seesCost && <><th className="num">Себест., ₽/ед</th><th className="num">Маржа</th></>}<th className="num">Порог</th><th>Статус</th></tr></thead>
          <tbody>{r.lines.map((l) => (
            <tr key={l.id} style={{ opacity: ["cancelled", "unavailable"].includes(l.status) ? 0.5 : 1 }}>
              <td><input type="checkbox" disabled={closed || ["cancelled", "unavailable"].includes(l.status)} checked={sel.includes(l.id)}
                onChange={(e) => setSel(e.target.checked ? [...sel, l.id] : sel.filter((x) => x !== l.id))} /></td>
              <td>{l.no}{l.parent_line_id && <div className="muted" style={{ fontSize: 11 }}>часть строки</div>}
                {!closed && r.lines.length > 1 && DELETABLE.includes(l.status) && (
                  <div><a href="#" style={{ fontSize: 11, color: "var(--bad)" }} title="Удалить ошибочно введённую позицию"
                    onClick={(e) => { e.preventDefault(); if (confirm(`Удалить позицию ${l.no} «${l.raw_article || l.raw_name}»?`)) act(() => api.del<Req>(`${base}/lines/${l.id}`), "Позиция удалена"); }}>удалить</a></div>)}</td>
              <td><b>{l.raw_article}</b><div className="muted" style={{ fontSize: 11 }}>{l.raw_name}</div></td>
              <td>{l.part ? <>{l.part.article} {l.part.name_ru}<div className="muted" style={{ fontSize: 11 }}>{MATCH[l.match_kind] || l.match_kind}{l.part.manufacturer && ` · ${l.part.manufacturer}`}</div></>
                : <span className="badge bad">{l.candidates?.length ? `несколько вариантов (${l.candidates.length})` : "не найдено"}</span>}
                {!closed && <div><a href="#" style={{ fontSize: 11 }} onClick={(e) => { e.preventDefault(); search(l.id, l.raw_article || l.raw_name); }}>подобрать вручную</a></div>}
                {pick?.line === l.id && (
                  <div style={{ border: "1px solid var(--line)", padding: 4, marginTop: 4 }}>
                    <input value={pick.q} onChange={(e) => search(l.id, e.target.value)} />
                    {pick.items.map((p) => <div key={p.id}><a href="#" onClick={(e) => { e.preventDefault(); setPick(null); patch(l, { part_id: p.id }); }}>{p.article} {p.name_ru}{p.manufacturer && ` (${p.manufacturer})`}</a></div>)}
                    {pick.items.length === 0 && <div className="muted">ничего не найдено — заведите карточку в «Каталоге»</div>}
                  </div>)}</td>
              <td className="num"><input type="number" style={{ width: 60 }} defaultValue={l.qty} disabled={closed} onBlur={(e) => Number(e.target.value) !== l.qty && patch(l, { qty: Number(e.target.value) })} /> {l.unit}
                {l.available != null && <div className="muted" style={{ fontSize: 11 }}>на складе {l.available}</div>}</td>
              <td><select value={l.source} disabled={closed || !l.part} onChange={(e) => patch(l, { source: e.target.value })}><option value="">—</option><option value="stock">со склада</option><option value="order">под заказ</option></select>
                {!closed && l.qty > 1 && <div><a href="#" style={{ fontSize: 11 }} onClick={(e) => { e.preventDefault(); const v = prompt("Сколько отделить в новую строку?", "1"); if (v) act(() => api.post<Req>(`${base}/lines/${l.id}/split`, { qty: Number(v) })); }}>разделить</a></div>}</td>
              <td className="num"><input type="number" style={{ width: 60 }} defaultValue={l.lead_days ?? ""} disabled={closed} onBlur={(e) => patch(l, { lead_days: e.target.value ? Number(e.target.value) : null })} /></td>
              <td className="num"><input type="number" style={{ width: 90 }} defaultValue={l.price ?? ""} disabled={closed} key={`${l.id}-${l.price}`}
                onBlur={(e) => { const v = e.target.value ? Number(e.target.value) : 0; if (v !== (l.price || 0)) patch(l, { price: v, currency: l.currency }); }} />
                <select value={l.currency} disabled={closed} onChange={(e) => patch(l, { currency: e.target.value })}>{CUR.map((c) => <option key={c}>{c}</option>)}</select>
                {!closed && l.part && (
                  <div><select style={{ maxWidth: 150, fontSize: 11 }} title="Поставщик: от него зависит валюта по умолчанию (GRP — евро, Китай — CNY, Россия — рубли)" value={l.supplier_id || ""}
                    onChange={(e) => patch(l, { supplier_id: e.target.value || null })}>
                    <option value="">поставщик —</option>{sups.map((f) => <option key={f.id} value={f.id}>{f.short_name || f.name} · {f.effective_currency}</option>)}</select></div>)}
                <div className="muted" style={{ fontSize: 11 }}>{PSRC[l.price_source] || ""}</div></td>
              <td className="num">{l.sum_rub != null ? fmtRub(l.sum_rub) : "—"}</td>
              {seesCost && <><td className="num">{l.cost != null ? fmtRub(l.cost) : <span className="muted" style={{ fontSize: 11 }}>{l.cost_note}</span>}</td>
                <td className="num" style={{ color: l.approval ? "var(--bad)" : undefined }}>{l.margin_pct != null ? `${l.margin_pct} %` : "—"}</td></>}
              <td className="num">{l.threshold_pct != null ? `${l.threshold_pct} %` : "—"}{l.approval && <div className="badge bad">виза {l.approval === "fd" ? "ФД" : "КД"}</div>}</td>
              <td><select value={l.status} disabled={closed} onChange={(e) => patch(l, { status: e.target.value })}>{Object.entries(meta.line_status).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                {l.eta && <div className="muted" style={{ fontSize: 11, color: new Date(l.eta) < new Date() && !["in_stock", "shipped"].includes(l.status) ? "var(--bad)" : undefined }}>на складе ≈ {fmtDate(l.eta)}</div>}</td>
            </tr>))}</tbody>
        </table>
      </div>
      {!closed && (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-start" }}>
          <textarea rows={2} style={{ minWidth: 340 }} placeholder="добавить строки: артикул, наименование, количество" value={add} onChange={(e) => setAdd(e.target.value)} />
          <button className="btn add" disabled={!add.trim()} onClick={() => act(() => api.post<Req>(`${base}/lines`, { lines_text: add })).then(() => setAdd(""))}>Строки</button>
        </div>)}

      {!closed && (
        <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
          <button className="btn ghost" disabled={!sel.length} onClick={() => act(() => api.post<Req & { reserve_short?: number[] }>(`/api/parts/stock/requests/${r.id}/client-order`, { line_ids: sel })
            .then((x) => { if (x.reserve_short?.length) alert(`Свободного остатка не хватило для строк: ${x.reserve_short.join(", ")}`); return x; }), "Заказ клиента отмечен, строки «со склада» в жёстком резерве")}>Клиент заказал отмеченные</button>
          <span className="muted" style={{ fontSize: 11 }}>строки «со склада» — жёсткий резерв; «под заказ» — в «Заказы поставщикам» → «К заказу»</span>
        </div>)}
      {!closed && <SupplierPanel r={r} sel={sel} act={act} />}
      {r.warranty && <ClaimFromRequest r={r} />}
      {!closed && r.lines.some((l) => ["ordered", "po", "transit", "in_stock"].includes(l.status)) && <ShipPanel r={r} act={act} />}

      <h3>Итого по строкам в работе</h3>
      <div className="grid cols-3">
        <div>Сумма: <b>{fmtRub(r.summary.sum_rub)} ₽</b> ({r.summary.lines} строк)</div>
        {seesCost && <div>Маржа: <b>{r.summary.profit_rub != null ? `${fmtRub(r.summary.profit_rub)} ₽` : "—"}</b>{r.summary.margin_pct != null && ` (${r.summary.margin_pct} %)`}
          {r.summary.uncosted_lines > 0 && <span className="muted"> · без себестоимости: {r.summary.uncosted_lines}</span>}</div>}
        {r.summary.handling_fee_rub > 0 && <div>«Обработка заказа»: <b>{fmtRub(r.summary.handling_fee_rub)} ₽</b> <span className="muted">— добавится в КП до минимума маржи</span></div>}
      </div>
      {r.summary.approval_needed && (
        <div className="alert info" style={{ marginTop: 8 }}>Есть цены ниже порога маржи — нужна виза {r.summary.approval_needed === "fd" ? "ФД" : "КД"}.
          {approver && <div className="row" style={{ marginTop: 4 }}><input placeholder="комментарий" value={comment} onChange={(e) => setComment(e.target.value)} />
            <button className="btn" onClick={() => act(() => api.post<Req>(`${base}/approve`, { comment }), "Виза поставлена").then(() => setComment(""))}>Утвердить цены</button></div>}</div>)}
      {r.approvals?.length > 0 && <div className="muted" style={{ marginTop: 6 }}>Визы: {r.approvals.map((a) => `${a.level === "fd" ? "ФД" : "КД"} ${a.name}, ${fmtDate(a.at)}${a.comment ? ` — ${a.comment}` : ""}`).join("; ")}</div>}

      <h3>Коммерческое предложение</h3>
      {!closed && (
        <div className="row" style={{ flexWrap: "wrap" }}>
          <span className="muted">В КП пойдут строки с ценой: {kpSel.length} из {sel.length}</span>
          <button className="btn ghost" onClick={() => setSel(r.lines.filter((l) => l.price != null && !["cancelled", "unavailable"].includes(l.status)).map((l) => l.id))}>отметить все с ценой</button>
          <label className="row">валюта КП<select value={kpCur} onChange={(e) => setKpCur(e.target.value)}>{["RUB", "EUR", "CNY", "USD"].map((c) => <option key={c}>{c}</option>)}</select></label>
          {approver && r.summary.handling_fee_rub > 0 && <label className="row"><input type="checkbox" checked={noFee} onChange={(e) => setNoFee(e.target.checked)} /> без «Обработки заказа» (ФД увидит)</label>}
          <button className="btn" disabled={!kpSel.length} onClick={() => act(() => api.post<Req & { reserve_short?: string[] }>(`${base}/kp`, { line_ids: kpSel, currency: kpCur, remove_handling_fee: noFee })
            .then((x) => { if (x.reserve_short?.length) alert(`Свободного остатка не хватило для строк: ${x.reserve_short.join(", ")}`); return x; }), "КП сформировано, строки «со склада» в мягком резерве")}>Сформировать КП</button>
        </div>)}
      {r.kps?.length > 0 && (
        <table style={{ marginTop: 6 }}>
          <thead><tr><th>КП</th><th>Дата</th><th className="num">Строк</th><th className="num">Итого</th><th>Действует до</th><th>Что изменилось</th><th></th></tr></thead>
          <tbody>{r.kps.map((k) => (
            <tr key={k.id}><td><b>{k.number}</b>{k.sent_at && <span className="badge" style={{ marginLeft: 4 }}>отправлено {fmtDate(k.sent_at)}</span>}</td><td>{fmtDate(k.created_at)}</td>
              <td className="num">{k.lines.length}</td>
              <td className="num">{fmtRub(k.total, 2)} {k.currency}{k.handling_fee > 0 && <div className="muted" style={{ fontSize: 11 }}>в т. ч. обработка {fmtRub(k.handling_fee, 2)}</div>}
                {k.handling_fee_removed_by && <div className="muted" style={{ fontSize: 11 }}>обработка заказа убрана</div>}</td>
              <td style={{ color: k.expired ? "var(--bad)" : undefined }}>{fmtDate(k.valid_until)}{k.expired && " — истекло"}</td>
              <td style={{ fontSize: 12 }}>{k.changes.length ? k.changes.map((c, i) => <div key={i}>{c.article}: {c.change}{c.old != null ? ` ${String(c.old)} → ${String(c.new)}` : ""}</div>) : <span className="muted">—</span>}</td>
              <td><a className="btn ghost" href={`/api/parts/requests/kp/${k.id}/docx`}>Word</a>
                {!k.sent_at && !closed && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`/api/parts/requests/kp/${k.id}/sent`), "Отмечено как отправленное")}>отправлено</button>}</td></tr>))}</tbody>
        </table>)}

      <div className="row" style={{ marginTop: 10 }}>
        {!closed ? <>
          <select value={closeKind} onChange={(e) => setCloseKind(e.target.value)}><option value="">закрыть заявку…</option>{Object.entries(meta.close_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          {closeKind && <><input placeholder="причина" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/close`, { closed: closeKind, reason }), "Заявка закрыта")}>Закрыть</button></>}
        </> : <><span className="muted">Закрыта: {meta.close_kinds[r.closed]}{r.close_reason && ` — ${r.close_reason}`}</span>
          <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/reopen`), "Заявка снова в работе")}>вернуть в работу</button></>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ запросы цен поставщикам, сравнение ответов и выбор (ТЗ запчастей, раздел 7)

type RfqLine = { id: string; no: number; request_line_id: string; number: string; name_ru: string; name_foreign: string; machine_translated: boolean;
  qty: number; unit: string; availability: string; price: number | null; currency: string; lead_days: number | null; moq: number | null;
  analog_number: string; comment: string; chosen?: boolean;
  chain?: { supplier_price?: number | null; supplier_currency?: string; group_margin_rub?: number; error?: string; warnings?: string[];
    pending?: { price: number | null; currency: string } } };
// зеркальный запрос через компанию группы (D-156): upper — её предложение, lower — её запрос поставщику
type Mirror = { role: "upper" | "lower"; group_company: string; end_supplier: string; upper_id: string; lower_id: string | null;
  upper_number: string; lower_number: string; state: string; state_title: string; route: string };
type Rfq = { id: string; number: string; supplier_id: string; supplier_name: string; language_title: string; status: string; status_title: string;
  due_date: string | null; sent_at: string | null; answered: number; lines: RfqLine[]; lines_count?: number;
  send_log: { at: string; via: string; ok: boolean; error?: string; from?: string }[]; mirror?: Mirror | null };
type Quote = RfqLine & { rfq_number: string; supplier_id: string; supplier_name: string; mirror?: Mirror | null; selectable?: boolean };
const MIRROR_TONE: Record<string, string> = { ok: "var(--good)", waiting: "var(--muted)", no_policy: "var(--bad)", error: "var(--bad)", changed_after_select: "var(--warn, #b7791f)" };
const rfqTitle = (q: { supplier_name: string; mirror?: Mirror | null }) =>
  !q.mirror ? q.supplier_name : q.mirror.role === "upper" ? `${q.mirror.group_company} → поставщик ${q.mirror.end_supplier}` : `${q.supplier_name} · запрос от ${q.mirror.group_company}`;
function MirrorBadge({ m }: { m?: Mirror | null }) {
  if (!m || m.role !== "upper") return null;
  return <div style={{ fontSize: 11, color: MIRROR_TONE[m.state] || "var(--muted)" }}>{m.state_title}{m.route && <span className="muted"> · {m.route}</span>}</div>;
}
function ChainNote({ l }: { l: RfqLine }) {
  const c = l.chain;
  if (!c) return null;
  if (c.error) return <div style={{ fontSize: 11, color: "var(--bad)" }}>{c.error}</div>;
  return (<div className="muted" style={{ fontSize: 11 }}>
    {c.supplier_price != null && <>поставщик {fmtRub(c.supplier_price, 2)} {c.supplier_currency}</>}
    {c.group_margin_rub != null && <> · маржа группы {fmtRub(c.group_margin_rub, 0)} ₽</>}
    {c.pending && <div style={{ color: "var(--warn, #b7791f)" }}>новая цена {c.pending.price != null ? `${fmtRub(c.pending.price, 2)} ${c.pending.currency}` : "— нет"} — выберите повторно, чтобы принять</div>}
    {c.warnings?.map((w) => <div key={w} style={{ color: "var(--bad)" }}>{w}</div>)}
  </div>);
}
const AVAIL: Record<string, string> = { available: "в наличии", on_order: "под заказ", none: "нет", analog: "аналог" };

function SupplierPanel({ r, sel, act }: { r: Req; sel: string[]; act: <T>(fn: () => Promise<T>, ok?: string) => Promise<void> }) {
  const [sups, setSups] = useState<{ id: string; name: string; short_name: string; country: string; supplier_status: string; approval_status?: string }[]>([]);
  const [pickSup, setPickSup] = useState<string[]>([]);
  const [viaGroup, setViaGroup] = useState<Record<string, string[]>>({});
  const [due, setDue] = useState(3);
  const [rfqs, setRfqs] = useState<Rfq[]>([]);
  const [open, setOpen] = useState<Rfq | null>(null);
  const [quotes, setQuotes] = useState<Record<string, Quote[]>>({});
  const [err, setErr] = useState("");
  const base = `/api/parts/requests/${r.id}`;
  const [chain, setChain] = useState<{ rows: { line_id: string; no: number | null; end_supplier: string; group_company: string; route: string }[]; needed: boolean; approved: boolean; stale: boolean;
    by_name?: string; can_edit: boolean; routes?: { id: string; name: string }[] } | null>(null);
  const reload = () => {
    api.get<typeof chain>(`/api/parts/requests/${r.id}/chain`).then(setChain).catch(() => setChain(null));
    api.get<Rfq[]>(`/api/parts/rfq?open_only=false&request_id=${r.id}`).then(setRfqs).catch((e) => setErr((e as Error).message));
    api.get<Record<string, Quote[]>>(`${base}/quotes`).then(setQuotes).catch(() => setQuotes({}));
  };
  useEffect(() => { api.get<typeof sups>("/api/refs/factories?supplies=parts").then(setSups).catch(() => setSups([])); }, []);
  useEffect(reload, [r]);
  const run = async (fn: () => Promise<unknown>, ok = "") => { setErr(""); try { const x = await fn(); if (ok) setErr(ok); if (x && (x as Rfq).lines && (x as Rfq).supplier_id) setOpen(x as Rfq); reload(); } catch (e) { setErr((e as Error).message); } };
  const withQuotes = r.lines.filter((l) => quotes[l.id]?.length);
  const fdGd = (MeContext.current?.roles || []).some((r) => ["fd", "gd"].includes(r.code));   // компания группы вручную — ФД/ЗГД (D-164)
  const groups = sups.filter((s) => pickSup.includes(s.id) && s.supplier_status === "group_company");
  const ends = sups.filter((s) => s.supplier_status !== "group_company" && s.approval_status !== "pending");
  const groupMissing = groups.some((g) => !(viaGroup[g.id] || []).length);

  return (
    <>
      <h3>Запросы поставщикам</h3>
      {err && <div className="muted" style={{ marginBottom: 4 }}>{err}</div>}
      <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-start" }}>
        <span className="muted">Отмечено строк: {sel.length}</span>
        <select multiple size={4} style={{ minWidth: 260 }} value={pickSup} onChange={(e) => setPickSup(Array.from(e.target.selectedOptions as ArrayLike<HTMLOptionElement>).map((o: HTMLOptionElement) => o.value))}>
          {sups.filter((s) => fdGd || s.supplier_status !== "group_company").map((s) => <option key={s.id} value={s.id} disabled={s.approval_status === "pending"}>{s.short_name || s.name} · {s.country}{s.approval_status === "pending" ? " — на утверждении" : ""}</option>)}</select>
        {groups.map((g) => (
          <label key={g.id} style={{ display: "flex", flexDirection: "column", fontSize: 12 }}>через «{g.short_name || g.name}» — конечные поставщики
            <select multiple size={4} style={{ minWidth: 220 }} value={viaGroup[g.id] || []}
              onChange={(e) => setViaGroup({ ...viaGroup, [g.id]: Array.from(e.target.selectedOptions as ArrayLike<HTMLOptionElement>).map((o: HTMLOptionElement) => o.value) })}>
              {ends.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name} · {s.country}</option>)}</select></label>))}
        <label className="row">ответ за<input type="number" style={{ width: 50 }} value={due} onChange={(e) => setDue(Number(e.target.value))} />дн.</label>
        <button className="btn" disabled={!sel.length || !pickSup.length || groupMissing} title={groupMissing ? "Выберите конечных поставщиков для компании группы" : undefined} onClick={() => act(async () => {
          const via_group = Object.fromEntries(groups.map((g) => [g.id, viaGroup[g.id] || []]));
          const x = await api.post<{ request: Req; rules: Record<string, string> }>(`${base}/rfq`, { line_ids: sel, supplier_ids: pickSup, due_days: due, via_group }); reload();
          const via = Object.entries(x.rules || {}).filter(([, t]) => t.includes("→") && !t.endsWith("напрямую"));
          setErr(via.length ? `По правилам цепочек: ${via.map(([id, t]) => `${sups.find((q) => q.id === id)?.short_name || sups.find((q) => q.id === id)?.name || ""} — ${t}`).join("; ")}. Запрос поставщику уходит от имени компании группы — отправьте из её предложения` : "");
          return x.request;
        }, "Запросы созданы — скачайте файл или отправьте поставщику")}>Запросить цены</button>
        <span className="muted" style={{ fontSize: 11 }}>Ctrl/⌘ — несколько поставщиков; файл — на языке поставщика. Нет нужного — в разделе «Поставщики» отметьте у него «Запасные части»</span>
      </div>
      {chain && chain.rows.length > 0 && <div className={"alert " + (chain.needed ? "warn" : "info")} style={{ fontSize: 13, marginTop: 6 }}>
        <b>Цепочка:</b> {chain.rows.map((x) => `строка ${x.no}: ${x.end_supplier} → ${x.group_company}${x.route ? ` (${x.route})` : ""}`).join("; ")}.{" "}
        {chain.approved ? `Утверждена${chain.by_name ? `: ${chain.by_name}` : ""}.` : chain.stale ? "Изменилась после визы — нужна новая виза ФД или ЗГД до КП." : "Нужна виза ФД или ЗГД до КП."}
        {chain.can_edit && chain.needed && <button className="btn" style={{ marginLeft: 8 }} onClick={() => run(() => api.post(`/api/parts/requests/${r.id}/chain/approve`), "Цепочка утверждена")}>Утвердить цепочку</button>}</div>}
      {rfqs.length > 0 && (
        <table style={{ marginTop: 6 }}>
          <thead><tr><th>Запрос</th><th>Поставщик</th><th>Язык</th><th className="num">Ответ</th><th>Срок ответа</th><th>Статус</th><th></th></tr></thead>
          <tbody>{rfqs.map((q) => (
            <tr key={q.id} style={{ background: open?.id === q.id ? "var(--accent-soft)" : undefined }}>
              <td><a href="#" onClick={(e) => { e.preventDefault(); api.get<Rfq>(`/api/parts/rfq/${q.id}`).then(setOpen); }}><b>{q.number}</b></a></td>
              <td>{rfqTitle(q)}<MirrorBadge m={q.mirror} />
                {chain?.can_edit && q.mirror?.role !== "lower" && q.status !== "closed" && <div style={{ fontSize: 11 }}>цепочка: <select style={{ fontSize: 11, maxWidth: 220 }} value={q.mirror ? "?" : ""}
                  onChange={(e) => run(() => api.post(`/api/parts/rfq/${q.id}/chain`, { route_id: e.target.value === "" ? null : e.target.value }), "Цепочка изменена")}>
                  <option value="">напрямую от Берг-Хауса</option>{q.mirror && <option value="?">{q.mirror.route || "текущий маршрут"}</option>}
                  {(chain.routes || []).filter((x) => x.name !== q.mirror?.route).map((x) => <option key={x.id} value={x.id}>через: {x.name}</option>)}</select></div>}</td><td>{q.language_title}</td><td className="num">{q.answered} из {q.lines_count ?? q.lines?.length}</td>
              <td style={{ color: q.due_date && !["answered", "closed"].includes(q.status) && new Date(q.due_date) < new Date() ? "var(--bad)" : undefined }}>{fmtDate(q.due_date)}</td>
              <td>{q.status_title}</td>
              <td><a className="btn ghost" href={`/api/parts/rfq/${q.id}/xlsx`}>Excel</a>
                {q.status !== "closed" && <>
                  {q.mirror?.role !== "lower" && <><button className="btn ghost" onClick={() => run(async () => { const x = await api.post<Rfq & { send_results: { via: string; ok: boolean; error: string }[] }>(`/api/parts/rfq/${q.id}/send`, { only_unanswered: q.status !== "draft" });
                    setErr(x.send_results.map((s) => `${s.via === "mail" ? "почта" : "WeCom"}: ${s.ok ? "отправлено" : s.error}`).join("; ")); return x; })}>{q.status === "draft" ? "отправить" : "напомнить"}</button>
                  {q.status === "draft" && <button className="btn ghost" onClick={() => run(() => api.post(`/api/parts/rfq/${q.id}/mark-sent`), "Отмечено как отправленное")}>отправлен вручную</button>}</>}
                  {q.mirror?.role !== "upper" && <label className="btn ghost" style={{ cursor: "pointer" }}>ответ из файла<input type="file" accept=".xlsx" style={{ display: "none" }}
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => api.upload(`/api/parts/rfq/${q.id}/import`, f), "Ответ загружен"); e.target.value = ""; }} /></label>}
                  <button className="btn ghost" onClick={() => run(() => api.post(`/api/parts/rfq/${q.id}/close`), "Запрос закрыт")}>закрыть</button></>}</td></tr>))}</tbody>
        </table>)}
      {open && <RfqAnswers rfq={open} onSave={(lines) => run(() => api.put(`/api/parts/rfq/${open.id}/answers`, { lines }), "Ответ сохранён")} onClose={() => setOpen(null)} />}
      {withQuotes.length > 0 && (<>
        <h3>Сравнение ответов</h3>
        <table>
          <thead><tr><th>№</th><th>Позиция</th><th>Поставщик</th><th>Наличие</th><th className="num">Цена</th><th className="num">Срок, дн</th><th className="num">Мин. партия</th><th>Комментарий</th><th></th></tr></thead>
          <tbody>{withQuotes.map((l) => {
            const qs = [...quotes[l.id]].sort((a, b) => (a.price == null ? 1 : 0) - (b.price == null ? 1 : 0));
            return qs.map((q, i) => (
              <tr key={q.id} style={{ background: l.rfq_line_id === q.id ? "var(--accent-soft)" : undefined, borderTop: i === 0 ? "2px solid var(--line)" : undefined }}>
                {i === 0 && <><td rowSpan={qs.length}>{l.no}</td><td rowSpan={qs.length}><b>{l.part?.article || l.raw_article}</b><div className="muted" style={{ fontSize: 11 }}>{l.part?.name_ru || l.raw_name} · {l.qty} {l.unit}</div></td></>}
                <td>{rfqTitle(q)}<div className="muted" style={{ fontSize: 11 }}>{q.rfq_number}</div>{q.mirror?.role === "upper" && <ChainNote l={q} />}</td>
                <td>{q.availability ? AVAIL[q.availability] : <span className="muted">ждём ответ</span>}{q.analog_number && <div style={{ fontSize: 11 }}>аналог {q.analog_number}</div>}</td>
                <td className="num">{q.price != null ? `${fmtRub(q.price, 2)} ${q.currency}` : "—"}</td>
                <td className="num">{q.lead_days ?? "—"}</td><td className="num">{q.moq ?? "—"}</td><td style={{ fontSize: 12 }}>{q.comment}</td>
                <td>{l.rfq_line_id === q.id ? <span className="badge">выбран</span>
                  : q.selectable === false ? <span className="muted" style={{ fontSize: 11 }}>выбирается предложение компании группы</span>
                  : q.price != null && q.availability !== "none" && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/lines/${l.id}/choose`, { rfq_line_id: q.id }), "Поставщик выбран, цена пересчитана")}>выбрать</button>}</td>
              </tr>));
          })}</tbody>
        </table>
        <p className="muted" style={{ fontSize: 11 }}>Срок клиенту = срок поставщика + перевозка по маршруту. Цена клиенту считается от цены поставщика по маршруту с наценками группы.
          Предложение компании группы = цена поставщика с наценками группы по маршруту; изменение ответа поставщика после выбора цену в заявке не меняет.</p>
      </>)}
    </>
  );
}

function RfqAnswers({ rfq, onSave, onClose }: { rfq: Rfq; onSave: (lines: Partial<RfqLine>[]) => void; onClose: () => void }) {
  if (rfq.mirror?.role === "upper") return <MirrorOffer rfq={rfq} onClose={onClose} />;
  return <RfqAnswersForm rfq={rfq} onSave={onSave} onClose={onClose} />;
}

function MirrorOffer({ rfq, onClose }: { rfq: Rfq; onClose: () => void }) {
  const m = rfq.mirror!;
  return (
    <div style={{ border: "1px solid var(--line)", padding: 8, marginTop: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}><b>{rfq.number} · предложение {m.group_company} — поставщик {m.end_supplier}</b><button className="btn ghost" onClick={onClose}>свернуть</button></div>
      <MirrorBadge m={m} />
      <div className="muted" style={{ fontSize: 11 }}>Считается из ответа поставщика на запрос {m.lower_number || "компании группы"} по маршруту и наценкам группы — вручную не правится.</div>
      <table>
        <thead><tr><th>№</th><th>Номер</th><th>Наименование</th><th className="num">Кол-во</th><th>Наличие</th><th className="num">Цена {m.group_company}</th><th className="num">Срок, дн</th><th className="num">Мин. партия</th><th>Аналог</th></tr></thead>
        <tbody>{rfq.lines.map((l) => (
          <tr key={l.id}><td>{l.no}</td><td>{l.number}</td><td>{l.name_ru}</td><td className="num">{l.qty} {l.unit}</td>
            <td>{l.availability ? AVAIL[l.availability] : <span className="muted">ждём ответ</span>}</td>
            <td className="num">{l.price != null ? `${fmtRub(l.price, 2)} ${l.currency}` : "—"}<ChainNote l={l} /></td>
            <td className="num">{l.lead_days ?? "—"}</td><td className="num">{l.moq ?? "—"}</td><td>{l.analog_number}</td></tr>))}</tbody>
      </table>
    </div>);
}

function RfqAnswersForm({ rfq, onSave, onClose }: { rfq: Rfq; onSave: (lines: Partial<RfqLine>[]) => void; onClose: () => void }) {
  const [rows, setRows] = useState<RfqLine[]>(rfq.lines);
  useEffect(() => setRows(rfq.lines), [rfq]);
  const set = (i: number, k: keyof RfqLine, v: unknown) => setRows(rows.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  return (
    <div style={{ border: "1px solid var(--line)", padding: 8, marginTop: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}><b>{rfq.number} · {rfqTitle(rfq)} — ответ по строкам</b><button className="btn ghost" onClick={onClose}>свернуть</button></div>
      {rfq.send_log?.length > 0 && <div className="muted" style={{ fontSize: 11 }}>Отправка: {rfq.send_log.map((s) => `${fmtDate(s.at)} ${s.via}${s.from ? ` от ${s.from}` : ""}${s.ok ? "" : ` — ${s.error}`}`).join("; ")}</div>}
      <table>
        <thead><tr><th>№</th><th>Номер у поставщика</th><th>Наименование</th><th className="num">Кол-во</th><th>Наличие</th><th className="num">Цена</th><th>Валюта</th><th className="num">Срок, дн</th><th className="num">Мин. партия</th><th>Аналог</th><th>Комментарий</th></tr></thead>
        <tbody>{rows.map((l, i) => (
          <tr key={l.id}>
            <td>{l.no}</td><td>{l.number}</td>
            <td>{l.name_ru}{l.name_foreign && <div className="muted" style={{ fontSize: 11 }}>{l.name_foreign}{l.machine_translated && " · машинный перевод, проверить"}</div>}</td>
            <td className="num">{l.qty} {l.unit}</td>
            <td><select value={l.availability} onChange={(e) => set(i, "availability", e.target.value)}><option value="">—</option>{Object.entries(AVAIL).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></td>
            <td className="num"><input type="number" style={{ width: 80 }} value={l.price ?? ""} onChange={(e) => set(i, "price", e.target.value ? Number(e.target.value) : null)} /></td>
            <td><select value={l.currency || ""} onChange={(e) => set(i, "currency", e.target.value)}><option value="">—</option>{CUR.map((c) => <option key={c}>{c}</option>)}</select></td>
            <td className="num"><input type="number" style={{ width: 55 }} value={l.lead_days ?? ""} onChange={(e) => set(i, "lead_days", e.target.value ? Number(e.target.value) : null)} /></td>
            <td className="num"><input type="number" style={{ width: 55 }} value={l.moq ?? ""} onChange={(e) => set(i, "moq", e.target.value ? Number(e.target.value) : null)} /></td>
            <td><input style={{ width: 100 }} value={l.analog_number} onChange={(e) => set(i, "analog_number", e.target.value)} /></td>
            <td><input value={l.comment} onChange={(e) => set(i, "comment", e.target.value)} /></td>
          </tr>))}</tbody>
      </table>
      <button className="btn" style={{ marginTop: 6 }} onClick={() => onSave(rows.filter((x) => x.availability || x.price != null).map((x) => ({
        id: x.id, availability: x.availability, price: x.price, currency: x.currency, lead_days: x.lead_days, moq: x.moq, analog_number: x.analog_number, comment: x.comment })))}>Сохранить ответ</button>
    </div>
  );
}

// ------------------------------------------------------------------ отгрузка клиенту по жёсткому резерву (ТЗ запчастей, раздел 8)

type Shippable = { line_id: string; no: number; article: string; name: string; qty: number; unit: string; shipped_qty: number; status: string;
  reserved: { warehouse_id: string; warehouse: string; qty: number }[] };

function ShipPanel({ r, act }: { r: Req; act: <T>(fn: () => Promise<T>, ok?: string) => Promise<void> }) {
  const [rows, setRows] = useState<Shippable[]>([]);
  const [qty, setQty] = useState<Record<string, string>>({});
  const [wh, setWh] = useState("");
  useEffect(() => { api.get<Shippable[]>(`/api/parts/stock/requests/${r.id}/shippable`).then((x) => { setRows(x); setQty({}); }).catch(() => setRows([])); }, [r]);
  const whs = [...new Map(rows.flatMap((x) => x.reserved).map((x) => [x.warehouse_id, x.warehouse])).entries()];
  const w = wh || whs[0]?.[0] || "";
  const ready = rows.filter((x) => x.reserved.some((y) => y.warehouse_id === w && y.qty > 0));
  if (!whs.length) return null;
  return (
    <>
      <h3>Отгрузка клиенту</h3>
      <div className="row"><label className="row">со склада<select value={w} onChange={(e) => setWh(e.target.value)}>{whs.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select></label></div>
      <table style={{ marginTop: 4 }}>
        <thead><tr><th>№</th><th>Позиция</th><th className="num">В заявке</th><th className="num">Отгружено</th><th className="num">В резерве</th><th className="num">Отгрузить</th></tr></thead>
        <tbody>{ready.map((x) => { const res = x.reserved.find((y) => y.warehouse_id === w)?.qty || 0; return (
          <tr key={x.line_id}><td>{x.no}</td><td>{x.article} {x.name}</td><td className="num">{x.qty} {x.unit}</td><td className="num">{x.shipped_qty}</td><td className="num">{res}</td>
            <td className="num"><input inputMode="decimal" style={{ width: 70 }} placeholder={String(res)} value={qty[x.line_id] ?? ""} onChange={(e) => setQty({ ...qty, [x.line_id]: e.target.value })} /></td></tr>); })}</tbody>
      </table>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn ghost" onClick={() => setQty(Object.fromEntries(ready.map((x) => [x.line_id, String(x.reserved.find((y) => y.warehouse_id === w)?.qty || 0)])))}>всё из резерва</button>
        <button className="btn" disabled={!Object.values(qty).some((v) => Number(v) > 0)} onClick={() => act(() => api.post<Req>(`/api/parts/stock/requests/${r.id}/shipments`, {
          warehouse_id: w, lines: Object.entries(qty).filter(([, v]) => Number(v) > 0).map(([line_id, v]) => ({ line_id, qty: Number(v.replace(",", ".")) })) }), "Отгрузка проведена, остаток и резерв уменьшены")}>Отгрузить</button>
      </div>
    </>
  );
}

function ClaimFromRequest({ r }: { r: Req }) {
  const [sups, setSups] = useState<{ id: string; name: string; short_name: string }[]>([]);
  const [sid, setSid] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<typeof sups>("/api/refs/factories").then(setSups).catch(() => setSups([])); }, []);
  return (
    <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
      <b>Гарантийная заявка:</b> претензия заводу
      <select value={sid} onChange={(e) => setSid(e.target.value)}><option value="">— завод —</option>{sups.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select>
      <button className="btn ghost" disabled={!sid} onClick={() => api.post<{ number: string }>("/api/parts/claims", { supplier_id: sid, request_id: r.id, machine_id: (r as Req & { machine_id?: string }).machine_id || null })
        .then((c) => setMsg(`Создана претензия ${c.number} — откройте вкладку «Гарантия», приложите фото и отправьте заводу`)).catch((e) => setMsg((e as Error).message))}>Создать претензию</button>
      {msg && <span className="muted">{msg}</span>}
    </div>
  );
}


/** Карточка заявки на запчасти по своему адресу /deals/parts/:id — единый раздел «Сделки». */
export function PartsRequestView({ me, id }: { me: Me; id: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [cur, setCur] = useState<Req | null>(null);
  const [msg, setMsg] = useState("");
  const seesCost = can(me, "cost");
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  useEffect(() => { api.get<Meta>("/api/parts/requests/meta").then(setMeta); }, []);
  useEffect(() => { api.get<Req>(`/api/parts/requests/${id}`).then(setCur).catch((e) => setMsg((e as Error).message)); }, [id]);
  async function act<T>(fn: () => Promise<T>, ok = "") {
    try { const r = (await fn()) as unknown as Req; if (r && r.lines) setCur(r); else setCur(await api.get<Req>(`/api/parts/requests/${id}`)); if (ok) setMsg(ok); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      <div className="page-head"><Link to="/deals?type=parts" className="muted">← Сделки</Link><h1 style={{ margin: 0 }}>Заявка на запчасти {cur?.number || ""}</h1></div>
      {msg && <div className="alert info" onClick={() => setMsg("")}>{msg}</div>}
      {cur && meta ? <RequestCard r={cur} meta={meta} seesCost={seesCost} approver={has("cd", "fd", "gd")} act={act} /> : !msg && <div className="card">Загрузка…</div>}
    </>);
}
