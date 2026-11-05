import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Me } from "../types";

// Заявки клиентов на запчасти: попозиционный учёт, подбор по каталогу, цена, маржа и визы (ТЗ запчастей, разделы 5–6).

type Line = {
  id: string; no: number; parent_line_id: string | null; raw_article: string; raw_name: string; qty: number; unit: string;
  part: { id: string; article: string; name_ru: string; manufacturer: string } | null; match_kind: string; candidates: string[];
  source: string; lead_days: number | null; price: number | null; currency: string; price_source: string; price_rub: number | null; sum_rub: number | null;
  available: number | null; status: string; comment: string; threshold_pct: number | null; approval: string | null;
  cost?: number | null; margin_pct?: number | null; profit_rub?: number | null; cost_note?: string;
};
type Req = {
  id: string; number: string; client_id: string; client_name: string; manager_name: string; machine: string; urgent: boolean; warranty: boolean;
  contact: string; channel: string; comment: string; closed: string; close_reason: string; created_at: string; status_text: string;
  lines: Line[]; approvals: { level: string; name: string; at: string; comment: string }[];
  summary: { lines: number; sum_rub: number; profit_rub?: number | null; margin_pct?: number | null; handling_fee_rub: number; approval_needed: string | null; uncosted_lines: number };
};
type Row = { id: string; number: string; client_name: string; machine: string; urgent: boolean; created_at: string; lines: number; status_text: string; price_rub: number };
type Meta = { channels: Record<string, string>; line_status: Record<string, string>; close_kinds: Record<string, string>; can_create: boolean };
type Ref = { id: string; name: string };

const MATCH: Record<string, string> = { article: "по артикулу", oem: "по OEM", supplier: "по номеру поставщика", analog: "аналог", manual: "вручную" };
const PSRC: Record<string, string> = { contract: "договорная", pricelist: "прайс", calc: "расчёт", manual: "вручную" };
const CUR = ["RUB", "EUR", "CNY", "USD", "KGS"];

export default function PartsRequests({ me }: { me: Me }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [q, setQ] = useState("");
  const [cur, setCur] = useState<Req | null>(null);
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState("");
  const [clients, setClients] = useState<Ref[]>([]);
  const seesCost = can(me, "cost");
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));

  const load = () => api.get<Row[]>(`/api/parts/requests?open_only=${openOnly}&q=${encodeURIComponent(q)}`).then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => { api.get<Meta>("/api/parts/requests/meta").then(setMeta); api.get<Ref[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); }, []);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [openOnly, q]);
  const open = (id: string) => api.get<Req>(`/api/parts/requests/${id}`).then((r) => { setCur(r); setCreating(false); setMsg(""); });
  async function act<T>(fn: () => Promise<T>, ok = "") {
    try { const r = (await fn()) as unknown as Req; if (r && (r as Req).lines) setCur(r); if (ok) setMsg(ok); load(); }
    catch (e) { setMsg((e as Error).message); }
  }

  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input placeholder="номер, клиент, машина" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="row"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> только открытые</label>
          {meta?.can_create && <button className="btn add" style={{ marginLeft: "auto" }} onClick={() => { setCreating(true); setCur(null); }}>Заявка</button>}
        </div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Номер</th><th>Дата</th><th>Клиент</th><th>Машина</th><th className="num">Строк</th><th>Статус</th><th className="num">Сумма, ₽</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} style={{ cursor: "pointer", background: cur?.id === r.id ? "var(--accent-soft)" : undefined }} onClick={() => open(r.id)}>
              <td><b>{r.number}</b>{r.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td><td>{fmtDate(r.created_at)}</td>
              <td>{r.client_name}</td><td>{r.machine}</td><td className="num">{r.lines}</td><td>{r.status_text}</td><td className="num">{r.price_rub ? fmtRub(r.price_rub) : "—"}</td></tr>))}
            {rows.length === 0 && <tr><td colSpan={7} className="muted">Заявок нет</td></tr>}</tbody>
        </table>
      </div>
      {creating && meta && <CreateForm meta={meta} clients={clients} onCreated={(r) => { setCreating(false); setCur(r); load(); }} onCancel={() => setCreating(false)} setMsg={setMsg} />}
      {cur && meta && <RequestCard r={cur} meta={meta} seesCost={seesCost} approver={has("cd", "fd", "gd")} act={act} />}
    </>
  );
}

