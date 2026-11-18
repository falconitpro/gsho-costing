import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";

// Рабочее место логиста (D-073): адреса отгрузки и доставки, выбранные ЗГД характеристики, запрос логистическим
// компаниям (Excel / PDF / почта) и их ответы.

export type WsField = { code: string; name: string; unit: string; value: string; requirement: string };
export type WsPos = { position_id: string; machine_type: string; designation: string; qty: number; rows: WsField[] };
export type Quote = { id: string; carrier: string; amount: number; currency: string; transit_days: number | null; valid_until: string; scope: string;
  comment: string; file_name: string; storage_path: string; at: string; by_name: string };
type Carrier = { name: string; email: string; phone?: string; comment?: string };
type Req = { id: string; no: string; at: string; by_name: string; answer_by: string; sent_to: string[]; files: { manual?: boolean } };
type Adr = { pickup: { factory_id: string; factory: string; card: string; address: string; edited: boolean }[]; delivery_address: string; delivery_source: string;
  client_addresses: { id: string; address: string; status: string; deal_number?: string }[] };
export type Workspace = { fields: WsPos[]; addresses: Adr; carriers: Carrier[]; requests: Req[]; quotes: Quote[]; mail_enabled: boolean; can_choose_fields: boolean; can_edit: boolean };

export const SCOPES: Record<string, string> = { door_to_door: "До двери (всё включено)", intl_freight: "До границы РФ", customs: "Таможня (брокер, СВХ)",
  rf_delivery: "Доставка по РФ", insurance: "Страхование", export: "Экспортное оформление" };
const CURS = ["RUB", "CNY", "USD", "EUR"];

/** Выбранные ЗГД характеристики позиции — компактно, для расчёта доставки и таможни. */
export function LogistFields({ pos, canChoose, onChoose }: { pos?: WsPos; canChoose: boolean; onChoose: () => void }) {
  if (!pos) return null;
  const filled = pos.rows.filter((r) => r.value || r.requirement).length;
  return (
    <div className="lw-fields">
      <div className="lw-fields-head"><b>Данные для логиста</b> <span className="muted">· заполнено {filled} из {pos.rows.length} · все характеристики — на вкладке «Позиции и характеристики»</span>
        {canChoose && <button className="btn ghost" onClick={onChoose}>⚙ выбрать поля</button>}</div>
      <div className="lw-fields-grid">{pos.rows.map((r) => (
        <div key={(r.code || "") + r.name} className={"lw-f" + (r.value || r.requirement ? "" : " lw-f-empty")}>
          <span className="muted">{r.name}</span><b>{r.value || r.requirement || "—"}{(r.value || r.requirement) && r.unit ? ` ${r.unit}` : ""}</b>
          {!r.value && r.requirement && <span className="muted lw-f-note">требование клиента</span>}</div>))}
        {pos.rows.length === 0 && <span className="muted">Поля не выбраны{canChoose ? " — нажмите «выбрать поля»" : ""}.</span>}</div>
    </div>);
}