function CreateForm({ meta, clients, onCreated, onCancel, setMsg }: { meta: Meta; clients: Ref[]; onCreated: (r: Req) => void; onCancel: () => void; setMsg: (s: string) => void }) {
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
  const [pick, setPick] = useState<{ line: string; q: string; items: { id: string; article: string; name_ru: string; manufacturer: string }[] } | null>(null);
  const base = `/api/parts/requests/${r.id}`;
  const patch = (l: Line, body: Record<string, unknown>) => act(() => api.patch<Req>(`${base}/lines/${l.id}`, body));
  async function search(line: string, q: string) {
    const res = await api.get<{ items: { id: string; article: string; name_ru: string; manufacturer: string }[] }>(`/api/parts/catalog?q=${encodeURIComponent(q)}&limit=10`);
    setPick({ line, q, items: res.items });
  }
  const closed = !!r.closed;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{r.number} · {r.client_name} {r.urgent && <span className="badge bad">машина стоит</span>} {r.warranty && <span className="badge">гарантия</span>}</h2>
        <span className="muted">{r.status_text}</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>{[r.machine, r.contact, meta.channels[r.channel], r.manager_name && `менеджер ${r.manager_name}`].filter(Boolean).join(" · ")}{r.comment && ` · ${r.comment}`}</p>
      <div className="row" style={{ marginBottom: 6 }}>
        {!closed && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/match`), "Подбор повторён")}>Подобрать по каталогу</button>}
        {!closed && <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/reprice`), "Цены пересчитаны")}>Пересчитать цены</button>}
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>№</th><th>Запрос клиента</th><th>Подобрано</th><th className="num">Кол-во</th><th>Источник</th><th className="num">Срок, дн</th><th className="num">Цена</th><th className="num">Сумма, ₽</th>
            {seesCost && <><th className="num">Себест., ₽/ед</th><th className="num">Маржа</th></>}<th className="num">Порог</th><th>Статус</th></tr></thead>
          <tbody>{r.lines.map((l) => (
            <tr key={l.id} style={{ opacity: ["cancelled", "unavailable"].includes(l.status) ? 0.5 : 1 }}>
              <td>{l.no}{l.parent_line_id && <div className="muted" style={{ fontSize: 11 }}>часть строки</div>}</td>
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
                <select value={l.currency} disabled={closed} onChange={(e) => patch(l, { price: l.price || 0, currency: e.target.value })}>{CUR.map((c) => <option key={c}>{c}</option>)}</select>
                <div className="muted" style={{ fontSize: 11 }}>{PSRC[l.price_source] || ""}</div></td>
              <td className="num">{l.sum_rub != null ? fmtRub(l.sum_rub) : "—"}</td>
              {seesCost && <><td className="num">{l.cost != null ? fmtRub(l.cost) : <span className="muted" style={{ fontSize: 11 }}>{l.cost_note}</span>}</td>
                <td className="num" style={{ color: l.approval ? "var(--bad)" : undefined }}>{l.margin_pct != null ? `${l.margin_pct} %` : "—"}</td></>}
              <td className="num">{l.threshold_pct != null ? `${l.threshold_pct} %` : "—"}{l.approval && <div className="badge bad">виза {l.approval === "fd" ? "ФД" : "КД"}</div>}</td>
              <td><select value={l.status} disabled={closed} onChange={(e) => patch(l, { status: e.target.value })}>{Object.entries(meta.line_status).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></td>
            </tr>))}</tbody>
        </table>
      </div>
      {!closed && (
        <div className="row" style={{ marginTop: 8, alignItems: "flex-start" }}>
          <textarea rows={2} style={{ minWidth: 340 }} placeholder="добавить строки: артикул, наименование, количество" value={add} onChange={(e) => setAdd(e.target.value)} />
          <button className="btn add" disabled={!add.trim()} onClick={() => act(() => api.post<Req>(`${base}/lines`, { lines_text: add })).then(() => setAdd(""))}>Строки</button>
        </div>)}

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

      <div className="row" style={{ marginTop: 10 }}>
        {!closed ? <>
          <select value={closeKind} onChange={(e) => setCloseKind(e.target.value)}><option value="">закрыть заявку…</option>{Object.entries(meta.close_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          {closeKind && <><input placeholder="причина" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/close`, { closed: closeKind, reason }), "Заявка закрыта")}>Закрыть</button></>}
        </> : <><span className="muted">Закрыта: {meta.close_kinds[r.closed]}{r.close_reason && ` — ${r.close_reason}`}</span>
          <button className="btn ghost" onClick={() => act(() => api.post<Req>(`${base}/reopen`), "Заявка снова в работе")}>вернуть в работу</button></>}
      </div>
      <p className="muted" style={{ fontSize: 12 }}>КП с частичной отправкой, сроком действия и мягким резервом на 30 дней — следующий шаг.</p>
    </div>
  );
}