/** ЗГД выбирает характеристики, которые видит логист (общая настройка для всех сделок). */
export function FieldsChooser({ dealId, onClose }: { dealId: string; onClose: () => void }) {
  const [opts, setOpts] = useState<{ code: string; name: string; section: string }[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const k = (o: { code: string; name: string }) => o.code || o.name.toLowerCase();
  useEffect(() => { api.get<{ fields: { code: string; name: string }[]; options: { code: string; name: string; section: string }[] }>(`/api/refs/logistics-fields?deal_id=${dealId}`)
    .then((r) => { setOpts(r.options); setSel(new Set(r.fields.map(k))); }).catch((e) => setErr(e.message)); }, [dealId]);
  const shown = opts.filter((o) => !q || (o.name + o.code + o.section).toLowerCase().includes(q.toLowerCase()));
  const sections = [...new Set(shown.map((o) => o.section))];
  const save = () => api.put("/api/refs/logistics-fields", opts.filter((o) => sel.has(k(o))).map((o) => ({ code: o.code, name: o.name }))).then(onClose).catch((e) => setErr(e.message));
  return (
    <div className="lw-modal"><div className="card lw-modal-body">
      <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>Какие характеристики видит логист</h3><button className="btn ghost" onClick={onClose}>×</button></div>
      <p className="muted" style={{ marginTop: 4 }}>Общая настройка для всех сделок (выбирает ЗГД). Выбрано: <b>{sel.size}</b>. Обычно нужны габариты, массы, страна производства, объём, тип упаковки.</p>
      {err && <div className="alert error">{err}</div>}
      <input placeholder="поиск по названию" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%", marginBottom: 8 }} />
      <div className="lw-modal-list">{sections.map((s) => (
        <div key={s} style={{ marginBottom: 8 }}><b className="muted">{s}</b>
          {shown.filter((o) => o.section === s).map((o) => (
            <label key={k(o)} className="lw-opt"><input type="checkbox" checked={sel.has(k(o))} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(k(o)); else n.delete(k(o)); setSel(n); }} />
              {o.name} {o.code && <span className="muted">{o.code}</span>}</label>))}
        </div>))}</div>
      <div className="row" style={{ gap: 8, marginTop: 8 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={onClose}>Отмена</button></div>
    </div></div>);
}

/** Адреса, запрос логистическим компаниям и их ответы. */
export default function LogistWorkspace({ dealId, ws, reload, onApplyQuote }: { dealId: string; ws: Workspace; reload: () => void; onApplyQuote?: (q: Quote) => void }) {
  const edit = ws.can_edit;
  const [adr, setAdr] = useState({ pickup: Object.fromEntries(ws.addresses.pickup.map((p) => [p.factory_id, p.address])), delivery: ws.addresses.delivery_address, propose: true });
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null);
  const [days, setDays] = useState(3);
  const [to, setTo] = useState<string[]>([]);
  const [extra, setExtra] = useState("");
  const [text, setText] = useState("");
  const [carrierForm, setCarrierForm] = useState<Carrier | null>(null);
  const [qf, setQf] = useState({ carrier: "", amount: "", currency: "RUB", transit_days: "", valid_until: "", scope: "door_to_door", comment: "" });
  const [qfile, setQfile] = useState<File | null>(null);
  useEffect(() => { setAdr({ pickup: Object.fromEntries(ws.addresses.pickup.map((p) => [p.factory_id, p.address])), delivery: ws.addresses.delivery_address, propose: true }); }, [ws.addresses]);
  const ok = (t: string) => setMsg({ ok: true, t }), bad = (e: unknown) => setMsg({ ok: false, t: (e as Error).message });
  const known = new Set(ws.addresses.client_addresses.map((a) => a.address.trim().toLowerCase()));
  const isNew = !!adr.delivery.trim() && !known.has(adr.delivery.trim().toLowerCase());

  const saveAdr = () => api.put<{ proposed: boolean }>(`/api/deals/${dealId}/logistics/addresses`, { pickup: adr.pickup, delivery_address: adr.delivery, propose_to_client: adr.propose })
    .then((r) => { ok(r.proposed ? "Адреса сохранены. Новый адрес доставки отправлен в карточку клиента на утверждение ЗГД." : "Адреса сохранены."); reload(); }).catch(bad);
  const recipients = [...to, ...extra.split(/[,;\s]+/).filter((x) => x.includes("@"))];
  const send = (manual: boolean) => api.post(`/api/deals/${dealId}/logistics/request/send`, { emails: recipients, message: text, answer_days: days, manual })
    .then(() => { ok(manual ? "Отправка отмечена в сделке." : `Запрос отправлен: ${recipients.join(", ")}`); setTo([]); setExtra(""); reload(); }).catch(bad);
  const saveCarrier = () => { if (!carrierForm) return; api.put("/api/refs/logistics-carriers", [...ws.carriers, carrierForm]).then(() => { setCarrierForm(null); reload(); }).catch(bad); };
  async function addQuote() {
    const fd = new FormData();
    Object.entries(qf).forEach(([k, v]) => { if (v !== "") fd.append(k, k === "amount" ? String(Number(String(v).replace(",", ".")) || 0) : v); });
    if (qfile) fd.append("file", qfile);
    try { await api.form(`/api/deals/${dealId}/logistics/quotes`, fd); setQf({ carrier: "", amount: "", currency: "RUB", transit_days: "", valid_until: "", scope: "door_to_door", comment: "" }); setQfile(null); ok("Ответ добавлен."); reload(); }
    catch (e) { bad(e); }
  }
  const delQuote = (q: Quote) => { if (window.confirm(`Удалить ответ ${q.carrier}?`)) api.del(`/api/deals/${dealId}/logistics/quotes/${q.id}`).then(reload).catch(bad); };
  const best = [...ws.quotes].filter((q) => q.scope === "door_to_door" && q.amount > 0).sort((a, b) => a.amount - b.amount)[0];

  return (
    <div className="card lw">
      {msg && <div className={"alert " + (msg.ok ? "ok" : "error")}>{msg.t}</div>}
      <h3 className="lw-h"><span className="pf-num">1</span> Адреса</h3>
      <div className="lw-grid">
        {ws.addresses.pickup.map((p) => (
          <label key={p.factory_id} className="pf-field"><span className="pf-label">Отгрузка: {p.factory} {p.card && adr.pickup[p.factory_id] !== p.card ? "· изменён (в карточке завода — другой)" : "· из карточки завода"}</span>
            <input value={adr.pickup[p.factory_id] || ""} disabled={!edit} placeholder="адрес завода — нет в карточке" onChange={(e) => setAdr({ ...adr, pickup: { ...adr.pickup, [p.factory_id]: e.target.value } })} /></label>))}
        {ws.addresses.pickup.length === 0 && <div className="muted pf-field">Отгрузка: завод в позициях ещё не выбран.</div>}
        <label className="pf-field"><span className="pf-label">Доставка клиенту {ws.addresses.delivery_source ? `· ${ws.addresses.delivery_source}` : ""}</span>
          <input list="lw-client-adr" value={adr.delivery} disabled={!edit} placeholder="адрес доставки" onChange={(e) => setAdr({ ...adr, delivery: e.target.value })} />
          <datalist id="lw-client-adr">{ws.addresses.client_addresses.map((a) => <option key={a.id} value={a.address}>{a.status === "pending" ? "на утверждении" : "из карточки клиента"}</option>)}</datalist>
          {edit && isNew && <label className="muted lw-sub"><input type="checkbox" checked={adr.propose} onChange={(e) => setAdr({ ...adr, propose: e.target.checked })} /> добавить в карточку клиента как адрес доставки (после утверждения ЗГД)</label>}</label>
      </div>
      {edit && <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={saveAdr}>Сохранить адреса</button></div>}

      <h3 className="lw-h"><span className="pf-num">2</span> Запрос логистическим компаниям</h3>
      <div className="lw-req">
        <div className="lw-req-col">
          <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <a className="btn ghost" href={`/api/deals/${dealId}/logistics/request/file?fmt=xlsx&answer_days=${days}`}>⬇ Excel</a>
            <a className="btn ghost" href={`/api/deals/${dealId}/logistics/request/file?fmt=pdf&answer_days=${days}`}>⬇ PDF</a>
            <label className="muted">ответ в течение <input type="number" min={1} max={30} value={days} style={{ width: 56 }} onChange={(e) => setDays(Number(e.target.value) || 3)} /> дн.</label>
          </div>
          <p className="muted lw-sub">Типовая форма: адреса отгрузки и доставки, базис, готовность груза, груз по позициям (выбранные характеристики) и таблица для ответа: услуга, стоимость, валюта, срок, условия.</p>
        </div>
        {edit && <div className="lw-req-col">
          <b>Кому</b>
          <div className="lw-carriers">{ws.carriers.map((c) => (
            <label key={c.email || c.name} className="lw-opt"><input type="checkbox" disabled={!c.email} checked={to.includes(c.email)} onChange={(e) => setTo(e.target.checked ? [...to, c.email] : to.filter((x) => x !== c.email))} />
              {c.name} <span className="muted">{c.email || "нет почты"}</span></label>))}
            {ws.carriers.length === 0 && <span className="muted">Логистических компаний пока нет — добавьте.</span>}</div>
          {carrierForm ? (
            <div className="lw-inline"><input placeholder="компания" value={carrierForm.name} onChange={(e) => setCarrierForm({ ...carrierForm, name: e.target.value })} />
              <input placeholder="e-mail" value={carrierForm.email} onChange={(e) => setCarrierForm({ ...carrierForm, email: e.target.value })} />
              <button className="btn" onClick={saveCarrier}>Добавить</button><button className="btn ghost" onClick={() => setCarrierForm(null)}>×</button></div>
          ) : <button className="btn ghost" onClick={() => setCarrierForm({ name: "", email: "" })}>+ логистическая компания</button>}
          <input placeholder="ещё адреса через запятую" value={extra} onChange={(e) => setExtra(e.target.value)} style={{ width: "100%", marginTop: 6 }} />
          <textarea placeholder="текст письма (необязательно)" value={text} rows={2} onChange={(e) => setText(e.target.value)} style={{ width: "100%", marginTop: 6 }} />
          <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
            <button className="btn" disabled={!ws.mail_enabled || !recipients.length} title={ws.mail_enabled ? "" : "почта не подключена"} onClick={() => send(false)}>✉ Отправить из системы</button>
            <button className="btn ghost" disabled={!recipients.length} onClick={() => send(true)}>Отмечу: отправлено вручную</button>
          </div>
          {!ws.mail_enabled && <p className="muted lw-sub">Почта пока не подключена: скачайте Excel или PDF, отправьте своей почтой и отметьте отправку. После подключения почты кнопка «Отправить из системы» заработает.</p>}
        </div>}
      </div>
      {ws.requests.length > 0 && <table className="lw-table"><thead><tr><th>Запрос</th><th>Когда</th><th>Кому</th><th>Ответ до</th><th>Кто</th></tr></thead>
        <tbody>{[...ws.requests].reverse().map((r) => <tr key={r.id}><td>{r.no}{r.files?.manual && <span className="muted"> · вручную</span>}</td><td>{fmtDate(r.at)}</td>
          <td>{r.sent_to.join(", ")}</td><td>{fmtDate(r.answer_by)}</td><td>{r.by_name}</td></tr>)}</tbody></table>}

      <h3 className="lw-h"><span className="pf-num">3</span> Ответы логистических компаний</h3>
      {ws.quotes.length > 0 && <table className="lw-table"><thead><tr><th>Компания</th><th>Услуга</th><th className="num">Стоимость</th><th className="num">Срок, дн.</th><th>Действует до</th><th>Условия</th><th>Файл</th><th /></tr></thead>
        <tbody>{ws.quotes.map((q) => (
          <tr key={q.id} className={best?.id === q.id ? "lw-best" : ""}><td><b>{q.carrier}</b>{best?.id === q.id && <span className="badge ok" style={{ marginLeft: 6 }}>дешевле всех</span>}<div className="muted" style={{ fontSize: 11 }}>{fmtDate(q.at)} · {q.by_name}</div></td>
            <td>{SCOPES[q.scope] || q.scope}</td><td className="num">{fmtRub(q.amount)} {q.currency}</td><td className="num">{q.transit_days ?? "—"}</td><td>{q.valid_until ? fmtDate(q.valid_until) : "—"}</td>
            <td className="muted" style={{ fontSize: 12 }}>{q.comment}</td><td>{q.storage_path ? <a href={`/api/files/${q.storage_path}`}>{q.file_name}</a> : "—"}</td>
            <td style={{ whiteSpace: "nowrap" }}>{edit && onApplyQuote && <button className="btn ghost" title="подставить в форму новой партии" onClick={() => onApplyQuote(q)}>в партию</button>}
              {edit && <button className="btn ghost" onClick={() => delQuote(q)}>×</button>}</td></tr>))}</tbody></table>}
      {edit && (
        <div className="lw-quote-form">
          <input placeholder="компания" list="lw-carrier-names" value={qf.carrier} onChange={(e) => setQf({ ...qf, carrier: e.target.value })} />
          <datalist id="lw-carrier-names">{ws.carriers.map((c) => <option key={c.name} value={c.name} />)}</datalist>
          <select value={qf.scope} onChange={(e) => setQf({ ...qf, scope: e.target.value })}>{Object.entries(SCOPES).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <input placeholder="стоимость" inputMode="decimal" value={qf.amount} onChange={(e) => setQf({ ...qf, amount: e.target.value })} />
          <select value={qf.currency} onChange={(e) => setQf({ ...qf, currency: e.target.value })}>{CURS.map((c) => <option key={c}>{c}</option>)}</select>
          <input placeholder="срок, дн." type="number" value={qf.transit_days} onChange={(e) => setQf({ ...qf, transit_days: e.target.value })} />
          <input type="date" title="действует до" value={qf.valid_until} onChange={(e) => setQf({ ...qf, valid_until: e.target.value })} />
          <input placeholder="условия / что не входит" value={qf.comment} onChange={(e) => setQf({ ...qf, comment: e.target.value })} />
          <label className="btn ghost lw-file">{qfile ? qfile.name.slice(0, 18) : "📎 файл ответа"}<input type="file" hidden onChange={(e) => setQfile(e.target.files?.[0] || null)} /></label>
          <button className="btn secondary" disabled={!qf.carrier.trim()} onClick={addQuote}>Добавить ответ</button>
        </div>)}
      {ws.quotes.length === 0 && !edit && <p className="muted">Ответов пока нет.</p>}
    </div>);
}
